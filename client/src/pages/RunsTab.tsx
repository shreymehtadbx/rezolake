import { useEffect, useMemo, useState } from 'react';
import { useOutletContext, useSearchParams } from 'react-router';
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useAnalyticsQuery,
  Switch,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { api, fmtTime, parseJson, shortId, type RunRequest } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useConfig } from '../lib/context';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../components/states';
import type { EntityTypeOutlet } from './EntityTypeLayout';
import { AutomationCard } from '../components/AutomationCard';

const ACTIVE = ['PENDING', 'RUNNING'];
const STAGES = ['commands', 'ingest', 'canonicalize', 'represent', 'index_sync', 'block', 'match', 'decide', 'cluster', 'survive'];

export function RunsTab() {
  const { entityType, refresh: refreshType } = useOutletContext<EntityTypeOutlet>();
  const [search] = useSearchParams();
  const highlight = search.get('run');
  const [limit, setLimit] = useState(25);
  const [rematchAll, setRematchAll] = useState(false);
  const [err, setErr] = useState<string>();
  const queue = useApi<(RunRequest & { entity_type_name?: string | null })[]>('/api/runs/active', {
    pollMs: 10000,
    pollWhile: (d) => d.length > 0,
  });
  const runs = useApi<RunRequest[]>(`/api/entity-types/${entityType.id}/runs`, {
    pollMs: 5000,
    pollWhile: (d) => d.some((r) => ACTIVE.includes(r.status)),
  });

  async function start(mode: string, max?: number) {
    setErr(undefined);
    try {
      await api(`/api/entity-types/${entityType.id}/runs`, { method: 'POST', json: { mode, max_records: max, rematch_all: rematchAll || undefined } });
      setRematchAll(false);
      runs.restart();
      queue.restart();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  // Refresh the header badges when a run finishes.
  const anyActive = runs.data?.some((r) => ACTIVE.includes(r.status));
  useEffect(() => {
    if (anyActive === false) void refreshType();
  }, [anyActive]); // eslint-disable-line react-hooks/exhaustive-deps

  const published = !!entityType.published_version;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Run the resolution engine</CardTitle>
          <CardDescription>
            Incremental reads only source changes (CDF); Full snapshots every source, re-embeds and rebuilds entities;
            Limited caps the work set. Every mode re-matches only records that changed or were never matched, so
            settled matches keep the decisions (and strategy version) that made them. Runs queue one at a time.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button disabled={!published} onClick={() => start('INCREMENTAL')}>Run incremental</Button>
          <Button disabled={!published} variant="outline" onClick={() => start('FULL')}>Run full</Button>
          <Input className="w-28" type="number" min={1} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          <Button disabled={!published || limit < 1} variant="outline" onClick={() => start('LIMITED', limit)}>
            Run limited
          </Button>
          <label className="ml-2 inline-flex items-center gap-2 text-sm">
            <Switch checked={rematchAll} onCheckedChange={setRematchAll} />
            Re-match all records with the current strategy
          </label>
          {rematchAll && (
            <span className="text-xs text-warning-foreground bg-warning/15 rounded px-2 py-1">
              Replaces settled matches with new decisions; history is kept.
            </span>
          )}
          {!published && <span className="text-sm text-muted-foreground">Publish a definition first.</span>}
        </CardContent>
      </Card>
      {err && <ErrorNote error={err} title="Could not start run" />}
      {queue.data && queue.data.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Engine queue · all entity types</CardTitle>
            <CardDescription>
              The engine runs one job at a time, so runs (including steward changes) wait for the one in front. A long
              run steps aside between scoring batches when a steward change is waiting, and queues a continuation.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Entity type</TableHead>
                  <TableHead>Run</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Queued</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {queue.data.map((r, i) => (
                  <TableRow key={r.run_id} data-state={r.entity_type_id === entityType.id ? 'selected' : undefined}>
                    <TableCell className="tabular-nums">{i + 1}</TableCell>
                    <TableCell>
                      {r.entity_type_name ?? r.entity_type_id}
                      {r.entity_type_id === entityType.id && <span className="text-xs text-muted-foreground"> · this type</span>}
                    </TableCell>
                    <TableCell>
                      {r.mode}
                      {r.trigger_source === 'continuation' && <span className="text-xs text-muted-foreground"> · continuation</span>}
                      {r.params?.rematch_all === 'true' && <span className="text-xs text-muted-foreground"> · re-match all</span>}
                    </TableCell>
                    <TableCell><StatusPill value={r.status} /> {r.stage && <span className="text-xs text-muted-foreground">{r.stage}</span>}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{fmtTime(r.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {runs.loading && !runs.data && <Loading rows={4} />}
      {runs.error && <ErrorNote error={runs.error} title="Could not load runs" />}
      {runs.data && runs.data.length === 0 && <EmptyState title="No runs yet" />}
      {runs.data && runs.data.length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Stage / result</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Completed</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data.map((r) => (
                  <TableRow key={r.run_id} className={r.run_id === highlight ? 'bg-muted/60' : undefined}>
                    <TableCell className="font-mono text-xs">{shortId(r.run_id)}</TableCell>
                    <TableCell>
                      {r.mode}{typeof r.params?.max_records === 'string' ? ` (${r.params.max_records})` : ''}
                      {r.params?.rematch_all === 'true' && <span className="text-xs text-muted-foreground"> · re-match all</span>}
                    </TableCell>
                    <TableCell><StatusPill value={r.status} /></TableCell>
                    <TableCell className="text-xs max-w-md whitespace-normal break-words">
                      {ACTIVE.includes(r.status) ? <StageBar stage={r.stage} status={r.status} /> : <RunSummary run={r} />}
                    </TableCell>
                    <TableCell className="text-xs align-top">{fmtTime(r.created_at)}<br /><span className="text-muted-foreground">{r.requested_by}</span></TableCell>
                    <TableCell className="text-xs">{fmtTime(r.completed_at)}</TableCell>
                    <TableCell>
                      {ACTIVE.includes(r.status) && (
                        <Button size="sm" variant="ghost" onClick={async () => {
                          await api(`/api/runs/${r.run_id}/cancel`, { method: 'POST' });
                          runs.restart();
                        }}>Cancel</Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      <AutomationCard entityTypeId={entityType.id} published={published} />
      <RunManifests />
    </div>
  );
}

function StageBar({ stage, status }: { stage?: string | null; status: string }) {
  if (status === 'PENDING') return <span className="text-muted-foreground">queued — waiting for serverless compute…</span>;
  const idx = STAGES.indexOf(stage ?? '');
  return (
    <div className="flex flex-wrap gap-1">
      {STAGES.map((s, i) => (
        <span key={s} className={`px-1.5 py-0.5 rounded ${i < idx ? 'bg-muted text-muted-foreground' : i === idx ? 'bg-primary text-primary-foreground' : 'text-muted-foreground/60'}`}>
          {s}
        </span>
      ))}
      {stage === 'publish' && <span className="px-1.5 py-0.5 rounded bg-primary text-primary-foreground">publish</span>}
    </div>
  );
}

function RunSummary({ run }: { run: RunRequest }) {
  if (run.error) return <span className="text-destructive break-all line-clamp-3">{run.error}</span>;
  const m = run.metrics ?? {};
  const bits: string[] = [];
  const add = (k: string, label: string) => {
    const v = m[k];
    if (typeof v === 'number' || typeof v === 'string') bits.push(`${label} ${v}`);
  };
  add('entity_definition_version', 'definition v');
  add('source_records_inserted', '+');
  add('source_records_updated', '~');
  add('source_records_deleted', '−');
  add('work_set', 'work set');
  add('candidate_pairs', 'pairs');
  add('pairs_scored', 'scored');
  add('scores_reused', 'reused');
  add('decisions_link', 'link');
  add('decisions_review', 'review');
  add('entities', 'entities');
  add('commands_applied', 'commands');
  return <span className="text-muted-foreground">{bits.join(' · ') || '—'}</span>;
}

function RunManifests() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { catalog } = useConfig();
  const params = useMemo(() => ({ catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id) }), [catalog, entityType.id]);
  const { data, loading, error } = useAnalyticsQuery('run_history', params);
  const [open, setOpen] = useState<string>();
  if (loading) return <Loading rows={2} />;
  if (error) return <ErrorNote error={error} title="Could not load run manifests" />;
  if (!data?.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run manifests (Delta, immutable)</CardTitle>
        <CardDescription>What each run froze: definition/strategy versions, components and source version ranges.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {data.map((r) => {
          const man = parseJson<Record<string, unknown>>(r.manifest, {});
          const sources = parseJson<Record<string, unknown>[]>(r.sources, []);
          return (
            <div key={r.run_id} className="border rounded-md p-3 text-sm">
              <button className="w-full text-left flex flex-wrap gap-3 items-center" onClick={() => setOpen(open === r.run_id ? undefined : r.run_id)}>
                <span className="font-mono text-xs">{shortId(r.run_id)}</span>
                <span>{r.mode}</span>
                <StatusPill value={r.status} />
                <span className="text-muted-foreground">
                  def v{String(man.entity_definition_version)} · strategy v{String(man.strategy_version)} · {String(man.blocker)} →{' '}
                  {(man.matchers as string[] | undefined)?.join(', ')} → {String(man.decision_policy)}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">{fmtTime(r.started_at)}</span>
              </button>
              {open === r.run_id && (
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <pre className="text-xs bg-muted p-2 rounded overflow-auto">{JSON.stringify(man, null, 2)}</pre>
                  <div className="space-y-2">
                    <pre className="text-xs bg-muted p-2 rounded overflow-auto">{JSON.stringify(sources, null, 2)}</pre>
                    <pre className="text-xs bg-muted p-2 rounded overflow-auto">{JSON.stringify(parseJson(r.metrics, {}), null, 2)}</pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
