import type { Application, Request } from 'express';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import { PG_SCHEMA } from '../lib/schema';
import { currentUser, databricksGet, databricksList, handle, HttpError } from '../lib/http';

type Row = Record<string, unknown>;
export interface Deps {
  query(text: string, params?: unknown[]): Promise<{ rows: Row[] }>;
  runJob(params: Record<string, string>): Promise<number>;
  getJobRun(runId: number): Promise<{ lifeCycle?: string; result?: string; message?: string } | null>;
  cancelJobRun(runId: number): Promise<void>;
}

const S = PG_SCHEMA;
// Resolved at startup from the engine job's `catalog` parameter (see server.ts).
const catalog = () => process.env.REZOLAKE_CATALOG ?? '';
const RUN_MODES = ['INCREMENTAL', 'FULL', 'LIMITED', 'COMMAND'] as const;
const COMMAND_TYPES = [
  'ACCEPT_MATCH', 'REJECT_MATCH', 'MERGE_ENTITIES', 'SPLIT_ENTITY', 'ISOLATE_RECORD', 'REVOKE_CONSTRAINT',
  'PIN_GOLDEN_RECORD', 'OVERRIDE_ATTRIBUTE', 'EDIT_RECORD', 'REMATCH',
] as const;

const qs = (req: Request, k: string): string => (typeof req.query[k] === 'string' ? req.query[k] : '');

const slugify = (s: string) =>
  s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48) || 'entity';

const CreateEntityType = z.object({
  name: z.string().min(1).max(100),
  entity_kind: z.enum(['people', 'companies', 'products', 'other']).default('other'),
  description: z.string().max(1000).optional(),
});

