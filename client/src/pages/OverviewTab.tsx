import { useMemo } from 'react';
import { Link, useOutletContext } from 'react-router';
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle, useAnalyticsQuery } from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { useConfig } from '../lib/context';
import { fmtTime } from '../lib/api';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../components/states';
import type { EntityTypeOutlet } from './EntityTypeLayout';

export function OverviewTab() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { catalog } = useConfig();
  const params = useMemo(
    () => ({ catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id) }),
    [catalog, entityType.id],
  );
  const { data, loading, error } = useAnalyticsQuery('entity_type_stats', params);
  const s = data?.[0];

  if (!entityType.published_version) {
    return (
      <EmptyState title="Not published yet">
        Define attributes and sources, then publish. <Link className="underline" to="definition">Open definition →</Link>
      </EmptyState>
    );
  }

  const tiles: { label: string; value: number | undefined; hint: string; to?: string }[] = [
    { label: 'Source records', value: s?.records, hint: `across ${s?.sources ?? 0} sources` },
    { label: 'Master entities', value: s?.entities, hint: `${s?.multi_record_entities ?? 0} with 2+ records`, to: 'entities' },
    {
      label: 'Duplicates resolved',
      value: s ? Number(s.records) - Number(s.entities) : undefined,
      hint: 'records folded into another entity',
    },
    { label: 'Open reviews', value: s?.open_reviews, hint: 'pairs awaiting a steward decision', to: 'review' },
    { label: 'Human constraints', value: s?.active_constraints, hint: 'active must/cannot-link and isolate rules' },
  ];

  return (
    <div className="space-y-4">
      {error && <ErrorNote error={error} title="Could not load stats" />}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-5">
        {tiles.map((t) => (
          <Card key={t.label}>
            <CardHeader className="pb-1">
              <CardDescription>{t.label}</CardDescription>
              <CardTitle className="text-3xl tabular-nums">
                {loading ? '…' : t.value === undefined ? '—' : Number(t.value).toLocaleString()}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">
              {t.to ? <Link className="underline" to={t.to}>{t.hint}</Link> : t.hint}
            </CardContent>
          </Card>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Source: {catalog}.rezolake2_* Delta tables via the Lakehouse//RT warehouse · as of the last completed run
        {entityType.last_run ? ` (${fmtTime(entityType.last_run.completed_at ?? entityType.last_run.created_at)})` : ''}.
      </p>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Last run</CardTitle>
            <CardDescription>Runs execute on the serverless rezolake-resolve job.</CardDescription>
          </div>
          <Button asChild variant="outline">
            <Link to="runs">Runs →</Link>
          </Button>
        </CardHeader>
        <CardContent className="text-sm">
          {loading && <Loading rows={1} />}
          {entityType.last_run ? (
            <span>
              {entityType.last_run.mode} <StatusPill value={entityType.last_run.status} /> started{' '}
              {fmtTime(entityType.last_run.created_at)}
            </span>
          ) : (
            'No runs yet.'
          )}
        </CardContent>
      </Card>
    </div>
  );
}
