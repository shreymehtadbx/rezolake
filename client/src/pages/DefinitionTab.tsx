import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router';
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
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
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { Plus, Trash2 } from 'lucide-react';
import { api, parseJson, type Attribute, type DefinitionDraft, type SourceDef, type TableInfo } from '../lib/api';
import { useApi } from '../lib/hooks';
import { ErrorNote, Loading } from '../components/states';
import type { EntityTypeOutlet } from './EntityTypeLayout';

const EXT = '__extension__';
const IGNORE = '__ignore__';
const NEW = '__new__';
const SEMANTIC = ['text', 'name', 'email', 'phone', 'address', 'id', 'date', 'number', 'code'] as const;
const DTYPES = ['string', 'integer', 'float', 'date', 'boolean'] as const;
const RULES = ['SOURCE_PRIORITY', 'MOST_RECENT_NON_NULL', 'MOST_COMMON', 'LONGEST'] as const;
const CASING = ['none', 'title', 'upper', 'lower'] as const;

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
const toIdent = (s: string) =>
  s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'attr';

// Common synonyms so a second source proposes the right canonical attribute.
const SYNONYMS: Record<string, string[]> = {
  first_name: ['firstname', 'fname', 'givenname', 'forename', 'first'],
  last_name: ['lastname', 'lname', 'familyname', 'surname', 'last'],
  email: ['email', 'emailaddress', 'mail', 'emailaddr'],
  phone: ['phone', 'tel', 'telephone', 'mobile', 'phonenumber', 'cell'],
  city: ['city', 'town'],
  name: ['name', 'fullname', 'productname', 'title'],
  postal_code: ['zip', 'zipcode', 'postcode', 'postalcode'],
};

function guessSemantic(name: string): Attribute['semantic_type'] {
  const n = norm(name);
  if (n.includes('email') || n.includes('mail')) return 'email';
  if (n.includes('phone') || n.includes('tel') || n.includes('mobile')) return 'phone';
  if (n.includes('name')) return 'name';
  if (n.includes('addr') || n.includes('street') || n.includes('city') || n.includes('zip')) return 'address';
  if (n.endsWith('id') || n.includes('sku') || n.includes('code')) return 'id';
  if (n.includes('date') || n.includes('dob')) return 'date';
  return 'text';
}

function guessType(t: string): Attribute['data_type'] {
  const x = t.toLowerCase();
  if (/(int|long|short|byte)/.test(x)) return 'integer';
  if (/(double|float|decimal)/.test(x)) return 'float';
  if (x.startsWith('date') || x.startsWith('timestamp')) return 'date';
  if (x.startsWith('boolean')) return 'boolean';
  return 'string';
}

function suggestAttribute(col: string, attrs: Attribute[]): string | undefined {
  const n = norm(col);
  const exact = attrs.find((a) => norm(a.name) === n || norm(a.display_name) === n);
  if (exact) return exact.attribute_id;
  for (const a of attrs) {
    const syns = SYNONYMS[a.name] ?? [];
    if (syns.includes(n)) return a.attribute_id;
  }
  for (const [canon, syns] of Object.entries(SYNONYMS)) {
    if (syns.includes(n)) {
      const a = attrs.find((x) => x.name === canon);
      if (a) return a.attribute_id;
    }
  }
  return undefined;
}

function newAttribute(name: string, type = 'string', existing: Attribute[] = []): Attribute {
  let base = toIdent(name);
  let n = base;
  let i = 2;
  while (existing.some((a) => a.name === n)) n = `${base}_${i++}`;
  base = n;
  return {
    attribute_id: `a_${base}`,
    name: base,
    display_name: name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
    data_type: guessType(type),
    semantic_type: guessSemantic(name),
    matching: true,
    display: true,
    survivorship: { rule: 'SOURCE_PRIORITY' },
    // Sources disagree on casing (JON vs Jon); title-case people/place names by default.
    standardize: ['name', 'address'].includes(guessSemantic(name)) ? 'title' : 'none',
  };
}