export function registerApi(app: Application, deps: Deps) {
  const q = (text: string, params?: unknown[]) => deps.query(text, params);

  app.get('/api/config', handle((req) => Promise.resolve({ catalog: catalog(), user: currentUser(req) })));

  // ── entity types ────────────────────────────────────────────────────
  app.get('/api/entity-types', handle(async () => {
    const { rows } = await q(`
      SELECT t.*, (SELECT row_to_json(r) FROM (
                    SELECT run_id, mode, status, stage, created_at, completed_at FROM ${S}.run_request
                    WHERE entity_type_id = t.id ORDER BY created_at DESC LIMIT 1) r) AS last_run,
             (SELECT count(*) FROM ${S}.resolution_command c
               WHERE c.entity_type_id = t.id AND c.status IN ('PENDING','RUNNING'))::int AS pending_commands
      FROM ${S}.entity_type t ORDER BY t.created_at`);
    return rows;
  }));

  app.post('/api/entity-types', handle(async (req, res) => {
    const body = CreateEntityType.parse(req.body);
    const slug = slugify(body.name);
    const { rows } = await q(
      `INSERT INTO ${S}.entity_type (id, name, slug, entity_kind, description, created_by)
       VALUES ($1, $2, $1, $3, $4, $5) ON CONFLICT DO NOTHING RETURNING *`,
      [slug, body.name, body.entity_kind, body.description ?? '', currentUser(req)],
    );
    if (!rows.length) throw new HttpError(409, `an entity type named like "${body.name}" already exists`);
    await q(`INSERT INTO ${S}.entity_definition_draft (entity_type_id, content, updated_by) VALUES ($1, $2, $3)`,
      [slug, { attributes: [], sources: [] }, currentUser(req)]);
    await q(`INSERT INTO ${S}.strategy_draft (entity_type_id, content, updated_by) VALUES ($1, '{}'::jsonb, $2)`,
      [slug, currentUser(req)]);
    res.status(201);
    return rows[0];
  }));

  app.get('/api/entity-types/:id', handle(async (req) => {
    const { rows } = await q(
      `SELECT t.*, d.content AS definition, d.updated_at AS definition_updated_at,
              s.content AS strategy,
              (SELECT row_to_json(r) FROM (
                 SELECT run_id, mode, status, stage, created_at, completed_at FROM ${S}.run_request
                 WHERE entity_type_id = t.id ORDER BY created_at DESC LIMIT 1) r) AS last_run
       FROM ${S}.entity_type t
       LEFT JOIN ${S}.entity_definition_draft d ON d.entity_type_id = t.id
       LEFT JOIN ${S}.strategy_draft s ON s.entity_type_id = t.id
       WHERE t.id = $1`, [req.params.id]);
    if (!rows.length) throw new HttpError(404, 'entity type not found');
    return rows[0];
  }));

  app.patch('/api/entity-types/:id', handle(async (req) => {
    const body = CreateEntityType.partial().parse(req.body);
    const { rows } = await q(
      `UPDATE ${S}.entity_type SET name = COALESCE($2, name), entity_kind = COALESCE($3, entity_kind),
              description = COALESCE($4, description), updated_at = now() WHERE id = $1 RETURNING *`,
      [req.params.id, body.name ?? null, body.entity_kind ?? null, body.description ?? null]);
    if (!rows.length) throw new HttpError(404, 'entity type not found');
    return rows[0];
  }));

  app.delete('/api/entity-types/:id', handle(async (req) => {
    await q(`DELETE FROM ${S}.entity_type WHERE id = $1`, [req.params.id]);
    return { ok: true };
  }));

  app.put('/api/entity-types/:id/definition', handle(async (req) => {
    const content = z.object({ attributes: z.array(z.any()), sources: z.array(z.any()) }).passthrough().parse(req.body);
    await q(
      `INSERT INTO ${S}.entity_definition_draft (entity_type_id, content, updated_by, updated_at)
       VALUES ($1, $2, $3, now())
       ON CONFLICT (entity_type_id) DO UPDATE SET content = EXCLUDED.content, updated_by = EXCLUDED.updated_by,
         updated_at = now()`, [req.params.id, content, currentUser(req)]);
    await q(`UPDATE ${S}.entity_type SET updated_at = now() WHERE id = $1`, [req.params.id]);
    return { ok: true };
  }));

  app.put('/api/entity-types/:id/strategy', handle(async (req) => {
    const content = z.record(z.string(), z.any()).parse(req.body);
    await q(
      `INSERT INTO ${S}.strategy_draft (entity_type_id, content, updated_by, updated_at) VALUES ($1, $2, $3, now())
       ON CONFLICT (entity_type_id) DO UPDATE SET content = EXCLUDED.content, updated_by = EXCLUDED.updated_by,
         updated_at = now()`, [req.params.id, content, currentUser(req)]);
    return { ok: true };
  }));

  // ── runs ────────────────────────────────────────────────────────────
  async function startRun(req: Request, entityTypeId: string, mode: string, extra: Record<string, string> = {}) {
    const runId = randomUUID();
    const user = currentUser(req);
    await q(
      `INSERT INTO ${S}.run_request (run_id, entity_type_id, mode, status, params, requested_by, trigger_source)
       VALUES ($1, $2, $3, 'PENDING', $4, $5, 'manual')`, [runId, entityTypeId, mode, extra, user]);
    try {
      const jobRunId = await deps.runJob({
        mode, entity_type_id: entityTypeId, run_id: runId, requested_by: user, trigger_source: 'manual', ...extra,
      });
      await q(`UPDATE ${S}.run_request SET job_run_id = $2 WHERE run_id = $1`, [runId, String(jobRunId)]);
    } catch (err) {
      await q(`UPDATE ${S}.run_request SET status = 'FAILED', error = $2, completed_at = now() WHERE run_id = $1`,
        [runId, (err as Error).message]);
      throw err;
    }
    return { run_id: runId };
  }

  app.post('/api/entity-types/:id/publish', handle(async (req) => startRun(req, req.params.id as string, 'PUBLISH')));

  app.post('/api/entity-types/:id/runs', handle(async (req) => {
    const body = z.object({
      mode: z.enum(RUN_MODES).default('INCREMENTAL'),
      max_records: z.coerce.number().int().positive().max(1_000_000).optional(),
      rematch_all: z.boolean().optional(),
    }).parse(req.body ?? {});
    const extra: Record<string, string> = {};
    if (body.max_records) extra.max_records = String(body.max_records);
    if (body.rematch_all) extra.rematch_all = 'true';
    return startRun(req, req.params.id as string, body.mode, extra);
  }));

  app.get('/api/entity-types/:id/runs', handle(async (req) => {
    const { rows } = await q(
      `SELECT * FROM ${S}.run_request WHERE entity_type_id = $1 ORDER BY created_at DESC LIMIT 50`, [req.params.id]);
    return rows;
  }));

  // The engine job runs one at a time across all entity types: show the whole queue.
  app.get('/api/runs/active', handle(async () => {
    const { rows } = await q(
      `SELECT r.*, t.name AS entity_type_name FROM ${S}.run_request r
       LEFT JOIN ${S}.entity_type t ON t.id = r.entity_type_id
       WHERE r.status IN ('PENDING', 'RUNNING') AND r.created_at > now() - interval '1 day'
       ORDER BY r.created_at`);
    const runs = await Promise.all(rows.map((r) => reconcileRun(r)));
    return runs.filter((r) => ['PENDING', 'RUNNING'].includes(String(r.status)));
  }));

  app.get('/api/runs/:runId', handle(async (req) => {
    const { rows } = await q(`SELECT * FROM ${S}.run_request WHERE run_id = $1`, [req.params.runId]);
    if (!rows.length) throw new HttpError(404, 'run not found');
    return reconcileRun(rows[0]);
  }));

  app.post('/api/runs/:runId/cancel', handle(async (req) => {
    const { rows } = await q(`SELECT job_run_id FROM ${S}.run_request WHERE run_id = $1`, [req.params.runId]);
    const jr = rows[0]?.job_run_id;
    if (jr) await deps.cancelJobRun(Number(jr));
    await q(`UPDATE ${S}.run_request SET status = 'CANCELLED', completed_at = now()
             WHERE run_id = $1 AND status IN ('PENDING','RUNNING')`, [req.params.runId]);
    return { ok: true };
  }));

  /** If the engine died before reporting, reflect the job's terminal state. */
  async function reconcileRun(run: Row): Promise<Row> {
    if (!['PENDING', 'RUNNING'].includes(String(run.status)) || !run.job_run_id) return run;
    const jr = await deps.getJobRun(Number(run.job_run_id));
    if (jr && ['TERMINATED', 'INTERNAL_ERROR', 'SKIPPED'].includes(jr.lifeCycle ?? '') && jr.result !== 'SUCCESS') {
      const status = jr.result === 'CANCELED' ? 'CANCELLED' : 'FAILED';
      await q(`UPDATE ${S}.run_request SET status = $2, error = COALESCE(error, $3), completed_at = now()
               WHERE run_id = $1 AND status IN ('PENDING','RUNNING')`, [run.run_id, status, jr.message ?? jr.result]);
      await q(`UPDATE ${S}.resolution_command SET status = 'FAILED', error = $2, applied_at = now()
               WHERE run_id = $1 AND status = 'RUNNING'`, [run.run_id, `run ${status.toLowerCase()}`]);
      return { ...run, status, error: run.error ?? jr.message ?? jr.result };
    }
    return { ...run, job_state: jr?.lifeCycle };
  }

  // ── commands (stewardship) ──────────────────────────────────────────
  app.post('/api/entity-types/:id/commands', handle(async (req, res) => {
    const body = z.object({ command_type: z.enum(COMMAND_TYPES), payload: z.record(z.string(), z.any()) })
      .parse(req.body);
    const et = req.params.id as string;
    const id = randomUUID();
    await q(
      `INSERT INTO ${S}.resolution_command (id, entity_type_id, command_type, payload, requested_by)
       VALUES ($1, $2, $3, $4, $5)`, [id, et, body.command_type, body.payload, currentUser(req)]);
    // Coalesce bursts: a queued (not yet started) run will pick this command up.
    const { rows } = await q(
      `SELECT run_id FROM ${S}.run_request WHERE entity_type_id = $1 AND status = 'PENDING'
         AND created_at > now() - interval '30 minutes' LIMIT 1`, [et]);
    const run = rows.length ? { run_id: rows[0].run_id as string } : await startRun(req, et, 'COMMAND');
    res.status(202);
    return { command_id: id, run_id: run.run_id };
  }));

  app.get('/api/entity-types/:id/commands', handle(async (req) => {
    const { rows } = await q(
      `SELECT * FROM ${S}.resolution_command WHERE entity_type_id = $1 ORDER BY requested_at DESC LIMIT 100`,
      [req.params.id]);
    return rows;
  }));

  // ── review notes (UNSURE / flag / comment stay operational) ─────────
  app.post('/api/entity-types/:id/review-notes', handle(async (req) => {
    const body = z.object({
      pair_id: z.string().min(1), verdict: z.enum(['UNSURE', 'NOTE']).default('NOTE'),
      flagged: z.boolean().default(false), note: z.string().max(4000).optional(),
    }).parse(req.body);
    const { rows } = await q(
      `INSERT INTO ${S}.review_note (id, entity_type_id, pair_id, verdict, flagged, note, author)
       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *`,
      [randomUUID(), req.params.id, body.pair_id, body.verdict, body.flagged, body.note ?? '', currentUser(req)]);
    return rows[0];
  }));

  app.get('/api/entity-types/:id/review-notes', handle(async (req) => {
    const pair = qs(req, 'pair_id') || undefined;
    const { rows } = await q(
      `SELECT * FROM ${S}.review_note WHERE entity_type_id = $1 AND ($2::text IS NULL OR pair_id = $2)
       ORDER BY created_at DESC LIMIT 500`, [req.params.id, pair ?? null]);
    return rows;
  }));

  // ── Unity Catalog browsing (as the signed-in user) ──────────────────
  app.get('/api/uc/catalogs', handle(async (req) => {
    // UC list APIs paginate (a page can be smaller than max_results), so follow next_page_token.
    const r = await databricksList<{ name: string }>(req, '/api/2.1/unity-catalog/catalogs?max_results=100', 'catalogs');
    return r.map((c) => c.name).sort();
  }));
  app.get('/api/uc/schemas', handle(async (req) => {
    const cat = encodeURIComponent(qs(req, 'catalog'));
    const r = await databricksList<{ name: string }>(
      req, `/api/2.1/unity-catalog/schemas?catalog_name=${cat}&max_results=100`, 'schemas');
    return r.map((s) => s.name).sort();
  }));
  app.get('/api/uc/tables', handle(async (req) => {
    const cat = encodeURIComponent(qs(req, 'catalog'));
    const sch = encodeURIComponent(qs(req, 'schema'));
    const r = await databricksList<{ name: string; table_type: string }>(
      req, `/api/2.1/unity-catalog/tables?catalog_name=${cat}&schema_name=${sch}&max_results=100&omit_columns=true`, 'tables');
    return r.map((t) => ({ name: t.name, table_type: t.table_type })).sort((a, b) => a.name.localeCompare(b.name));
  }));
  app.get('/api/uc/table', handle(async (req) => {
    const name = encodeURIComponent(qs(req, 'name'));
    const t = await databricksGet<{
      full_name: string; table_type: string; columns?: { name: string; type_text: string }[];
      properties?: Record<string, string>;
    }>(req, `/api/2.1/unity-catalog/tables/${name}`);
    return {
      full_name: t.full_name,
      table_type: t.table_type,
      cdf_enabled: (t.properties?.['delta.enableChangeDataFeed'] ?? 'false') === 'true',
      columns: (t.columns ?? []).map((c) => ({ name: c.name, type: c.type_text })),
    };
  }));
}
