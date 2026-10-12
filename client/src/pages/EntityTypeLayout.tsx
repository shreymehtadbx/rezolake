import { NavLink, Outlet, useParams } from 'react-router';
import { Badge } from '@databricks/appkit-ui/react';
import type { EntityType } from '../lib/api';
import { useApi } from '../lib/hooks';
import { ErrorNote, Loading, StatusPill } from '../components/states';

const tabs = [
  { to: '', label: 'Overview', end: true },
  { to: 'definition', label: 'Definition' },
  { to: 'strategy', label: 'Strategy' },
  { to: 'runs', label: 'Runs' },
  { to: 'review', label: 'Review queue' },
  { to: 'entities', label: 'Entities' },
  { to: 'activity', label: 'Steward activity' },
];

export interface EntityTypeOutlet {
  entityType: EntityType;
  refresh: () => Promise<EntityType | undefined>;
}

export function EntityTypeLayout() {
  const { id } = useParams();
  const { data, error, loading, refresh } = useApi<EntityType>(`/api/entity-types/${id}`);

  if (loading && !data) return <Loading rows={6} />;
  if (error || !data) return <ErrorNote error={error ?? 'not found'} title="Could not load entity type" />;

  return (
    <div className="max-w-7xl mx-auto space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-2xl font-semibold">{data.name}</h2>
        <StatusPill value={data.status} />
        <Badge variant="outline">{data.entity_kind}</Badge>
        {data.published_version && <Badge variant="secondary">definition v{data.published_version}</Badge>}
        {data.strategy_version && <Badge variant="secondary">strategy v{data.strategy_version}</Badge>}
      </div>
      <nav className="flex flex-wrap gap-1 border-b">
        {tabs.map((t) => (
          <NavLink
            key={t.label}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              `px-3 py-2 text-sm font-medium border-b-2 -mb-px ${
                isActive ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet context={{ entityType: data, refresh } satisfies EntityTypeOutlet} />
    </div>
  );
}
