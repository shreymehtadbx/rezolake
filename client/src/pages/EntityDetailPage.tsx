import { Link, useOutletContext, useParams } from 'react-router';
import { Button } from '@databricks/appkit-ui/react';
import { ArrowLeft } from 'lucide-react';
import { useConfig } from '../lib/context';
import { EntityView } from '../components/entity/EntityView';
import type { EntityTypeOutlet } from './EntityTypeLayout';

export function EntityDetailPage() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { entityId = '' } = useParams();
  const { catalog } = useConfig();
  return (
    <div className="space-y-3">
      <Button asChild variant="ghost" size="sm">
        <Link to={`/t/${entityType.id}/entities?e=${encodeURIComponent(entityId)}`}><ArrowLeft className="h-4 w-4 mr-1" />Entity explorer</Link>
      </Button>
      <EntityView key={entityId} entityType={entityType} catalog={catalog} entityId={entityId} />
    </div>
  );
}
