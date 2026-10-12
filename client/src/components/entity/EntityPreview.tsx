// Hover preview of an entity's member records (loaded only when the card opens).
import { useMemo } from 'react';
import { useAnalyticsQuery } from '@databricks/appkit-ui/react';
import { sql } from '@databricks/appkit-ui/js';
import { isTrue, parseJson, type Attribute } from '../../lib/api';
import { ErrorNote, Loading } from '../states';

export function EntityPreview({ catalog, entityTypeId, entityId, attrs }: {
  catalog: string;
  entityTypeId: string;
  entityId: string;
  attrs: Attribute[];
}) {
  const params = useMemo(() => ({
    catalog: sql.string(catalog), entity_type_id: sql.string(entityTypeId), entity_id: sql.string(entityId),
  }), [catalog, entityTypeId, entityId]);
  const { data, loading, error } = useAnalyticsQuery('entity_members', params);
  const shown = attrs.filter((a) => a.display).slice(0, 2);
  if (loading) return <Loading rows={2} />;
  if (error) return <ErrorNote error={error} title="Could not load records" />;
  if (!data?.length) return <p className="text-xs text-muted-foreground">No member records.</p>;
  return (
    <div className="space-y-1 text-xs">
      <div className="font-medium">{data.length} member record{data.length === 1 ? '' : 's'}</div>
      {data.slice(0, 6).map((m) => {
        const v = parseJson<Record<string, string | null>>(m.attributes_json, {});
        return (
          <div key={m.record_id} className="border-t pt-1">
            <span className="font-medium">{m.source_id}</span> <span className="text-muted-foreground">{m.source_key}</span>
            {isTrue(m.is_anchor) && <span className="text-muted-foreground"> · anchor</span>}
            <div className="text-muted-foreground truncate">{shown.map((a) => v[a.attribute_id]).filter(Boolean).join(' · ') || '—'}</div>
          </div>
        );
      })}
      {data.length > 6 && <div className="text-muted-foreground">+{data.length - 6} more</div>}
    </div>
  );
}
