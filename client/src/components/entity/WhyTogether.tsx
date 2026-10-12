// Why are these records one entity? Graph of decisions and constraints, a split suggestion computed
// from the graph, and the evidence table with accept / reject / revoke actions.
import { useMemo, useState } from 'react';
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  ToggleGroup,
  ToggleGroupItem,
} from '@databricks/appkit-ui/react';
import { Network, Scissors, TableIcon, X } from 'lucide-react';
import { isTrue, parseJson, shortId, type Attribute } from '../../lib/api';
import { StatusPill } from '../states';
import { ClusterGraph, type GraphEdge, type GraphNode } from './ClusterGraph';
import { suggestSplit, type SplitEdge } from './split';
import type { GoldenRow, MemberRow } from './CompareTable';
import { RecordDetail } from './RecordDetail';
import type { OnCommand } from './types';

export interface EdgeRow {
  kind: string;
  pair_id: string | null;
  left_record_id: string;
  right_record_id: string | null;
  label: string;
  score: number | string | null;
  reason: string | null;
  detail: string | null;
  evidence: string | null;
  matcher_id: string | null;
  is_current: boolean | string;
  constraint_id: string | null;
  internal: boolean | string;
  strategy_version?: number | string | null;
  scores?: string | null;
}

/** Long source keys (e.g. URLs) shortened to their tail for graph labels. */
const shortKey = (k: string) => (k.length > 16 ? `…${k.slice(-10)}` : k);

const CUTS: Record<string, { label: string; below?: number }> = {
  weakest: { label: 'the weakest link' },
  '0.95': { label: 'links below 0.95', below: 0.95 },
  '0.9': { label: 'links below 0.90', below: 0.9 },
  '0.85': { label: 'links below 0.85', below: 0.85 },
};

/** "strategy v3 · llm_entity_matcher_v1.p2" — which strategy and matcher(s) produced a decision. */
function provenance(r: EdgeRow): string {
  const scores = parseJson<{ matcher?: string }[]>(r.scores, []);
  const matchers = scores.map((x) => x.matcher).filter(Boolean).join(', ') || r.matcher_id || '';
  return [r.strategy_version != null ? `strategy v${r.strategy_version}` : 'before provenance', matchers].filter(Boolean).join(' · ');
}

