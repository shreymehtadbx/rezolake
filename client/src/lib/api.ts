// Thin client for the app's Lakebase-backed API (/api/...). Warehouse reads use useAnalyticsQuery.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    headers: { ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await res.text();
  const body = text ? (JSON.parse(text) as unknown) : undefined;
  if (!res.ok) {
    const msg = (body as { error?: string } | undefined)?.error ?? `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, msg);
  }
  return body as T;
}

export type EntityKind = 'people' | 'companies' | 'products' | 'other';

export interface Attribute {
  attribute_id: string;
  name: string;
  display_name: string;
  data_type: 'string' | 'integer' | 'float' | 'date' | 'boolean';
  semantic_type: 'text' | 'name' | 'email' | 'phone' | 'address' | 'id' | 'date' | 'number' | 'code';
  matching: boolean;
  display: boolean;
  survivorship: { rule: 'SOURCE_PRIORITY' | 'MOST_RECENT_NON_NULL' | 'MOST_COMMON' | 'LONGEST' };
  /** Casing applied to every source value at canonicalization. */
  standardize?: 'none' | 'title' | 'upper' | 'lower';
}

export interface SourceDef {
  source_id: string;
  name: string;
  table: string;
  primary_key: string[];
  mappings: Record<string, string>;
  priority: number;
  active: boolean;
}

export interface DefinitionDraft {
  attributes: Attribute[];
  sources: SourceDef[];
}

export interface RunSummary {
  run_id: string;
  mode: string;
  status: string;
  stage?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface EntityType {
  id: string;
  name: string;
  slug: string;
  entity_kind: EntityKind;
  description: string | null;
  status: 'DRAFT' | 'PUBLISHED';
  published_version: number | null;
  strategy_version: number | null;
  created_at: string;
  updated_at: string;
  last_run?: RunSummary | null;
  pending_commands?: number;
  definition?: DefinitionDraft;
  strategy?: Record<string, unknown>;
}

export interface RunRequest extends RunSummary {
  entity_type_id: string;
  progress: Record<string, unknown>;
  metrics: Record<string, unknown>;
  params: Record<string, unknown>;
  trigger_source: string;
  job_run_id: string | null;
  requested_by: string | null;
  error: string | null;
  started_at: string | null;
}

export interface Command {
  id: string;
  command_type: string;
  payload: Record<string, unknown>;
  status: 'PENDING' | 'RUNNING' | 'APPLIED' | 'FAILED';
  requested_by: string;
  requested_at: string;
  applied_at: string | null;
  error: string | null;
  run_id: string | null;
}

export interface ReviewNote {
  id: string;
  pair_id: string;
  verdict: 'UNSURE' | 'NOTE';
  flagged: boolean;
  note: string;
  author: string;
  created_at: string;
}

export interface TableInfo {
  full_name: string;
  table_type: string;
  cdf_enabled: boolean;
  columns: { name: string; type: string }[];
}

export const sendCommand = (entityTypeId: string, command_type: string, payload: Record<string, unknown>) =>
  api<{ command_id: string; run_id: string }>(`/api/entity-types/${entityTypeId}/commands`, {
    method: 'POST',
    json: { command_type, payload },
  });

/** JSON columns may arrive already parsed (objects) or as JSON text. */
export function parseJson<T = Record<string, unknown>>(s: unknown, fallback: T): T {
  if (s !== null && typeof s === 'object') return s as T;
  if (typeof s !== 'string' || !s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

export function fmtTime(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleString();
}

/** Array columns arrive as JSON arrays or JSON-encoded strings depending on the result format. */
export function asList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String);
  if (typeof v === 'string') return parseJson<unknown[]>(v, []).map(String);
  return [];
}

/** Warehouse booleans arrive as true/false or "true"/"false". */
export const isTrue = (v: unknown) => v === true || v === 'true';

export const shortId = (id: string | null | undefined) => (id ? id.slice(0, 8) : '—');
