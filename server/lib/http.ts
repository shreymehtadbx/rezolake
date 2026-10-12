import type { Request, Response } from 'express';
import { createWorkspaceClient } from '@databricks/appkit';

export function currentUser(req: Request): string {
  return (
    req.header('x-forwarded-email') ??
    req.header('x-forwarded-user') ??
    process.env.REZOLAKE_DEV_USER ??
    'local-dev'
  );
}

export function handle(fn: (req: Request, res: Response) => Promise<unknown>) {
  return async (req: Request, res: Response) => {
    try {
      const out = await fn(req, res);
      if (!res.headersSent) res.json(out ?? { ok: true });
    } catch (err) {
      const e = err as Error & { status?: number };
      console.error(`[api] ${req.method} ${req.path}:`, e.message);
      if (!res.headersSent) res.status(e.status ?? 500).json({ error: e.message });
    }
  };
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let spClient: ReturnType<typeof createWorkspaceClient> | undefined;

/**
 * Call a Databricks REST API. Uses the signed-in user's token when the Apps proxy
 * forwards one (so UC permissions apply), otherwise the app/dev credentials.
 */
export async function databricksGet<T>(req: Request, path: string): Promise<T> {
  const userToken = req.header('x-forwarded-access-token');
  let host = process.env.DATABRICKS_HOST ?? '';
  const headers = new Headers();
  if (userToken) {
    headers.set('Authorization', `Bearer ${userToken}`);
  } else {
    spClient ??= createWorkspaceClient();
    await spClient.config.authenticate(headers);
    host = (await spClient.config.getHost()).toString();
  }
  if (!host.startsWith('http')) host = `https://${host}`;
  const res = await fetch(`${host.replace(/\/$/, '')}${path}`, { headers });
  if (!res.ok) throw new HttpError(res.status, `${path}: ${res.status} ${(await res.text()).slice(0, 300)}`);
  return (await res.json()) as T;
}

/** GET a paginated Databricks list API, following next_page_token until exhausted. */
export async function databricksList<T>(req: Request, path: string, key: string, maxPages = 50): Promise<T[]> {
  const out: T[] = [];
  let token: string | undefined;
  for (let page = 0; page < maxPages; page++) {
    const sep = path.includes('?') ? '&' : '?';
    const url = token ? `${path}${sep}page_token=${encodeURIComponent(token)}` : path;
    const r = await databricksGet<Record<string, unknown> & { next_page_token?: string }>(req, url);
    out.push(...((r[key] as T[] | undefined) ?? []));
    token = r.next_page_token;
    if (!token) break;
  }
  return out;
}
