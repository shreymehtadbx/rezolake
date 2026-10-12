import { Alert, AlertDescription, AlertTitle, Empty, EmptyDescription, EmptyHeader, EmptyTitle, Skeleton } from '@databricks/appkit-ui/react';
import type { ReactNode } from 'react';

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-8 w-full" />
      ))}
    </div>
  );
}

export function ErrorNote({ error, title = 'Something went wrong' }: { error: unknown; title?: string }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="break-all">{msg}</AlertDescription>
    </Alert>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Empty className="border rounded-lg py-10">
      <EmptyHeader>
        <EmptyTitle>{title}</EmptyTitle>
        {children && <EmptyDescription>{children}</EmptyDescription>}
      </EmptyHeader>
    </Empty>
  );
}

const TONE: Record<string, string> = {
  SUCCEEDED: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  APPLIED: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  PUBLISHED: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  LINK: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  MUST_LINK: 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-200',
  RUNNING: 'bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-200',
  PENDING: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
  DRAFT: 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200',
  REVIEW: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
  UNSURE: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
  FAILED: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200',
  CANCELLED: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200',
  NO_LINK: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200',
  CANNOT_LINK: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200',
  ISOLATE: 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200',
};

export function StatusPill({ value }: { value: string | null | undefined }) {
  const v = value ?? '—';
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${TONE[v] ?? 'bg-muted text-muted-foreground'}`}>
      {v.replace(/_/g, ' ')}
    </span>
  );
}
