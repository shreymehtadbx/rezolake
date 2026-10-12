import { useOutletContext } from 'react-router';
import { Card, CardContent, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@databricks/appkit-ui/react';
import { fmtTime, shortId, type Command } from '../lib/api';
import { useApi } from '../lib/hooks';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../components/states';
import type { EntityTypeOutlet } from './EntityTypeLayout';

export function ActivityTab() {
  const { entityType } = useOutletContext<EntityTypeOutlet>();
  const { data, loading, error } = useApi<Command[]>(`/api/entity-types/${entityType.id}/commands`, {
    pollMs: 5000,
    pollWhile: (d) => d.some((c) => c.status === 'PENDING' || c.status === 'RUNNING'),
  });
  if (loading) return <Loading rows={4} />;
  if (error) return <ErrorNote error={error} />;
  if (!data?.length) return <EmptyState title="No steward actions yet" />;
  return (
    <Card>
      <CardContent className="pt-6">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Who</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Details</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="text-xs whitespace-nowrap">{fmtTime(c.requested_at)}</TableCell>
                <TableCell className="text-xs">{c.requested_by}</TableCell>
                <TableCell className="text-sm">{c.command_type.replace(/_/g, ' ').toLowerCase()}</TableCell>
                <TableCell className="text-xs font-mono max-w-md truncate" title={JSON.stringify(c.payload)}>
                  {Object.entries(c.payload).map(([k, v]) => `${k}=${Array.isArray(v) ? v.map((x) => shortId(String(x))).join('|') : typeof v === 'string' && v.length > 20 ? shortId(v) : String(v)}`).join(' ')}
                </TableCell>
                <TableCell>
                  <StatusPill value={c.status} />
                  {c.error && <div className="text-xs text-destructive">{c.error}</div>}
                </TableCell>
                <TableCell className="text-xs font-mono">{shortId(c.run_id)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