export function WhyTogether({ entityId, entityTypeId, catalog, attrs, golden, members, edges, onCommand }: {
  entityId: string;
  entityTypeId: string;
  catalog: string;
  attrs: Attribute[];
  golden: GoldenRow[];
  members: MemberRow[];
  edges: EdgeRow[];
  onCommand: OnCommand;
}) {
  const [view, setView] = useState<'graph' | 'table'>('graph');
  const [showOutside, setShowOutside] = useState(false);
  const [focus, setFocus] = useState<string>();
  const [cutLevel, setCutLevel] = useState<string>();
  const memberIds = useMemo(() => new Set(members.map((m) => m.record_id)), [members]);
  const label = useMemo(() => {
    const m = new Map(members.map((r) => [r.record_id, `${r.source_id}:${r.source_key}`]));
    return (id: string | null) => (id ? m.get(id) ?? `outside ${shortId(id)}` : '—');
  }, [members]);
  const shortLabel = (id: string) => {
    const r = members.find((x) => x.record_id === id);
    return r ? `${r.source_id}:${shortKey(r.source_key)}` : label(id);
  };
  const anchor = members.find((m) => isTrue(m.is_anchor))?.record_id;

  const current = useMemo(() => edges.filter((r) => r.kind === 'constraint' || isTrue(r.is_current)), [edges]);
  // Internal edges explain the grouping; external LINK/REVIEW edges and constraints explain what was kept out.
  const visible = useMemo(
    () => current.filter((r) => showOutside || isTrue(r.internal) || r.label !== 'NO_LINK'),
    [current, showOutside],
  );

  // Split suggestion over internal positive edges.
  const split = useMemo(() => {
    if (!cutLevel) return null;
    const positive: SplitEdge[] = current
      .filter((r) => isTrue(r.internal) && r.right_record_id && (r.label === 'LINK' || r.label === 'MUST_LINK'))
      .map((r) => ({ left: r.left_record_id, right: r.right_record_id!, score: Number(r.score ?? 0), kind: r.label as SplitEdge['kind'] }));
    return suggestSplit([...memberIds], positive, CUTS[cutLevel].below, anchor);
  }, [cutLevel, current, memberIds, anchor]);
  const groupOf = useMemo(() => {
    const g = new Map<string, number>();
    split?.groups.forEach((grp, i) => grp.forEach((r) => g.set(r, i)));
    return g;
  }, [split]);
  const graph = useMemo(() => {
    const cutKeys = new Set((split?.cut ?? []).flatMap((c) => [`${c.left}|${c.right}`, `${c.right}|${c.left}`]));
    const nodes: GraphNode[] = members.map((m) => ({
      id: m.record_id, label: `${m.source_id}:${shortKey(m.source_key)}`, source: m.source_id,
      anchor: isTrue(m.is_anchor), group: split ? groupOf.get(m.record_id) : undefined,
    }));
    const outside = new Set<string>();
    const gEdges: GraphEdge[] = [];
    for (const r of visible) {
      if (!r.right_record_id) continue; // ISOLATE constraints have one side
      for (const id of [r.left_record_id, r.right_record_id]) if (!memberIds.has(id)) outside.add(id);
      gEdges.push({ source: r.left_record_id, target: r.right_record_id, label: r.label,
        score: r.score == null ? null : Number(r.score), cut: cutKeys.has(`${r.left_record_id}|${r.right_record_id}`) });
    }
    for (const id of outside) nodes.push({ id, label: `outside ${shortId(id)}`, source: 'outside', external: true });
    return { nodes, edges: gEdges };
  }, [members, visible, memberIds, split, groupOf]);

  const rows = focus ? visible.filter((r) => r.left_record_id === focus || r.right_record_id === focus) : visible;
  const hiddenNoLinks = current.length - current.filter((r) => isTrue(r.internal) || r.label !== 'NO_LINK').length;

  async function applySplit() {
    if (!split) return;
    for (const group of split.groups.slice(1)) {
      await onCommand('SPLIT_ENTITY', { entity_id: entityId, record_ids: group, reason: 'steward split (suggested)' }, 'Split');
    }
    setCutLevel(undefined);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <ToggleGroup type="single" variant="outline" size="sm" value={view} onValueChange={(v) => v && setView(v as 'graph' | 'table')}>
          <ToggleGroupItem value="graph" aria-label="Graph"><Network className="h-4 w-4 mr-1" />Graph</ToggleGroupItem>
          <ToggleGroupItem value="table" aria-label="Evidence table"><TableIcon className="h-4 w-4 mr-1" />Evidence</ToggleGroupItem>
        </ToggleGroup>
        <label className="inline-flex items-center gap-2 text-sm">
          <Switch checked={showOutside} onCheckedChange={setShowOutside} /> Include non-matches to other entities{hiddenNoLinks ? ` (${hiddenNoLinks})` : ''}
        </label>
        <div className="ml-auto flex items-center gap-2">
          <Scissors className="h-4 w-4 text-muted-foreground" />
          <Select value={cutLevel ?? 'none'} onValueChange={(v) => setCutLevel(v === 'none' ? undefined : v)}>
            <SelectTrigger className="h-8 w-56"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No split suggestion</SelectItem>
              {Object.entries(CUTS).map(([k, c]) => <SelectItem key={k} value={k}>Suggest split: cut {c.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>

      {cutLevel && !split && (
        <Alert><AlertDescription>Nothing to cut at that level: every remaining link is a human must-link or stronger than the threshold.</AlertDescription></Alert>
      )}
      {split && (
        <Alert>
          <AlertTitle>Suggested split into {split.groups.length} entities</AlertTitle>
          <AlertDescription className="space-y-2">
            <p className="text-xs">
              Cuts {split.cut.length} link{split.cut.length === 1 ? '' : 's'}
              {split.cut.length > 0 && ` (scores ${split.cut.map((c) => c.score.toFixed(2)).join(', ')})`} in the strongest-link tree.
              The first group keeps this entity id; each other group becomes a new entity.
            </p>
            <div className="flex flex-wrap gap-3">
              {split.groups.map((g, i) => (
                <div key={g[0]} className="rounded border p-2 text-xs max-w-xs">
                  <div className="font-medium mb-1">{i === 0 ? 'Keeps this entity' : `New entity ${i}`} · {g.length} record{g.length === 1 ? '' : 's'}</div>
                  <div className="text-muted-foreground break-words" title={g.map(label).join('\n')}>
                    {g.slice(0, 6).map(shortLabel).join(', ')}{g.length > 6 ? ` +${g.length - 6} more` : ''}
                  </div>
                </div>
              ))}
            </div>
            <Button size="sm" onClick={applySplit}>Apply split</Button>
          </AlertDescription>
        </Alert>
      )}

      {view === 'graph' && (
        <div className="rounded-md border">
          <ClusterGraph nodes={graph.nodes} edges={graph.edges} selected={focus} onSelect={(id) => setFocus(id === focus ? undefined : id)} />
          <div className="flex flex-wrap gap-3 border-t p-2 text-xs text-muted-foreground">
            <span>Node colour: {split ? 'suggested group' : 'source'} · larger node: anchor · ◆ record in another entity</span>
            <span>Edges: grey = link (thicker = higher score) · amber = review · blue = human must-link · red dashed = non-match or cannot-link · faded dashed = cut by the suggestion</span>
            <span>Click a record to see its details and evidence; scroll to zoom, drag to rearrange.</span>
          </div>
        </div>
      )}

      {focus && (
        <>
          <RecordDetail recordId={focus} entityId={entityId} entityTypeId={entityTypeId} catalog={catalog} attrs={attrs}
            golden={golden} members={members} onCommand={onCommand} onClose={() => setFocus(undefined)} />
          <div className="flex items-center gap-2 text-sm">
            Evidence for <Badge variant="secondary">{label(focus)}</Badge>
            <Button size="icon" variant="ghost" className="h-6 w-6" aria-label="Clear record filter" onClick={() => setFocus(undefined)}><X className="h-3.5 w-3.5" /></Button>
          </div>
        </>
      )}
      {(view === 'table' || focus) && (
        rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No decisions or constraints{focus ? ' for this record' : ' — this entity is a single record'}.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Pair</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const ev = parseJson<{ reason?: string; model?: string }>(r.evidence, {});
                return (
                  <TableRow key={r.pair_id ?? r.constraint_id ?? `${r.label}-${r.left_record_id}`}>
                    <TableCell className="text-xs whitespace-nowrap">{label(r.left_record_id)} ↔ {label(r.right_record_id)}</TableCell>
                    <TableCell>
                      <StatusPill value={r.label} />
                      <div className="text-[10px] text-muted-foreground">
                        {r.kind === 'constraint' ? `by ${r.detail}` : provenance(r)}
                      </div>
                    </TableCell>
                    <TableCell className="tabular-nums">{r.score == null ? '—' : Number(r.score).toFixed(2)}</TableCell>
                    <TableCell className="text-xs max-w-lg whitespace-normal">
                      {ev.reason ?? (ev.model === 'ai_decide' ? 'ai_decide (no rationale)' : r.reason)}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      {r.kind === 'decision' && r.label !== 'NO_LINK' && (
                        <Button size="sm" variant="ghost" onClick={() => onCommand('REJECT_MATCH', { left_record_id: r.left_record_id, right_record_id: r.right_record_id, reason: 'edge rejected' }, 'Reject link')}>Reject</Button>
                      )}
                      {r.kind === 'decision' && r.label !== 'LINK' && (
                        <Button size="sm" variant="ghost" onClick={() => onCommand('ACCEPT_MATCH', { left_record_id: r.left_record_id, right_record_id: r.right_record_id, reason: 'edge accepted' }, 'Accept link')}>Accept</Button>
                      )}
                      {r.kind === 'constraint' && r.constraint_id && (
                        <Button size="sm" variant="ghost" onClick={() => onCommand('REVOKE_CONSTRAINT', { constraint_id: r.constraint_id }, 'Revoke constraint')}>Revoke</Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )
      )}
    </div>
  );
}
