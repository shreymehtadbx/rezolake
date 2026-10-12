// Entity explorer: summary strip, server-side filtered/sorted/paginated entity list, and the selected
// entity in a resizable side panel (full page on narrow screens). Filters and selection live in the URL.
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useOutletContext, useSearchParams } from 'react-router';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
  Input,
  Label,
  Popover,
  PopoverContent,
  PopoverTrigger,
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Toggle,
  ToggleGroup,
  ToggleGroupItem,
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { ChevronLeft, ChevronRight, KeyRound, Search } from 'lucide-react';
import { fmtTime, isTrue, parseJson, shortId, type EntityType } from '../lib/api';
import { useConfig } from '../lib/context';
import { useDebounced } from '../lib/hooks';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../components/states';
import { EntityPreview } from '../components/entity/EntityPreview';
import { EntityView } from '../components/entity/EntityView';
import type { EntityTypeOutlet } from './EntityTypeLayout';

const PAGE = 50;
const SORTS = { size: 'Most records', reviews: 'Most open reviews', updated: 'Recently changed', name: 'Name' } as const;

function useWide(query = '(min-width: 1024px)') {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setWide(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return wide;
}

export function EntitiesTab() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { catalog } = useConfig();
  const navigate = useNavigate();
  const wide = useWide();
  const [sp, setSp] = useSearchParams();
  const f = {
    q: sp.get('q') ?? '',
    source: sp.get('source') ?? '',
    size: (sp.get('size') ?? 'all') as 'all' | 'multi' | 'single',
    review: sp.get('review') === 'open' ? 'open' : 'all',
    sort: (sp.get('sort') ?? 'size') as keyof typeof SORTS,
    page: Math.max(0, Number(sp.get('page') ?? 0) || 0),
    e: sp.get('e') ?? '',
  };
  /** Update URL state; any filter change returns to the first page. */
  const update = (patch: Partial<Record<keyof typeof f, string | number>>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      if (v === '' || v === undefined || (k === 'page' && v === 0)) next.delete(k);
      else next.set(k, String(v));
    }
    if (!('page' in patch) && !('e' in patch)) next.delete('page');
    setSp(next, { replace: true });
  };
  const [text, setText] = useState(f.q);
  const q = useDebounced(text.trim(), 300);
  useEffect(() => { if (q !== f.q) update({ q }); }, [q]); // eslint-disable-line react-hooks/exhaustive-deps -- push the debounced text to the URL only when it changes

  const select = (entityId: string) => {
    if (wide) update({ e: entityId });
    else void navigate(entityId);
  };

  return (
    <div className="space-y-4">
      <SummaryStrip entityType={entityType} catalog={catalog}
        onFilter={(patch) => update(patch)} />
      <div className="flex flex-wrap items-end gap-2">
        <div className="relative min-w-64 flex-1">
          <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-8" placeholder="Search golden values, entity id, record id or source key" value={text}
            onChange={(e) => setText(e.target.value)} aria-label="Search entities" />
        </div>
        <Select value={f.source || 'all'} onValueChange={(v) => update({ source: v === 'all' ? '' : v })}>
          <SelectTrigger className="w-44" aria-label="Source"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            {(entityType.definition?.sources ?? []).map((s) => <SelectItem key={s.source_id} value={s.source_id}>{s.name || s.source_id}</SelectItem>)}
          </SelectContent>
        </Select>
        <ToggleGroup type="single" variant="outline" value={f.size} onValueChange={(v) => v && update({ size: v === 'all' ? '' : v })} aria-label="Entity size">
          <ToggleGroupItem value="all">All</ToggleGroupItem>
          <ToggleGroupItem value="multi">2+ records</ToggleGroupItem>
          <ToggleGroupItem value="single">Single</ToggleGroupItem>
        </ToggleGroup>
        <Toggle variant="outline" pressed={f.review === 'open'} onPressedChange={(p) => update({ review: p ? 'open' : '' })}>
          Open reviews
        </Toggle>
        <Select value={f.sort} onValueChange={(v) => update({ sort: v === 'size' ? '' : v })}>
          <SelectTrigger className="w-44" aria-label="Sort"><SelectValue /></SelectTrigger>
          <SelectContent>
            {Object.entries(SORTS).map(([k, label]) => <SelectItem key={k} value={k}>{label}</SelectItem>)}
          </SelectContent>
        </Select>
        <CrosswalkLookup entityType={entityType} catalog={catalog} onResolve={select} />
      </div>

      {wide ? (
        <ResizablePanelGroup direction="horizontal" className="min-h-[640px] rounded-lg border" autoSaveId="rezolake-explorer">
          <ResizablePanel id="list" order={1} defaultSize={f.e ? 42 : 100} minSize={28}>
            <EntityList entityType={entityType} catalog={catalog} f={f} selected={f.e} compact={!!f.e}
              onSelect={select} onPage={(page) => update({ page })} />
          </ResizablePanel>
          {f.e && (
            <>
              <ResizableHandle withHandle />
              <ResizablePanel id="detail" order={2} defaultSize={58} minSize={35}>
                <div className="h-full overflow-auto p-4">
                  <EntityView key={f.e} entityType={entityType} catalog={catalog} entityId={f.e} variant="panel"
                    onClose={() => update({ e: '' })} />
                </div>
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      ) : (
        <div className="rounded-lg border">
          <EntityList entityType={entityType} catalog={catalog} f={f} selected="" compact
            onSelect={select} onPage={(page) => update({ page })} />
        </div>
      )}
    </div>
  );
}

function SummaryStrip({ entityType, catalog, onFilter }: {
  entityType: EntityType;
  catalog: string;
  onFilter: (patch: { size?: string; review?: string }) => void;
}) {
  const params = useMemo(() => ({ catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id) }), [catalog, entityType.id]);
  const { data, loading, error } = useAnalyticsQuery('entity_type_stats', params);
  const s = data?.[0];
  const n = (v: unknown) => (v == null ? undefined : Number(v));
  const entities = n(s?.entities);
  const multi = n(s?.multi_record_entities);
  const records = n(s?.records);
  const pct = entities ? Math.round((100 * (multi ?? 0)) / entities) : 0;
  const run = entityType.last_run;
  const fmt = (v: number | undefined) => (loading ? '…' : v === undefined ? '—' : v.toLocaleString());

  const tiles = [
    { label: 'Master entities', value: fmt(entities), hint: `from ${fmt(records)} records in ${fmt(n(s?.sources))} sources` },
    { label: 'Multi-record entities', value: fmt(multi), hint: `${pct}% of entities · show them`, onClick: () => onFilter({ size: 'multi', review: '' }) },
    { label: 'Duplicates folded', value: fmt(records !== undefined && entities !== undefined ? records - entities : undefined), hint: 'records merged into another entity' },
    { label: 'Open reviews', value: fmt(n(s?.open_reviews)), hint: 'pairs awaiting a steward · show their entities', onClick: () => onFilter({ review: 'open', size: '' }) },
  ];

  return (
    <div className="space-y-1">
      {error && <ErrorNote error={error} title="Could not load summary" />}
      <div className="grid gap-3 grid-cols-2 xl:grid-cols-6">
        {tiles.map((t) => (
          <Card key={t.label} className={t.onClick ? 'cursor-pointer hover:bg-muted/40' : undefined}
            onClick={t.onClick} role={t.onClick ? 'button' : undefined} tabIndex={t.onClick ? 0 : undefined}
            onKeyDown={(e) => { if (t.onClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); t.onClick(); } }}>
            <CardHeader className="pb-1 pt-4">
              <CardDescription>{t.label}</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{t.value}</CardTitle>
            </CardHeader>
            <CardContent className="pb-4 text-xs text-muted-foreground">{t.hint}</CardContent>
          </Card>
        ))}
        <Card>
          <CardHeader className="pb-1 pt-4">
            <CardDescription>Last run</CardDescription>
            <CardTitle className="text-base">{run ? <><StatusPill value={run.status} /> {run.mode}</> : 'No runs yet'}</CardTitle>
          </CardHeader>
          <CardContent className="pb-4 text-xs text-muted-foreground">
            {run ? <>finished {fmtTime(run.completed_at ?? run.created_at)} · <Link className="underline" to={`/t/${entityType.id}/runs`}>runs</Link></> : 'Start one from the Runs tab.'}
          </CardContent>
        </Card>
        <Card className="col-span-2 xl:col-span-1">
          <CardHeader className="pb-1 pt-4">
            <CardDescription>{entities ? `${100 - pct}% of entities are single records` : 'Entities by size'}</CardDescription>
          </CardHeader>
          <CardContent className="pb-4">
            <SizeDistribution params={params} onPick={(size) => onFilter({ size, review: '' })} />
          </CardContent>
        </Card>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Source: {catalog}.rezolake2_* Delta tables, as of the last completed run.
      </p>
    </div>
  );
}

