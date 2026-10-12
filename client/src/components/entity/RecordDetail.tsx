// Details of one record picked on the graph: its values beside the golden record (members), or the
// entity it belongs to (records outside this entity).
import { useMemo } from 'react';
import { Link } from 'react-router';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, useAnalyticsQuery } from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { Check, ExternalLink, Pin, Scissors, UserMinus, X } from 'lucide-react';
import { isTrue, parseJson, shortId, type Attribute } from '../../lib/api';
import { ErrorNote, Loading } from '../states';
import type { GoldenRow, MemberRow } from './CompareTable';
import type { OnCommand } from './types';

const norm = (v: string | null | undefined) => (v == null ? '' : v.trim().toLowerCase());

export function RecordDetail({ recordId, entityId, entityTypeId, catalog, attrs, golden, members, onCommand, onClose }: {
  recordId: string;
  entityId: string;
  entityTypeId: string;
  catalog: string;
  attrs: Attribute[];
  golden: GoldenRow[];
  members: MemberRow[];
  onCommand: OnCommand;
  onClose: () => void;
}) {
  const member = members.find((m) => m.record_id === recordId);
  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-2 space-y-0 pb-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base break-words">
            {member ? <>{member.source_id} <span className="font-normal text-muted-foreground">{member.source_key}</span></> : `Record ${shortId(recordId)}`}
          </CardTitle>
          <div className="mt-1 flex flex-wrap gap-1">
            {member && isTrue(member.is_anchor) && <Badge variant="outline">anchor</Badge>}
            {!member && <Badge variant="secondary">in another entity</Badge>}
            <Badge variant="outline" className="font-mono">{shortId(recordId)}</Badge>
          </div>
        </div>
        <Button size="icon" variant="ghost" className="h-7 w-7" aria-label="Close record details" onClick={onClose}><X className="h-4 w-4" /></Button>
      </CardHeader>
      <CardContent>
        {member ? (
          <MemberDetail member={member} entityId={entityId} attrs={attrs} golden={golden} onlyMember={members.length === 1} onCommand={onCommand} />
        ) : (
          <OutsideDetail recordId={recordId} entityTypeId={entityTypeId} catalog={catalog} />
        )}
      </CardContent>
    </Card>
  );
}

function MemberDetail({ member, entityId, attrs, golden, onlyMember, onCommand }: {
  member: MemberRow; entityId: string; attrs: Attribute[]; golden: GoldenRow[]; onlyMember: boolean; onCommand: OnCommand;
}) {
  const values = parseJson<Record<string, string | null>>(member.attributes_json, {});
  const byId = new Map(golden.map((g) => [g.attribute_id, g]));
  return (
    <div className="space-y-3">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th className="py-1 pr-2 text-left font-normal w-28">Attribute</th>
            <th className="py-1 pr-2 text-left font-normal">This record</th>
            <th className="py-1 text-left font-normal">Golden</th>
          </tr>
        </thead>
        <tbody>
          {attrs.map((a) => {
            const v = values[a.attribute_id];
            const g = byId.get(a.attribute_id);
            const differs = !!v && norm(v) !== norm(g?.value);
            return (
              <tr key={a.attribute_id} className="border-t align-top">
                <td className="py-1 pr-2 text-muted-foreground">{a.display_name}</td>
                <td className={`py-1 pr-2 break-words ${differs ? 'bg-warning/10' : ''}`}>
                  <span className="line-clamp-4" title={v ?? undefined}>{v || <span className="text-muted-foreground">—</span>}</span>
                  {g?.winning_record_id === member.record_id && <Check className="inline h-3.5 w-3.5 ml-1 text-success" aria-label="golden source" />}
                </td>
                <td className="py-1 break-words text-muted-foreground"><span className="line-clamp-4" title={g?.value ?? undefined}>{g?.value ?? '—'}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => onCommand('PIN_GOLDEN_RECORD', { entity_id: entityId, record_id: member.record_id }, 'Pin golden record')}>
          <Pin className="h-4 w-4 mr-1" />Use as golden source
        </Button>
        <Button size="sm" variant="outline" disabled={onlyMember}
          onClick={() => onCommand('SPLIT_ENTITY', { entity_id: entityId, record_ids: [member.record_id], reason: 'steward split (graph)' }, 'Split')}>
          <Scissors className="h-4 w-4 mr-1" />Split into its own entity
        </Button>
        <Button size="sm" variant="ghost" disabled={onlyMember}
          onClick={() => onCommand('ISOLATE_RECORD', { record_id: member.record_id, reason: 'removed by steward (graph)' }, 'Remove record')}>
          <UserMinus className="h-4 w-4 mr-1" />Remove
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Split keeps the record linkable to others later; remove isolates it so it never re-joins an entity automatically.
      </p>
    </div>
  );
}

function OutsideDetail({ recordId, entityTypeId, catalog }: { recordId: string; entityTypeId: string; catalog: string }) {
  // entities_page search matches record ids through the crosswalk.
  const params = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityTypeId), search: sql.string(recordId),
    source_id: sql.string(''), size: sql.string('all'), review: sql.string('all'), sort: sql.string('size'),
    limit: sql.int(1), offset: sql.int(0),
  }), [catalog, entityTypeId, recordId]);
  const { data, loading, error } = useAnalyticsQuery('entities_page', params);
  if (loading) return <Loading rows={2} />;
  if (error) return <ErrorNote error={error} title="Could not find this record's entity" />;
  const other = data?.[0];
  if (!other) return <p className="text-sm text-muted-foreground">This record is not in any entity right now (it may have been deleted at its source).</p>;
  return (
    <div className="space-y-2 text-sm">
      <p>
        Belongs to <b className="break-words">{other.display_name}</b>{' '}
        <span className="font-mono text-xs text-muted-foreground">{shortId(other.entity_id)}</span> · {other.member_count} records
      </p>
      <Button asChild size="sm" variant="outline">
        <Link to={`/t/${entityTypeId}/entities?e=${encodeURIComponent(other.entity_id)}`}><ExternalLink className="h-4 w-4 mr-1" />Open that entity</Link>
      </Button>
    </div>
  );
}
