// Automatic orchestration: a per-entity-type trigger job (table update or cron) whose only task
// runs the shared rezolake-resolve engine job in INCREMENTAL mode. The trigger job is a doorbell;
// all logic lives in the engine, and the engine job's queue (max 1 concurrent) coalesces bursts.
import type { Application } from 'express';
import { z } from 'zod';
import { createWorkspaceClient } from '@databricks/appkit';
import { PG_SCHEMA } from '../lib/schema';
import { currentUser, handle, HttpError } from '../lib/http';
import type { Deps } from './api';

const S = PG_SCHEMA;

const Policy = z.object({
  enabled: z.boolean(),
  trigger_mode: z.enum(['table_update', 'scheduled']),
  cron: z.string().max(200).optional().nullable(),
  timezone: z.string().max(100).default('UTC'),
  wait_after_last_change_seconds: z.number().int().min(60).max(86400).default(60),
  min_time_between_triggers_seconds: z.number().int().min(60).max(86400).default(300),
});

type Row = Record<string, unknown>;
let ws: ReturnType<typeof createWorkspaceClient> | undefined;
const client = () => (ws ??= createWorkspaceClient());

export function registerOrchestration(app: Application, deps: Deps) {
  const q = (text: string, params?: unknown[]) => deps.query(text, params);

  app.get('/api/entity-types/:id/orchestration', handle(async (req) => {
    const { rows } = await q(`SELECT * FROM ${S}.orchestration_policy WHERE entity_type_id = $1`, [req.params.id]);
    return rows[0] ?? { entity_type_id: req.params.id, enabled: false, trigger_mode: 'table_update', timezone: 'UTC',
      wait_after_last_change_seconds: 60, min_time_between_triggers_seconds: 300 };
  }));

  app.put('/api/entity-types/:id/orchestration', handle(async (req) => {
    const p = Policy.parse(req.body);
    const et = req.params.id as string;
    if (p.enabled && p.trigger_mode === 'scheduled' && !p.cron) throw new HttpError(400, 'a cron expression is required');
    await q(
      `INSERT INTO ${S}.orchestration_policy (entity_type_id, enabled, trigger_mode, cron, timezone,
         wait_after_last_change_seconds, min_time_between_triggers_seconds, updated_by, updated_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
       ON CONFLICT (entity_type_id) DO UPDATE SET enabled = EXCLUDED.enabled, trigger_mode = EXCLUDED.trigger_mode,
         cron = EXCLUDED.cron, timezone = EXCLUDED.timezone,
         wait_after_last_change_seconds = EXCLUDED.wait_after_last_change_seconds,
         min_time_between_triggers_seconds = EXCLUDED.min_time_between_triggers_seconds,
         updated_by = EXCLUDED.updated_by, updated_at = now()`,
      [et, p.enabled, p.trigger_mode, p.cron ?? null, p.timezone, p.wait_after_last_change_seconds,
        p.min_time_between_triggers_seconds, currentUser(req)]);
    return reconcile(et);
  }));

  async function reconcile(et: string): Promise<Row> {
    const { rows } = await q(
      `SELECT p.*, d.content AS definition FROM ${S}.orchestration_policy p
       LEFT JOIN ${S}.entity_definition_draft d ON d.entity_type_id = p.entity_type_id
       WHERE p.entity_type_id = $1`, [et]);
    const row = rows[0];
    if (!row) throw new HttpError(404, 'no policy');
    const { definition: _definition, ...pol } = row;
    const jobs = client().jobs;
    const existing = pol.databricks_job_id ? Number(pol.databricks_job_id) : undefined;
    try {
      if (!pol.enabled) {
        if (existing) await jobs.delete({ job_id: existing }).catch(() => undefined);
        await q(`UPDATE ${S}.orchestration_policy SET databricks_job_id = NULL, job_error = NULL, job_synced_at = now()
                 WHERE entity_type_id = $1`, [et]);
        return { ...pol, databricks_job_id: null, job_error: null };
      }
      const resolveJobId = Number(process.env.DATABRICKS_JOB_ID);
      if (!resolveJobId) throw new Error('DATABRICKS_JOB_ID (engine job) is not configured');
      const def = (_definition ?? {}) as { sources?: { table: string; active: boolean }[] };
      const tables = (def.sources ?? []).filter((s) => s.active).map((s) => s.table).slice(0, 10);
      const tableMode = pol.trigger_mode === 'table_update';
      if (tableMode && !tables.length) throw new Error('no active source tables to watch');
      const settings = {
        name: `rezolake-trigger-${et}`,
        description: `Rezolake auto-orchestration for entity type ${et} (managed by the app; edit in the app).`,
        max_concurrent_runs: 1,
        queue: { enabled: true },
        tasks: [{
          task_key: 'resolve',
          run_job_task: {
            job_id: resolveJobId,
            job_parameters: { mode: 'INCREMENTAL', entity_type_id: et, trigger_source: tableMode ? 'table_update' : 'scheduled',
              requested_by: 'orchestration' },
          },
        }],
        ...(tableMode
          ? { trigger: { pause_status: 'UNPAUSED' as const, table_update: {
              table_names: tables,
              condition: 'ANY_UPDATED' as const,
              wait_after_last_change_seconds: Number(pol.wait_after_last_change_seconds),
              min_time_between_triggers_seconds: Number(pol.min_time_between_triggers_seconds) } } }
          : { schedule: { quartz_cron_expression: String(pol.cron), timezone_id: String(pol.timezone), pause_status: 'UNPAUSED' as const } }),
      };
      let jobId = existing;
      if (existing) {
        try {
          await jobs.reset({ job_id: existing, new_settings: settings });
        } catch {
          jobId = undefined; // deleted out-of-band: recreate
        }
      }
      if (!jobId) jobId = (await jobs.create(settings)).job_id;
      await q(`UPDATE ${S}.orchestration_policy SET databricks_job_id = $2, job_error = NULL, job_synced_at = now()
               WHERE entity_type_id = $1`, [et, jobId]);
      return { ...pol, databricks_job_id: jobId, job_error: null, watched_tables: tables };
    } catch (err) {
      const msg = (err as Error).message;
      await q(`UPDATE ${S}.orchestration_policy SET job_error = $2, job_synced_at = now() WHERE entity_type_id = $1`, [et, msg]);
      return { ...pol, job_error: msg };
    }
  }
}