/** Entities per size bucket as labelled meters; clicking a multi-record bucket filters the list. */
function SizeDistribution({ params, onPick }: {
  params: { catalog: ReturnType<typeof sql.string>; entity_type_id: ReturnType<typeof sql.string> };
  onPick: (size: string) => void;
}) {
  const { data, loading, error } = useAnalyticsQuery('entity_size_distribution', params);
  if (loading) return <Loading rows={2} />;
  if (error) return <p className="text-xs text-destructive">Could not load sizes</p>;
  const rows = data ?? [];
  const max = Math.max(1, ...rows.map((r) => Number(r.entities)));
  return (
    <div className="space-y-0.5" role="list" aria-label="Entities by records per entity">
      {rows.map((r) => (
        <button key={r.records_per_entity} type="button" role="listitem"
          className="flex w-full items-center gap-2 text-[11px] hover:underline"
          onClick={() => onPick(r.records_per_entity === '1' ? 'single' : 'multi')}
          title={`${Number(r.entities).toLocaleString()} entities with ${r.records_per_entity} record(s)`}>
          <span className="w-5 text-right text-muted-foreground">{r.records_per_entity}</span>
          <span className="h-2 flex-1 rounded bg-muted">
            <span className="block h-2 rounded bg-primary/70" style={{ width: `${(100 * Number(r.entities)) / max}%` }} />
          </span>
          <span className="w-10 text-right tabular-nums">{Number(r.entities).toLocaleString()}</span>
        </button>
      ))}
    </div>
  );
}

