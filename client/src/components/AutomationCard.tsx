import { useEffect, useState } from 'react';
import {
  Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label,
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue, Switch,
} from '@databricks/appkit-ui/react';
import { api, fmtTime } from '../lib/api';
import { useApi } from '../lib/hooks';
import { ErrorNote, Loading } from './states';

interface Policy {
  enabled: boolean;
  trigger_mode: 'table_update' | 'scheduled';
  cron?: string | null;
  timezone: string;
  wait_after_last_change_seconds: number;
  min_time_between_triggers_seconds: number;
  databricks_job_id?: number | null;
  job_error?: string | null;
  job_synced_at?: string | null;
}

export function AutomationCard({ entityTypeId, published }: { entityTypeId: string; published: boolean }) {
  const path = `/api/entity-types/${entityTypeId}/orchestration`;
  const { data, loading, error } = useApi<Policy>(path);
  const [p, setP] = useState<Policy>();
  const [busy, setBusy] = useState(false);
  const [saveErr, setSaveErr] = useState<string>();
  // Seed the editable form once the saved policy arrives.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (data) setP(data); }, [data]);

  async function save(next: Policy) {
    setBusy(true);
    setSaveErr(undefined);
    try {
      setP(await api<Policy>(path, { method: 'PUT', json: next }));
    } catch (e) {
      setSaveErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading || !p) return <Loading rows={2} />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Automatic orchestration</CardTitle>
        <CardDescription>
          When enabled, a small trigger job watches the active source tables (or a cron schedule) and runs the engine
          incrementally. Runs queue one at a time, so bursts of changes coalesce.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <ErrorNote error={error} />}
        <div className="flex items-center gap-2">
          <Switch checked={p.enabled} disabled={!published || busy} onCheckedChange={(v) => void save({ ...p, enabled: v })} />
          <span className="text-sm">{p.enabled ? 'Enabled' : 'Disabled'}</span>
          {p.databricks_job_id && <span className="text-xs text-muted-foreground">trigger job {p.databricks_job_id} · synced {fmtTime(p.job_synced_at)}</span>}
        </div>
        <div className="grid gap-3 md:grid-cols-4">
          <div className="space-y-1">
            <Label>Trigger</Label>
            <Select value={p.trigger_mode} onValueChange={(v) => setP({ ...p, trigger_mode: v as Policy['trigger_mode'] })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="table_update">When source tables change</SelectItem>
                <SelectItem value="scheduled">On a schedule (cron)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {p.trigger_mode === 'table_update' ? (
            <>
              <div className="space-y-1">
                <Label>Settle time (s)</Label>
                <Input type="number" min={60} value={p.wait_after_last_change_seconds}
                  onChange={(e) => setP({ ...p, wait_after_last_change_seconds: Number(e.target.value) })} />
              </div>
              <div className="space-y-1">
                <Label>Min gap between runs (s)</Label>
                <Input type="number" min={60} value={p.min_time_between_triggers_seconds}
                  onChange={(e) => setP({ ...p, min_time_between_triggers_seconds: Number(e.target.value) })} />
              </div>
            </>
          ) : (
            <>
              <div className="space-y-1">
                <Label>Quartz cron</Label>
                <Input placeholder="0 0 * * * ?" value={p.cron ?? ''} onChange={(e) => setP({ ...p, cron: e.target.value })} />
              </div>
              <div className="space-y-1">
                <Label>Timezone</Label>
                <Input value={p.timezone} onChange={(e) => setP({ ...p, timezone: e.target.value })} />
              </div>
            </>
          )}
          <div className="flex items-end">
            <Button variant="outline" disabled={busy || !published} onClick={() => void save(p)}>Save</Button>
          </div>
        </div>
        {p.job_error && <ErrorNote error={p.job_error} title="Trigger job could not be configured" />}
        {saveErr && <ErrorNote error={saveErr} title="Save failed" />}
      </CardContent>
    </Card>
  );
}
