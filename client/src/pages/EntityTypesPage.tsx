import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
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
  Textarea,
} from '@databricks/appkit-ui/react';
import { Plus } from 'lucide-react';
import { api, fmtTime, type EntityKind, type EntityType } from '../lib/api';
import { useApi } from '../lib/hooks';
import { EmptyState, ErrorNote, Loading, StatusPill } from '../components/states';

export function EntityTypesPage() {
  const { data, error, loading } = useApi<EntityType[]>('/api/entity-types');
  const [open, setOpen] = useState(false);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold">Entity types</h2>
          <p className="text-sm text-muted-foreground">
            Each entity type (customer, supplier, product, …) has a published definition, sources and a resolution
            strategy.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4 mr-1" /> New entity type
        </Button>
      </div>

      {loading && <Loading rows={4} />}
      {error && <ErrorNote error={error} title="Could not load entity types" />}
      {data && data.length === 0 && (
        <EmptyState title="No entity types yet">
          Create one, pick its source tables in Unity Catalog, and publish a definition.
        </EmptyState>
      )}
      {data && data.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((t) => (
            <Link key={t.id} to={`/t/${t.id}`} className="block">
              <Card className="h-full hover:border-primary transition-colors">
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-lg">{t.name}</CardTitle>
                    <StatusPill value={t.status} />
                  </div>
                  <CardDescription>
                    {t.entity_kind} · {t.published_version ? `definition v${t.published_version}` : 'not published'}
                  </CardDescription>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground space-y-1">
                  {t.description && <p className="line-clamp-2">{t.description}</p>}
                  <p>
                    Last run:{' '}
                    {t.last_run ? (
                      <>
                        {t.last_run.mode} <StatusPill value={t.last_run.status} /> {fmtTime(t.last_run.created_at)}
                      </>
                    ) : (
                      'never'
                    )}
                  </p>
                  {!!t.pending_commands && <p>{t.pending_commands} steward action(s) processing</p>}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
      <CreateDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}

function CreateDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [kind, setKind] = useState<EntityKind>('people');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    setError(undefined);
    try {
      const t = await api<EntityType>('/api/entity-types', {
        method: 'POST',
        json: { name, entity_kind: kind, description },
      });
      onOpenChange(false);
      void navigate(`/t/${t.id}/definition`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New entity type</DialogTitle>
          <DialogDescription>Name the kind of entity to master; you&apos;ll add sources and attributes next.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="et-name">Name</Label>
            <Input id="et-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Customer" />
          </div>
          <div className="space-y-1">
            <Label>Kind (selects the matcher&apos;s domain rules)</Label>
            <Select value={kind} onValueChange={(v) => setKind(v as EntityKind)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="people">People</SelectItem>
                <SelectItem value="companies">Companies</SelectItem>
                <SelectItem value="products">Products</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="et-desc">Description (given to the LLM matcher as domain context)</Label>
            <Textarea id="et-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          {error && <ErrorNote error={error} title="Could not create" />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={create} disabled={!name.trim() || busy}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