type Filters = { q: string; source: string; size: string; review: string; sort: string; page: number };

function EntityList({ entityType, catalog, f, selected, compact, onSelect, onPage }: {
  entityType: EntityType;
  catalog: string;
  f: Filters;
  selected: string;
  compact: boolean;
  onSelect: (entityId: string) => void;
  onPage: (page: number) => void;
}) {
  const params = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id), search: sql.string(f.q),
    source_id: sql.string(f.source), size: sql.string(f.size), review: sql.string(f.review), sort: sql.string(f.sort),
    limit: sql.int(PAGE), offset: sql.int(f.page * PAGE),
  }), [catalog, entityType.id, f.q, f.source, f.size, f.review, f.sort, f.page]);
  const { data, loading, error } = useAnalyticsQuery('entities_page', params);
  const attrs = entityType.definition?.attributes ?? [];
  // display_name already shows the first two display attributes (see the survive stage).
  const displayAttrs = attrs.filter((a) => a.display).slice(2, compact ? 2 : 5);
  const total = Number(data?.[0]?.total_count ?? 0);
  const maxSize = Math.max(1, ...(data ?? []).map((r) => Number(r.member_count)));
  const filtered = !!(f.q || f.source || f.size !== 'all' || f.review !== 'all');

  if (loading) return <div className="p-4"><Loading rows={8} /></div>;
  if (error) return <div className="p-4"><ErrorNote error={error} title="Could not load entities" /></div>;
  if (!data?.length) {
    return (
      <div className="p-4">
        <EmptyState title={filtered ? 'No entities match' : 'No entities yet'}>
          {filtered ? 'Change the search or filters.' : <>Run the engine from the <Link className="underline" to={`/t/${entityType.id}/runs`}>Runs tab</Link>.</>}
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Entity</TableHead>
              <TableHead>Sources</TableHead>
              <TableHead className="w-36">Records</TableHead>
              <TableHead className="text-right">Reviews</TableHead>
              {displayAttrs.map((a) => <TableHead key={a.attribute_id}>{a.display_name}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((e) => {
              const g = parseJson<Record<string, string | null>>(e.golden, {});
              const size = Number(e.member_count);
              return (
                <TableRow key={e.entity_id} data-state={e.entity_id === selected ? 'selected' : undefined}
                  className="cursor-pointer" tabIndex={0} onClick={() => onSelect(e.entity_id)}
                  onKeyDown={(k) => { if (k.key === 'Enter') onSelect(e.entity_id); }}>
                  <TableCell className="max-w-72 whitespace-normal">
                    <HoverCard openDelay={400}>
                      <HoverCardTrigger asChild>
                        <span className="font-medium hover:underline line-clamp-2 break-words">{e.display_name}</span>
                      </HoverCardTrigger>
                      <HoverCardContent className="w-80" side="right">
                        <EntityPreview catalog={catalog} entityTypeId={entityType.id} entityId={e.entity_id} attrs={attrs} />
                      </HoverCardContent>
                    </HoverCard>
                    <div className="font-mono text-[10px] text-muted-foreground">{shortId(e.entity_id)}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {parseJson<string[]>(e.sources, []).map((s) => <Badge key={s} variant="outline" className="text-[10px]">{s}</Badge>)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="w-6 text-right tabular-nums">{size}</span>
                      <div className="h-1.5 flex-1 rounded bg-muted" aria-hidden>
                        <div className="h-1.5 rounded bg-primary/70" style={{ width: `${(100 * size) / maxSize}%` }} />
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    {Number(e.open_reviews) > 0 ? <StatusPill value="REVIEW" /> : null}
                    {Number(e.open_reviews) > 0 && <span className="ml-1 tabular-nums text-xs">{e.open_reviews}</span>}
                  </TableCell>
                  {displayAttrs.map((a) => <TableCell key={a.attribute_id} className="text-sm max-w-48 truncate">{g[a.name] ?? '—'}</TableCell>)}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center gap-2 border-t p-2 text-xs text-muted-foreground">
        <span>{(f.page * PAGE + 1).toLocaleString()}–{(f.page * PAGE + data.length).toLocaleString()} of {total.toLocaleString()} entities</span>
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="outline" disabled={f.page === 0} onClick={() => onPage(f.page - 1)} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button>
          <Button size="sm" variant="outline" disabled={(f.page + 1) * PAGE >= total} onClick={() => onPage(f.page + 1)} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button>
        </div>
      </div>
    </div>
  );
}

function CrosswalkLookup({ entityType, catalog, onResolve }: { entityType: EntityType; catalog: string; onResolve: (entityId: string) => void }) {
  const sources = entityType.definition?.sources ?? [];
  const [sourceId, setSourceId] = useState(sources[0]?.source_id ?? '');
  const [key, setKey] = useState('');
  const [query, setQuery] = useState<{ s: string; k: string }>();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline"><KeyRound className="h-4 w-4 mr-1" />Source key lookup</Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 space-y-2" align="end">
        <p className="text-sm font-medium">Crosswalk lookup</p>
        <p className="text-xs text-muted-foreground">Source record → master entity and its sibling records.</p>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label>Source</Label>
            <Select value={sourceId || undefined} onValueChange={setSourceId}>
              <SelectTrigger><SelectValue placeholder="source" /></SelectTrigger>
              <SelectContent>{sources.map((s) => <SelectItem key={s.source_id} value={s.source_id}>{s.source_id}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Source key</Label>
            <Input value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && sourceId && key) setQuery({ s: sourceId, k: key.trim() }); }} />
          </div>
        </div>
        <Button size="sm" disabled={!sourceId || !key} onClick={() => setQuery({ s: sourceId, k: key.trim() })}>Resolve</Button>
        {query && <CrosswalkResult catalog={catalog} entityTypeId={entityType.id} sourceId={query.s} sourceKey={query.k} onResolve={onResolve} />}
      </PopoverContent>
    </Popover>
  );
}

function CrosswalkResult({ catalog, entityTypeId, sourceId, sourceKey, onResolve }: {
  catalog: string; entityTypeId: string; sourceId: string; sourceKey: string; onResolve: (entityId: string) => void;
}) {
  const params = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityTypeId), source_id: sql.string(sourceId), source_key: sql.string(sourceKey),
  }), [catalog, entityTypeId, sourceId, sourceKey]);
  const { data, loading, error } = useAnalyticsQuery('crosswalk_lookup', params);
  if (loading) return <Loading rows={1} />;
  if (error) return <ErrorNote error={error} />;
  if (!data?.length) return <p className="text-sm text-muted-foreground">No record {sourceId}:{sourceKey}.</p>;
  return (
    <div className="text-sm space-y-1">
      <Button size="sm" variant="link" className="h-auto p-0 font-mono" onClick={() => onResolve(data[0].entity_id)}>
        Open entity {shortId(data[0].entity_id)}
      </Button>
      <ul className="text-xs">
        {data.map((r) => (
          <li key={r.record_id} className={isTrue(r.is_query) ? 'font-medium' : 'text-muted-foreground'}>
            {r.source_id}:{r.source_key}{isTrue(r.is_anchor) ? ' (anchor)' : ''}
          </li>
        ))}
      </ul>
    </div>
  );
}