export function DefinitionTab() {
  const { entityType, refresh } = useOutletContext<EntityTypeOutlet>();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<DefinitionDraft>(() => ({
    attributes: entityType.definition?.attributes ?? [],
    sources: entityType.definition?.sources ?? [],
  }));
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string }>();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<SourceDef | 'new' | null>(null);

  const update = (d: DefinitionDraft) => {
    setDraft(d);
    setDirty(true);
  };

  async function save() {
    await api(`/api/entity-types/${entityType.id}/definition`, { method: 'PUT', json: draft });
    setDirty(false);
    await refresh();
  }

  async function onSave() {
    setBusy(true);
    try {
      await save();
      setMsg({ ok: true, text: 'Draft saved.' });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function onPublish() {
    setBusy(true);
    try {
      if (dirty) await save();
      const { run_id } = await api<{ run_id: string }>(`/api/entity-types/${entityType.id}/publish`, { method: 'POST' });
      void navigate(`../runs?run=${run_id}`);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const problems = useMemo(() => {
    const p: string[] = [];
    if (!draft.attributes.length) p.push('Add at least one attribute.');
    if (!draft.sources.some((s) => s.active)) p.push('Add at least one active source.');
    if (!draft.attributes.some((a) => a.matching)) p.push('Mark at least one attribute for matching.');
    for (const s of draft.sources) if (!s.primary_key.length) p.push(`Source ${s.source_id} needs a primary key.`);
    return p;
  }, [draft]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Sources</CardTitle>
            <CardDescription>
              Governed Unity Catalog tables that feed this entity type. Each needs a primary key; change data feed
              enables incremental ingest (otherwise Rezolake falls back to a snapshot diff).
            </CardDescription>
          </div>
          <Button onClick={() => setEditing('new')}>
            <Plus className="h-4 w-4 mr-1" /> Add source
          </Button>
        </CardHeader>
        <CardContent>
          {draft.sources.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sources yet. Add the first one to propose attributes.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Table</TableHead>
                  <TableHead>Primary key</TableHead>
                  <TableHead>Mapped</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Active</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {draft.sources.map((s, i) => {
                  const mapped = Object.values(s.mappings).filter((v) => v !== EXT && v !== IGNORE).length;
                  return (
                    <TableRow key={s.source_id}>
                      <TableCell className="font-medium">{s.name}</TableCell>
                      <TableCell className="font-mono text-xs">{s.table}</TableCell>
                      <TableCell className="font-mono text-xs">{s.primary_key.join(', ')}</TableCell>
                      <TableCell>{mapped} columns</TableCell>
                      <TableCell>{s.priority}</TableCell>
                      <TableCell>
                        <Switch
                          checked={s.active}
                          onCheckedChange={(v) =>
                            update({ ...draft, sources: draft.sources.map((x, j) => (j === i ? { ...x, active: v } : x)) })
                          }
                        />
                      </TableCell>
                      <TableCell className="text-right space-x-1">
                        <Button size="sm" variant="outline" onClick={() => setEditing(s)}>
                          Edit mapping
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => update({ ...draft, sources: draft.sources.filter((_, j) => j !== i) })}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AttributesCard draft={draft} onChange={update} />

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={onSave} disabled={busy || !dirty}>
          Save draft
        </Button>
        <Button onClick={onPublish} disabled={busy || problems.length > 0}>
          Publish definition v{(entityType.published_version ?? 0) + 1}
        </Button>
        {dirty && <span className="text-sm text-muted-foreground">Unsaved changes</span>}
        {msg && <span className={`text-sm ${msg.ok ? 'text-muted-foreground' : 'text-destructive'}`}>{msg.text}</span>}
      </div>
      {problems.length > 0 && (
        <Alert>
          <AlertDescription>
            <ul className="list-disc pl-5">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      <p className="text-xs text-muted-foreground">
        Publishing freezes this draft as an immutable definition version (Delta). Published attribute ids never change,
        so later renames keep mappings, strategies and history intact.
      </p>

      {editing && (
        <SourceDialog
          initial={editing === 'new' ? undefined : editing}
          draft={draft}
          onClose={() => setEditing(null)}
          onSave={(src, attrs) => {
            const others = draft.sources.filter((x) => x.source_id !== (editing === 'new' ? src.source_id : editing.source_id));
            update({ attributes: attrs, sources: [...others, src] });
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function AttributesCard({ draft, onChange }: { draft: DefinitionDraft; onChange: (d: DefinitionDraft) => void }) {
  const [newName, setNewName] = useState('');
  const set = (i: number, patch: Partial<Attribute>) =>
    onChange({ ...draft, attributes: draft.attributes.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const usedBy = (aid: string) =>
    draft.sources.filter((s) => Object.values(s.mappings).includes(aid)).map((s) => s.source_id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Canonical attributes</CardTitle>
        <CardDescription>
          The entity type&apos;s intentional schema. Matching attributes feed the match representation; survivorship picks
          each golden value; standardize fixes casing across sources before matching and survivorship.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {draft.attributes.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Display name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Semantic</TableHead>
                <TableHead>Match</TableHead>
                <TableHead>Show</TableHead>
                <TableHead>Survivorship</TableHead>
                <TableHead>Standardize</TableHead>
                <TableHead>Sources</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {draft.attributes.map((a, i) => (
                <TableRow key={a.attribute_id}>
                  <TableCell className="font-mono text-xs">{a.name}</TableCell>
                  <TableCell>
                    <Input value={a.display_name} onChange={(e) => set(i, { display_name: e.target.value })} className="h-8" />
                  </TableCell>
                  <TableCell>
                    <SmallSelect value={a.data_type} options={DTYPES} onChange={(v) => set(i, { data_type: v as Attribute['data_type'] })} />
                  </TableCell>
                  <TableCell>
                    <SmallSelect value={a.semantic_type} options={SEMANTIC} onChange={(v) => set(i, { semantic_type: v as Attribute['semantic_type'] })} />
                  </TableCell>
                  <TableCell>
                    <Checkbox checked={a.matching} onCheckedChange={(v) => set(i, { matching: v === true })} />
                  </TableCell>
                  <TableCell>
                    <Checkbox checked={a.display} onCheckedChange={(v) => set(i, { display: v === true })} />
                  </TableCell>
                  <TableCell>
                    <SmallSelect
                      value={a.survivorship.rule}
                      options={RULES}
                      onChange={(v) => set(i, { survivorship: { rule: v as Attribute['survivorship']['rule'] } })}
                    />
                  </TableCell>
                  <TableCell>
                    <SmallSelect value={a.standardize ?? 'none'} options={CASING} onChange={(v) => set(i, { standardize: v as Attribute['standardize'] })} />
                  </TableCell>
                  <TableCell className="text-xs">{usedBy(a.attribute_id).join(', ') || '—'}</TableCell>
                  <TableCell>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={usedBy(a.attribute_id).length > 0}
                      title="Unmap it from all sources first"
                      onClick={() => onChange({ ...draft, attributes: draft.attributes.filter((_, j) => j !== i) })}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex gap-2 max-w-md">
          <Input placeholder="new attribute name" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Button
            variant="outline"
            disabled={!newName.trim()}
            onClick={() => {
              onChange({ ...draft, attributes: [...draft.attributes, newAttribute(newName, 'string', draft.attributes)] });
              setNewName('');
            }}
          >
            Add attribute
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SmallSelect({ value, options, onChange }: { value: string; options: readonly string[]; onChange: (v: string) => void }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="h-8 min-w-28">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o} value={o}>
            {o.replace(/_/g, ' ').toLowerCase()}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SourceDialog({
  initial,
  draft,
  onClose,
  onSave,
}: {
  initial?: SourceDef;
  draft: DefinitionDraft;
  onClose: () => void;
  onSave: (s: SourceDef, attributes: Attribute[]) => void;
}) {
  const [catalog, setCatalog] = useState(initial?.table.split('.')[0] ?? '');
  const [schema, setSchema] = useState(initial?.table.split('.')[1] ?? '');
  const [table, setTable] = useState(initial?.table.split('.')[2] ?? '');
  const catalogs = useApi<string[]>('/api/uc/catalogs');
  const schemas = useApi<string[]>(catalog ? `/api/uc/schemas?catalog=${encodeURIComponent(catalog)}` : null);
  const tables = useApi<{ name: string }[]>(
    catalog && schema ? `/api/uc/tables?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}` : null,
  );
  const full = catalog && schema && table ? `${catalog}.${schema}.${table}` : '';
  const info = useApi<TableInfo>(full ? `/api/uc/table?name=${encodeURIComponent(full)}` : null);

  const [sourceId, setSourceId] = useState(initial?.source_id ?? '');
  const [name, setName] = useState(initial?.name ?? '');
  const [priority, setPriority] = useState(initial?.priority ?? (draft.sources.length + 1) * 10);
  const [pk, setPk] = useState<string[]>(initial?.primary_key ?? []);
  const [mapping, setMapping] = useState<Record<string, string>>(initial?.mappings ?? {});

  // When a table is chosen for a new source: derive id, detect PK, propose mappings.
  // (Seeding editable form state from an async fetch is the intended use here.)
  useEffect(() => {
    if (!info.data || initial) return;
    const cols = info.data.columns;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSourceId(toIdent(table).slice(0, 40));
    setName(table.replace(/_/g, ' '));
    const pkGuess = cols.find((c) => /^(id|.*_id|.*_no|.*key|.*number)$/i.test(c.name) && !/(^|_)(email)/i.test(c.name));
    setPk(pkGuess ? [pkGuess.name] : []);
    const m: Record<string, string> = {};
    for (const c of cols) {
      if (pkGuess && c.name === pkGuess.name) continue;
      if (c.name.startsWith('_')) {
        m[c.name] = IGNORE;
        continue;
      }
      m[c.name] = suggestAttribute(c.name, draft.attributes) ?? (draft.attributes.length === 0 ? NEW : EXT);
    }
    setMapping(m);
  }, [info.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const idTaken = !initial && draft.sources.some((s) => s.source_id === sourceId);
  const valid = full && sourceId && /^[a-z0-9_-]+$/.test(sourceId) && pk.length > 0 && !idTaken;

  function save() {
    if (!info.data) return;
    const attrs = [...draft.attributes];
    const finalMap: Record<string, string> = {};
    for (const [col, target] of Object.entries(mapping)) {
      if (pk.includes(col) && target === NEW) continue;
      if (target === NEW) {
        const type = info.data.columns.find((c) => c.name === col)?.type ?? 'string';
        const a = newAttribute(col, type, attrs);
        attrs.push(a);
        finalMap[col] = a.attribute_id;
      } else {
        finalMap[col] = target;
      }
    }
    onSave(
      { source_id: sourceId, name: name || sourceId, table: full, primary_key: pk, mappings: finalMap, priority, active: initial?.active ?? true },
      attrs,
    );
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[95vw] sm:max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{initial ? `Source: ${initial.name}` : 'Add source'}</DialogTitle>
          <DialogDescription>Pick a Unity Catalog table, its primary key, and how each column maps to the entity&apos;s attributes.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <UcSelect label="Catalog" value={catalog} items={catalogs.data} loading={catalogs.loading}
              onChange={(v) => { setCatalog(v); setSchema(''); setTable(''); }} disabled={!!initial} />
            <UcSelect label="Schema" value={schema} items={schemas.data} loading={schemas.loading}
              onChange={(v) => { setSchema(v); setTable(''); }} disabled={!catalog || !!initial} />
            <UcSelect label="Table" value={table} items={tables.data?.map((t) => t.name)} loading={tables.loading}
              onChange={setTable} disabled={!schema || !!initial} />
          </div>
          {catalogs.error && <ErrorNote error={catalogs.error} title="Unity Catalog browse failed" />}
          {info.loading && full && <Loading rows={2} />}
          {info.error && <ErrorNote error={info.error} title="Could not read table" />}
          {info.data && (
            <>
              {!info.data.cdf_enabled && (
                <Alert>
                  <AlertDescription>
                    Change data feed is off on this table. Ingest will use a full snapshot diff each run. Rezolake never
                    alters your source; ask its owner to set <code>delta.enableChangeDataFeed = true</code> for incremental
                    ingest.
                  </AlertDescription>
                </Alert>
              )}
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="space-y-1">
                  <Label>Source id</Label>
                  <Input value={sourceId} onChange={(e) => setSourceId(toIdent(e.target.value))} disabled={!!initial} />
                  {idTaken && <p className="text-xs text-destructive">id already used</p>}
                </div>
                <div className="space-y-1">
                  <Label>Display name</Label>
                  <Input value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label>Priority (lower wins survivorship)</Label>
                  <Input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} />
                </div>
              </div>
              <SourcePreview table={full} />
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Column</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Primary key</TableHead>
                    <TableHead>Maps to</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {info.data.columns.map((c) => (
                    <TableRow key={c.name}>
                      <TableCell className="font-mono text-xs">{c.name}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{c.type}</TableCell>
                      <TableCell>
                        <Checkbox
                          checked={pk.includes(c.name)}
                          onCheckedChange={(v) => setPk(v === true ? [...pk, c.name] : pk.filter((x) => x !== c.name))}
                        />
                      </TableCell>
                      <TableCell>
                        <Select value={mapping[c.name] ?? (pk.includes(c.name) ? IGNORE : EXT)}
                          onValueChange={(v) => setMapping({ ...mapping, [c.name]: v })}>
                          <SelectTrigger className="h-8 min-w-48">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {draft.attributes.map((a) => (
                              <SelectItem key={a.attribute_id} value={a.attribute_id}>
                                → {a.display_name}
                              </SelectItem>
                            ))}
                            <SelectItem value={NEW}>+ new canonical attribute</SelectItem>
                            <SelectItem value={EXT}>keep as source extension</SelectItem>
                            <SelectItem value={IGNORE}>ignore</SelectItem>
                          </SelectContent>
                        </Select>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="text-xs text-muted-foreground">
                Suggestions use exact names and common synonyms. Unmapped columns are kept as source-namespaced extension
                attributes; the full raw payload is always retained in the source ledger.
              </p>
            </>
          )}
        </div>
        <DialogFooter>
          {pk.length === 0 && info.data && <Badge variant="destructive">choose a primary key</Badge>}
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!valid} onClick={save}>
            {initial ? 'Update source' : 'Add source'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function UcSelect({ label, value, items, loading, onChange, disabled }: {
  label: string; value: string; items?: string[]; loading: boolean; onChange: (v: string) => void; disabled?: boolean;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger>
          <SelectValue placeholder={loading ? 'Loading…' : `Select ${label.toLowerCase()}`} />
        </SelectTrigger>
        <SelectContent>
          {(items ?? (value ? [value] : [])).map((i) => (
            <SelectItem key={i} value={i}>
              {i}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

const cellText = (v: unknown) => (v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v as string));

function SourcePreview({ table }: { table: string }) {
  const params = useMemo(() => ({ source_table: sql.string(table) }), [table]);
  const { data, loading, error } = useAnalyticsQuery('source_preview', params);
  // row_json may arrive already parsed (AppKit auto-parses JSON-looking strings) or as text.
  const rows = useMemo(() => (data ?? []).map((r) => parseJson<Record<string, unknown>>(r.row_json, {})), [data]);
  if (loading) return <Loading rows={2} />;
  if (error) return <ErrorNote error={error} title="Preview unavailable (read as you)" />;
  if (!rows.length) return <p className="text-sm text-muted-foreground">Table is empty.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="border rounded-md overflow-x-auto max-h-56">
      <Table>
        <TableHeader>
          <TableRow>
            {cols.map((c) => (
              <TableHead key={c} className="text-xs">{c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, 5).map((r, i) => (
            <TableRow key={i}>
              {cols.map((c) => (
                <TableCell key={c} className="text-xs whitespace-nowrap">{cellText(r[c])}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
