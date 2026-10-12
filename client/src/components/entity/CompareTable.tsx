// Golden record and member records side by side: one row per attribute, one column per record.
// Cells that differ from the golden value are highlighted; the record each golden value came from is marked.
import { useState } from 'react';
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Switch,
} from '@databricks/appkit-ui/react';
import { Check, Pencil, Pin, UserMinus } from 'lucide-react';
import { isTrue, parseJson, type Attribute } from '../../lib/api';
import { StatusPill } from '../states';
import type { OnCommand } from './types';

export interface MemberRow {
  record_id: string;
  source_id: string;
  source_key: string;
  attributes_json: string;
  is_anchor: boolean | string;
}

export interface GoldenRow {
  attribute_id: string;
  attribute_name: string;
  value: string | null;
  winning_record_id: string | null;
  winning_source_id: string | null;
  rule: string;
  manual_override: boolean | string;
  observed: string;
}

const norm = (v: string | number | null | undefined) => (v == null ? '' : String(v).trim().toLowerCase());

export function CompareTable({ entityId, attrs, golden, members, selected, onToggle, onCommand }: {
  entityId: string;
  attrs: Attribute[];
  golden: GoldenRow[];
  members: MemberRow[];
  selected: Set<string>;
  onToggle: (recordId: string) => void;
  onCommand: OnCommand;
}) {
  const [diffOnly, setDiffOnly] = useState(false);
  const [override, setOverride] = useState<{ id: string; name: string; value: string }>();
  const [edit, setEdit] = useState<{ record: string; attr: string; value: string }>();
  const byId = new Map(golden.map((g) => [g.attribute_id, g]));
  const values = members.map((m) => parseJson<Record<string, string | null>>(m.attributes_json, {}));
  const rows = attrs
    .map((a) => ({ a, g: byId.get(a.attribute_id) }))
    .filter(({ a, g }) => !diffOnly || values.some((v) => norm(v[a.attribute_id]) !== norm(g?.value)));
  const differing = attrs.filter((a) => values.some((v) => norm(v[a.attribute_id]) !== norm(byId.get(a.attribute_id)?.value))).length;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>{differing} of {attrs.length} attributes differ across records</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded-sm bg-warning/20" /> differs from golden</span>
        <span className="inline-flex items-center gap-1"><Check className="h-3 w-3 text-success" /> golden value came from this record</span>
        <label className="ml-auto inline-flex items-center gap-2">
          <Switch checked={diffOnly} onCheckedChange={setDiffOnly} /> Only differing attributes
        </label>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40 align-top">
              <th className="sticky left-0 z-10 bg-muted/40 p-2 text-left font-medium w-32">Attribute</th>
              <th className="p-2 text-left font-medium min-w-48 border-r">Golden record</th>
              {members.map((m) => (
                <th key={m.record_id} className="p-2 text-left font-normal min-w-44">
                  <div className="flex items-start gap-2">
                    <Checkbox aria-label={`select ${m.source_id}:${m.source_key}`} checked={selected.has(m.record_id)}
                      onCheckedChange={() => onToggle(m.record_id)} className="mt-0.5" />
                    <div className="min-w-0">
                      <div className="font-medium">{m.source_id}</div>
                      <div className="text-xs text-muted-foreground truncate max-w-40" title={m.source_key}>{m.source_key}</div>
                      {isTrue(m.is_anchor) && <Badge variant="outline" className="mt-1">anchor</Badge>}
                    </div>
                    <div className="ml-auto flex">
                      <Button size="icon" variant="ghost" className="h-7 w-7" title="Use this record as the golden source"
                        onClick={() => onCommand('PIN_GOLDEN_RECORD', { entity_id: entityId, record_id: m.record_id }, 'Pin golden record')}>
                        <Pin className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon" variant="ghost" className="h-7 w-7" title="Remove this record from the entity"
                        onClick={() => onCommand('ISOLATE_RECORD', { record_id: m.record_id, reason: 'removed by steward' }, 'Remove record')}>
                        <UserMinus className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ a, g }) => {
              const observed = parseJson<{ source_id: string; value: string }[]>(g?.observed, []);
              return (
                <tr key={a.attribute_id} className="border-b last:border-0 align-top">
                  <td className="sticky left-0 z-10 bg-background p-2 text-muted-foreground">{a.display_name}</td>
                  <td className="p-2 border-r">
                    <div className="flex items-start gap-1">
                      <div className="min-w-0 flex-1">
                        <div className="font-medium break-words line-clamp-3" title={g?.value ?? undefined}>{g?.value ?? <span className="text-muted-foreground">—</span>}</div>
                        {g && (
                          <div className="text-xs text-muted-foreground">
                            {isTrue(g.manual_override) ? <StatusPill value={g.rule} /> : g.rule.replace(/_/g, ' ').toLowerCase()}
                            {g.winning_source_id && <> · {g.winning_source_id}</>}
                            {new Set(observed.map((o) => norm(o.value))).size > 1 && <> · {new Set(observed.map((o) => norm(o.value))).size} variants</>}
                          </div>
                        )}
                      </div>
                      <Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" title="Override the golden value"
                        onClick={() => setOverride({ id: a.attribute_id, name: a.display_name, value: g?.value ?? '' })}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                  {members.map((m, i) => {
                    const v = values[i][a.attribute_id];
                    const differs = v != null && v !== '' && norm(v) !== norm(g?.value);
                    const winner = g?.winning_record_id === m.record_id;
                    return (
                      <td key={m.record_id}
                        className={`p-2 cursor-pointer break-words hover:underline ${differs ? 'bg-warning/10' : ''} ${selected.has(m.record_id) ? 'bg-muted/50' : ''}`}
                        title={`${v ? `${v}\n\n` : ''}Click to edit this record's value (survives re-ingest)`}
                        onClick={() => setEdit({ record: m.record_id, attr: a.attribute_id, value: v ?? '' })}>
                        <span className="line-clamp-3">
                          {v == null || v === '' ? <span className="text-muted-foreground">—</span> : v}
                          {winner && <Check className="inline h-3.5 w-3.5 ml-1 text-success" aria-label="golden source" />}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="text-sm text-muted-foreground">All records agree on every attribute.</p>}

      {override && (
        <Dialog open onOpenChange={(o) => !o && setOverride(undefined)}>
          <DialogContent>
            <DialogHeader><DialogTitle>Override golden {override.name}</DialogTitle><DialogDescription>Set the master value for this attribute.</DialogDescription></DialogHeader>
            <Input value={override.value} onChange={(x) => setOverride({ ...override, value: x.target.value })} />
            <p className="text-xs text-muted-foreground">A manual override beats every survivorship rule and survives future runs. Clear it to fall back to the rule.</p>
            <DialogFooter>
              <Button variant="outline" onClick={async () => { await onCommand('OVERRIDE_ATTRIBUTE', { entity_id: entityId, attribute_id: override.id, value: null }, 'Clear override'); setOverride(undefined); }}>Clear override</Button>
              <Button onClick={async () => { await onCommand('OVERRIDE_ATTRIBUTE', { entity_id: entityId, attribute_id: override.id, value: override.value }, 'Override'); setOverride(undefined); }}>Save override</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {edit && (
        <Dialog open onOpenChange={(o) => !o && setEdit(undefined)}>
          <DialogContent>
            <DialogHeader><DialogTitle>Edit record value</DialogTitle><DialogDescription>Change one source record&apos;s value for this attribute.</DialogDescription></DialogHeader>
            <Label>{attrs.find((a) => a.attribute_id === edit.attr)?.display_name}</Label>
            <Input value={edit.value} onChange={(x) => setEdit({ ...edit, value: x.target.value })} />
            <p className="text-xs text-muted-foreground">Record-level curation: applied at canonicalization, so it survives source re-ingest. The record is re-matched.</p>
            <DialogFooter>
              <Button variant="outline" onClick={async () => { await onCommand('EDIT_RECORD', { record_id: edit.record, attribute_id: edit.attr, value: null }, 'Revert edit'); setEdit(undefined); }}>Revert to source</Button>
              <Button onClick={async () => { await onCommand('EDIT_RECORD', { record_id: edit.record, attribute_id: edit.attr, value: edit.value }, 'Edit'); setEdit(undefined); }}>Save</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
