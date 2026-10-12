// Merge with another entity: search for it, then merge the whole entities or link one specific
// pair of records (a MUST_LINK between them, which pulls the entities together on the next run).
import { useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { GitMerge } from 'lucide-react';
import { parseJson, shortId } from '../../lib/api';
import { useDebounced } from '../../lib/hooks';
import { ErrorNote, Loading } from '../states';
import type { MemberRow } from './CompareTable';
import type { OnCommand } from './types';

function RecordPicker({ members, value, onChange, title }: { members: MemberRow[]; value?: string; onChange: (id: string) => void; title: string }) {
  return (
    <div className="space-y-1 min-w-0">
      <Label>{title}</Label>
      <div className="max-h-48 overflow-auto rounded border divide-y">
        {members.map((m) => (
          <label key={m.record_id} className={`flex cursor-pointer items-center gap-2 p-2 text-xs ${value === m.record_id ? 'bg-muted' : ''}`}>
            <input type="radio" name={title} checked={value === m.record_id} onChange={() => onChange(m.record_id)} />
            <span className="font-medium">{m.source_id}</span>
            <span className="truncate text-muted-foreground" title={m.source_key}>{m.source_key}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

export function MergeDialog({ catalog, entityTypeId, entityId, members, onCommand }: {
  catalog: string;
  entityTypeId: string;
  entityId: string;
  members: MemberRow[];
  onCommand: OnCommand;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const search = useDebounced(text.trim(), 300);
  const [target, setTarget] = useState<{ id: string; name: string }>();
  const [ours, setOurs] = useState<string>();
  const [theirs, setTheirs] = useState<string>();

  const searchParams = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityTypeId), search: sql.string(search),
    source_id: sql.string(''), size: sql.string('all'), review: sql.string('all'), sort: sql.string('size'),
    limit: sql.int(8), offset: sql.int(0),
  }), [catalog, entityTypeId, search]);
  const results = useAnalyticsQuery('entities_page', searchParams, { autoStart: open && search.length > 0 });
  const targetParams = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityTypeId), entity_id: sql.string(target?.id ?? ''),
  }), [catalog, entityTypeId, target]);
  const targetMembers = useAnalyticsQuery('entity_members', targetParams, { autoStart: !!target });

  function reset() {
    setText(''); setTarget(undefined); setOurs(undefined); setTheirs(undefined);
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}><GitMerge className="h-4 w-4 mr-1" />Merge with…</Button>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) reset(); }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Merge with another entity</DialogTitle>
            <DialogDescription>Find the other entity, then merge the two entities or link one record from each.</DialogDescription>
          </DialogHeader>
          {!target ? (
            <div className="space-y-2">
              <Input autoFocus placeholder="Search by name, entity id, record id or source key" value={text} onChange={(e) => setText(e.target.value)} />
              {results.loading && <Loading rows={3} />}
              {results.error && <ErrorNote error={results.error} />}
              {search && results.data && results.data.filter((r) => r.entity_id !== entityId).length === 0 && (
                <p className="text-sm text-muted-foreground">No other entity matches “{search}”.</p>
              )}
              <div className="divide-y rounded border empty:hidden">
                {(results.data ?? []).filter((r) => r.entity_id !== entityId).map((r) => (
                  <button key={r.entity_id} type="button" className="flex w-full items-center gap-2 p-2 text-left text-sm hover:bg-muted"
                    onClick={() => setTarget({ id: r.entity_id, name: r.display_name })}>
                    <span className="font-medium truncate">{r.display_name}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">{shortId(r.entity_id)}</span>
                    <span className="ml-auto flex gap-1">
                      {parseJson<string[]>(r.sources, []).map((s) => <Badge key={s} variant="outline">{s}</Badge>)}
                      <Badge variant="secondary">{r.member_count} rec</Badge>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm">
                Merging with <b>{target.name}</b> <span className="font-mono text-xs text-muted-foreground">{shortId(target.id)}</span>{' '}
                <Button size="sm" variant="link" className="h-auto p-0" onClick={() => { setTarget(undefined); setTheirs(undefined); }}>change</Button>
              </p>
              {targetMembers.loading && <Loading rows={3} />}
              {targetMembers.error && <ErrorNote error={targetMembers.error} />}
              {targetMembers.data && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <RecordPicker title="A record from this entity" members={members} value={ours} onChange={setOurs} />
                  <RecordPicker title={`A record from ${target.name}`} members={targetMembers.data} value={theirs} onChange={setTheirs} />
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                <b>Merge entities</b> pins every record of both entities to the surviving anchor (MUST_LINKs) and lifts any CANNOT_LINKs between
                the two entities (e.g. from an earlier split); the older entity id survives and the other becomes an alias.
                <b> Link records</b> writes a MUST_LINK between the two records you picked; it does not lift earlier
                CANNOT_LINKs, so use Merge entities to rejoin entities that were split apart.
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={!target || !ours || !theirs}
              onClick={async () => { await onCommand('ACCEPT_MATCH', { left_record_id: ours, right_record_id: theirs, reason: 'steward link' }, 'Link records'); setOpen(false); reset(); }}>
              Link selected records
            </Button>
            <Button disabled={!target}
              onClick={async () => { await onCommand('MERGE_ENTITIES', { entity_ids: [entityId, target!.id], reason: 'steward merge' }, 'Merge'); setOpen(false); reset(); }}>
              Merge entities
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
