import { useMemo, useState } from 'react';
import { useOutletContext } from 'react-router';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { Check, Flag, HelpCircle, X } from 'lucide-react';
import { api, parseJson, sendCommand, type Attribute, type Command, type ReviewNote } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useConfig } from '../lib/context';
import { EmptyState, ErrorNote, Loading } from '../components/states';
import type { EntityTypeOutlet } from './EntityTypeLayout';

export function ReviewTab() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { catalog } = useConfig();
  const params = useMemo(
    () => ({ catalog: sql.string(catalog), entity_type_id: sql.string(entityType.id), limit: sql.int(100) }),
    [catalog, entityType.id],
  );
  const { data, loading, error } = useAnalyticsQuery('review_queue', params);
  const commands = useApi<Command[]>(`/api/entity-types/${entityType.id}/commands`, {
    pollMs: 5000,
    pollWhile: (d) => d.some((c) => c.status === 'PENDING' || c.status === 'RUNNING'),
  });
  const notes = useApi<ReviewNote[]>(`/api/entity-types/${entityType.id}/review-notes`);
  const [decided, setDecided] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string>();
  const attrs = entityType.definition?.attributes ?? [];

  const inFlight = useMemo(() => {
    const s = new Set<string>();
    for (const c of commands.data ?? []) {
      if ((c.status === 'PENDING' || c.status === 'RUNNING') && c.payload.left_record_id) {
        s.add(`${c.payload.left_record_id as string}|${c.payload.right_record_id as string}`);
      }
    }
    return s;
  }, [commands.data]);

  async function decide(left: string, right: string, pairId: string, type: 'ACCEPT_MATCH' | 'REJECT_MATCH') {
    setErr(undefined);
    setDecided((d) => ({ ...d, [pairId]: type === 'ACCEPT_MATCH' ? 'Same entity' : 'Different entities' }));
    try {
      await sendCommand(entityType.id, type, { left_record_id: left, right_record_id: right, reason: 'review queue' });
      commands.restart();
    } catch (e) {
      setErr((e as Error).message);
      setDecided((d) => {
        const n = { ...d };
        delete n[pairId];
        return n;
      });
    }
  }

  async function note(pairId: string, verdict: 'UNSURE' | 'NOTE', flagged: boolean, text?: string) {
    await api(`/api/entity-types/${entityType.id}/review-notes`, {
      method: 'POST',
      json: { pair_id: pairId, verdict, flagged, note: text },
    });
    await notes.refresh();
  }

  const pending = (commands.data ?? []).filter((c) => c.status === 'PENDING' || c.status === 'RUNNING').length;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Pairs the decision policy scored between the review and link thresholds. &ldquo;Same entity&rdquo; writes a durable
        MUST_LINK constraint and &ldquo;Different&rdquo; writes a CANNOT_LINK, so later automated runs never undo them.
        {pending > 0 && <> · <b>{pending}</b> decision(s) applying on the engine…</>}
      </p>
      {err && <ErrorNote error={err} title="Could not submit decision" />}
      {loading && <Loading rows={4} />}
      {error && <ErrorNote error={error} title="Could not load review queue" />}
      {data && data.length === 0 && <EmptyState title="Review queue is empty">Nothing is waiting on a steward.</EmptyState>}
      {data?.map((r) => {
        const key = `${r.left_record_id}|${r.right_record_id}`;
        const done = decided[r.pair_id] ?? (inFlight.has(key) ? 'Decision applying' : undefined);
        const ev = parseJson<{ reason?: string; is_match?: boolean; confidence?: number; endpoint?: string; model?: string }>(r.evidence, {});
        const myNotes = (notes.data ?? []).filter((n) => n.pair_id === r.pair_id);
        return (
          <Card key={r.pair_id} className={done ? 'opacity-60' : undefined}>
            <CardHeader className="pb-2 flex flex-row flex-wrap items-center gap-2">
              <CardTitle className="text-base">P(match) {Number(r.score).toFixed(2)}</CardTitle>
              {ev.is_match !== undefined && <Badge variant="outline">LLM: {ev.is_match ? 'match' : 'no match'} ({Number(ev.confidence ?? 0).toFixed(2)})</Badge>}
              {ev.model === 'ai_decide' && <Badge variant="outline">ai_decide (no rationale)</Badge>}
              {myNotes.some((n) => n.flagged) && <Badge variant="destructive">flagged</Badge>}
              {myNotes.some((n) => n.verdict === 'UNSURE') && <Badge variant="secondary">marked unsure</Badge>}
              {r.trace_id && <span className="text-xs text-muted-foreground font-mono">trace {r.trace_id.slice(0, 12)}</span>}
              <div className="ml-auto flex gap-2">
                {done ? (
                  <Badge>{done}</Badge>
                ) : (
                  <>
                    <Button size="sm" onClick={() => decide(r.left_record_id, r.right_record_id, r.pair_id, 'ACCEPT_MATCH')}>
                      <Check className="h-4 w-4 mr-1" /> Same entity
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => decide(r.left_record_id, r.right_record_id, r.pair_id, 'REJECT_MATCH')}>
                      <X className="h-4 w-4 mr-1" /> Different
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => note(r.pair_id, 'UNSURE', false)}>
                      <HelpCircle className="h-4 w-4 mr-1" /> Unsure
                    </Button>
                  </>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              <SideBySide
                attrs={attrs}
                left={{ source: r.left_source, key: r.left_key, values: parseJson<Record<string, string>>(r.left_attributes, {}) }}
                right={{ source: r.right_source, key: r.right_key, values: parseJson<Record<string, string>>(r.right_attributes, {}) }}
              />
              {ev.reason && <p className="text-sm"><span className="text-muted-foreground">Matcher evidence:</span> {ev.reason}</p>}
              <NoteBox onSave={(t, flag) => note(r.pair_id, 'NOTE', flag, t)} notes={myNotes} />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

export function SideBySide({ attrs, left, right }: {
  attrs: Attribute[];
  left: { source: string; key: string; values: Record<string, string> };
  right: { source: string; key: string; values: Record<string, string> };
}) {
  // Fixed columns so both records always fit the card: values wrap, long ones clamp until expanded.
  const [full, setFull] = useState(false);
  const long = attrs.some((a) => (left.values[a.attribute_id] ?? '').length > 160 || (right.values[a.attribute_id] ?? '').length > 160);
  const cell = (v: string | undefined, differs: boolean) => (
    <td className={`p-2 align-top break-words ${differs ? 'bg-warning/10' : ''}`} title={v && !full ? v : undefined}>
      {v ? <span className={full ? undefined : 'line-clamp-4'}>{v}</span> : <span className="text-muted-foreground">—</span>}
    </td>
  );
  const head = (r: { source: string; key: string }) => (
    <th className="p-2 text-left align-top font-medium">
      {r.source} <span className="font-normal text-muted-foreground break-all">· {r.key}</span>
    </th>
  );
  return (
    <div className="space-y-1">
      <table className="w-full table-fixed text-sm">
        <colgroup>
          <col className="w-28 sm:w-36" />
          <col />
          <col />
        </colgroup>
        <thead>
          <tr className="border-b">
            <th className="p-2 text-left align-top font-medium text-muted-foreground">Attribute</th>
            {head(left)}
            {head(right)}
          </tr>
        </thead>
        <tbody>
          {attrs.map((a) => {
            const l = left.values[a.attribute_id];
            const r = right.values[a.attribute_id];
            const differs = !!l && !!r && l.trim().toLowerCase() !== r.trim().toLowerCase();
            return (
              <tr key={a.attribute_id} className="border-b last:border-0">
                <td className="p-2 align-top text-muted-foreground break-words">{a.display_name}</td>
                {cell(l, differs)}
                {cell(r, differs)}
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-warning/20" /> values differ</span>
        {long && (
          <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={() => setFull(!full)}>
            {full ? 'Collapse long text' : 'Show full text'}
          </Button>
        )}
      </div>
    </div>
  );
}

function NoteBox({ onSave, notes }: { onSave: (text: string, flag: boolean) => Promise<void>; notes: ReviewNote[] }) {
  const [text, setText] = useState('');
  return (
    <div className="space-y-1">
      {notes.filter((n) => n.note).map((n) => (
        <p key={n.id} className="text-xs text-muted-foreground">
          {n.flagged && '🚩 '}<b>{n.author}</b>: {n.note}
        </p>
      ))}
      <div className="flex gap-2">
        <Input className="h-8" placeholder="Add a comment…" value={text} onChange={(e) => setText(e.target.value)} />
        <Button size="sm" variant="outline" disabled={!text} onClick={async () => { await onSave(text, false); setText(''); }}>
          Comment
        </Button>
        <Button size="sm" variant="ghost" onClick={async () => { await onSave(text, true); setText(''); }}>
          <Flag className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
