// One entity: header with actions, then tabs for the side-by-side comparison, why the records are
// together (graph + evidence + split suggestion), raw payloads and lineage. Used by the explorer's
// detail panel and by the full entity page.
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { ExternalLink, RefreshCw, Scissors, X } from 'lucide-react';
import { asList, fmtTime, isTrue, parseJson, sendCommand, shortId, type Command, type EntityType } from '../../lib/api';
import { useApi } from '../../lib/hooks';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../states';
import { CompareTable, type GoldenRow, type MemberRow } from './CompareTable';
import { MergeDialog } from './MergeDialog';
import { WhyTogether, type EdgeRow } from './WhyTogether';
import type { EntityParams } from './types';

const EMPTY: never[] = [];

export function EntityView({ entityType, catalog, entityId, variant = 'page', onClose }: {
  entityType: EntityType;
  catalog: string;
  entityId: string;
  variant?: 'page' | 'panel';
  onClose?: () => void;
}) {
  const base = useMemo<EntityParams>(
    () => ({ catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id), entity_id: sql.string(entityId) }),
    [catalog, entityType.id, entityId],
  );
  const detail = useAnalyticsQuery('entity_detail', base);
  const e = detail.data?.[0];
  // Members, attributes, edges and events are keyed by the alias-resolved id.
  const params = useMemo<EntityParams>(
    () => ({ ...base, entity_id: sql.string(e?.entity_id ?? entityId) }),
    [base, e?.entity_id, entityId],
  );
  const ready = !!e;
  const members = useAnalyticsQuery('entity_members', params, { autoStart: ready });
  const golden = useAnalyticsQuery('entity_attributes', params, { autoStart: ready });
  const edges = useAnalyticsQuery('entity_edges', params, { autoStart: ready });
  const events = useAnalyticsQuery('entity_events', params, { autoStart: ready });

  const [notice, setNotice] = useState<string>();
  const [err, setErr] = useState<string>();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const commands = useApi<Command[]>(`/api/entity-types/${entityType.id}/commands`, {
    pollMs: 15000,
    pollWhile: (d) => d.some((c) => c.status === 'PENDING' || c.status === 'RUNNING'),
  });

  async function command(type: string, payload: Record<string, unknown>, label: string) {
    setErr(undefined);
    try {
      await sendCommand(entityType.id, type, payload);
      setNotice(`${label} requested — the resolution engine applies it on its next run; this entity refreshes after that.`);
      commands.restart();
    } catch (x) {
      setErr((x as Error).message);
    }
  }
  const toggle = (id: string) => {
    const n = new Set(selected);
    if (n.has(id)) n.delete(id); else n.add(id);
    setSelected(n);
  };

  if (detail.loading) return <Loading rows={6} />;
  if (detail.error) return <ErrorNote error={detail.error} title="Could not load entity" />;
  if (!e) return <EmptyState title="Entity not found">It may have been retired by a split or merge.</EmptyState>;

  const attrs = entityType.definition?.attributes ?? [];
  const memberRows = (members.data ?? EMPTY) as MemberRow[];
  const edgeRows = (edges.data ?? EMPTY) as EdgeRow[];
  // A REVIEW pair a steward already decided (any constraint on the pair) is no longer open — same rule as the queue.
  const decided = new Set(edgeRows.filter((r) => r.kind === 'constraint' && r.right_record_id)
    .flatMap((r) => [`${r.left_record_id}|${r.right_record_id}`, `${r.right_record_id}|${r.left_record_id}`]));
  const openReviews = edgeRows.filter((r) => r.kind === 'decision' && isTrue(r.is_current) && r.label === 'REVIEW'
    && !decided.has(`${r.left_record_id}|${r.right_record_id}`)).length;
  // Steward commands not yet applied that touch this entity or its records.
  const memberIds = new Set(memberRows.map((m) => m.record_id));
  const touches = (c: Command) => {
    const p = c.payload as { entity_id?: string; entity_ids?: string[]; record_id?: string; record_ids?: string[]; left_record_id?: string; right_record_id?: string };
    const ents = [p.entity_id, ...(p.entity_ids ?? [])];
    const recs = [p.record_id, p.left_record_id, p.right_record_id, ...(p.record_ids ?? [])];
    return ents.includes(e.entity_id) || recs.some((r) => r && memberIds.has(r));
  };
  const waiting = (commands.data ?? []).filter((c) => (c.status === 'PENDING' || c.status === 'RUNNING') && touches(c));
  const sources = [...new Set(memberRows.map((m) => m.source_id))].sort();
  const fullPage = `/t/${entityType.id}/entities/${e.entity_id}`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className={`${variant === 'panel' ? 'text-lg' : 'text-xl'} font-semibold break-words line-clamp-2`} title={e.display_name}>{e.display_name}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Badge variant="outline" className="font-mono">{shortId(e.entity_id)}</Badge>
            <span>{e.member_count} records · {e.source_count} sources · updated {fmtTime(e.updated_at)}</span>
            {sources.map((s) => <Badge key={s} variant="secondary">{s}</Badge>)}
            {openReviews > 0 && (
              <Link to={`/t/${entityType.id}/review`}><StatusPill value="REVIEW" /> <span className="underline">{openReviews} open</span></Link>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <MergeDialog catalog={catalog} entityTypeId={entityType.id} entityId={e.entity_id} members={memberRows} onCommand={command} />
          <Button variant="outline" size="sm" onClick={() => command('REMATCH', { entity_id: e.entity_id }, 'Re-match')}>
            <RefreshCw className="h-4 w-4 mr-1" />Re-match
          </Button>
          {variant === 'panel' && (
            <>
              <Button asChild variant="ghost" size="sm"><Link to={fullPage}><ExternalLink className="h-4 w-4 mr-1" />Full page</Link></Button>
              {onClose && <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}><X className="h-4 w-4" /></Button>}
            </>
          )}
        </div>
      </div>
      {isTrue(e.followed_alias) && (
        <Alert><AlertDescription>This id was merged; showing the surviving entity {shortId(e.entity_id)}.</AlertDescription></Alert>
      )}
      {waiting.length > 0 && (
        <Alert>
          <AlertDescription>
            {waiting.length} steward change{waiting.length === 1 ? '' : 's'} waiting for the engine
            ({waiting.map((c) => c.command_type.replace(/_/g, ' ').toLowerCase()).join(', ')}). Engine runs go one at a time,
            so a change waits for the run in front of it (a long run steps aside between scoring batches) — see the engine queue in the{' '}
            <Link className="underline" to={`/t/${entityType.id}/runs`}>Runs tab</Link>.
          </AlertDescription>
        </Alert>
      )}
      {notice && waiting.length === 0 && <Alert><AlertDescription>{notice}</AlertDescription></Alert>}
      {err && <ErrorNote error={err} title="Command rejected" />}

      <Tabs defaultValue="compare">
        <TabsList>
          <TabsTrigger value="compare">Compare records</TabsTrigger>
          <TabsTrigger value="why">Why together?</TabsTrigger>
          <TabsTrigger value="raw">Raw payloads</TabsTrigger>
          <TabsTrigger value="lineage">Lineage</TabsTrigger>
        </TabsList>

        <TabsContent value="compare" className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs text-muted-foreground">Click a record value to correct it; use the pencil to override a golden value.</p>
            <Button size="sm" variant="outline" className="ml-auto"
              disabled={selected.size === 0 || selected.size >= memberRows.length}
              onClick={async () => { await command('SPLIT_ENTITY', { entity_id: e.entity_id, record_ids: [...selected], reason: 'steward split' }, 'Split'); setSelected(new Set()); }}>
              <Scissors className="h-4 w-4 mr-1" />Split {selected.size || ''} selected into a new entity
            </Button>
          </div>
          {(members.loading || golden.loading) && <Loading rows={4} />}
          {members.error && <ErrorNote error={members.error} title="Could not load records" />}
          {golden.error && <ErrorNote error={golden.error} title="Could not load golden record" />}
          {members.data && golden.data && (
            <CompareTable entityId={e.entity_id} attrs={attrs} golden={golden.data as GoldenRow[]} members={memberRows}
              selected={selected} onToggle={toggle} onCommand={command} />
          )}
        </TabsContent>

        <TabsContent value="why">
          {(edges.loading || members.loading) && <Loading rows={4} />}
          {edges.error && <ErrorNote error={edges.error} title="Could not load decisions" />}
          {edges.data && members.data && (
            <WhyTogether entityId={e.entity_id} entityTypeId={entityType.id} catalog={catalog} attrs={attrs}
              golden={(golden.data ?? EMPTY) as GoldenRow[]} members={memberRows} edges={edgeRows} onCommand={command} />
          )}
        </TabsContent>

        <TabsContent value="raw" className="space-y-2">
          {members.loading && <Loading rows={3} />}
          {(members.data ?? EMPTY).map((m) => {
            const ext = parseJson<Record<string, unknown>>(m.additional_attributes, {});
            return (
              <div key={m.record_id} className="text-xs">
                <div className="font-medium">{m.source_id} · {m.source_key}{isTrue(m.is_anchor) ? ' · anchor' : ''}</div>
                <pre className="bg-muted p-2 rounded overflow-auto max-h-64">{JSON.stringify(parseJson(m.payload, {}), null, 2)}</pre>
                {Object.keys(ext).length > 0 && <div className="text-muted-foreground">extensions: {JSON.stringify(ext)}</div>}
              </div>
            );
          })}
        </TabsContent>

        <TabsContent value="lineage">
          <p className="text-xs text-muted-foreground mb-2">Created {fmtTime(e.created_at)} · last changed {fmtTime(e.updated_at)}</p>
          {events.loading && <Loading rows={2} />}
          {events.error && <ErrorNote error={events.error} />}
          {events.data && events.data.length === 0 && <p className="text-sm text-muted-foreground">No lineage events.</p>}
          <ul className="text-sm space-y-1">
            {(events.data ?? []).map((ev) => (
              <li key={`${ev.event_type}-${ev.created_at}-${asList(ev.target_entity_ids).join(',')}`}>
                <StatusPill value={ev.event_type} /> {fmtTime(ev.created_at)} · {asList(ev.source_entity_ids).map(shortId).join(', ') || '∅'} →{' '}
                {asList(ev.target_entity_ids).map(shortId).join(', ') || '∅'} {ev.command_id && <span className="text-muted-foreground">(steward command)</span>}
              </li>
            ))}
          </ul>
        </TabsContent>
      </Tabs>
    </div>
  );
}
