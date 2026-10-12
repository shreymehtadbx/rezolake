import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

/** `value`, updated only after it has stopped changing for `ms` (e.g. search-as-you-type). */
export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

/** Fetch a JSON API path; `refresh()` re-fetches; optional polling while `pollWhile(data)` is true. */
export function useApi<T>(path: string | null, opts?: { pollMs?: number; pollWhile?: (d: T) => boolean }) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState<boolean>(!!path);
  const [nonce, setNonce] = useState(0);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const load = useCallback(async (): Promise<T | undefined> => {
    if (!path) return undefined;
    try {
      const d = await api<T>(path);
      setData(d);
      setError(undefined);
      return d;
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    setLoading(!!path);
    const tick = async () => {
      const d = await load();
      const o = optsRef.current;
      if (alive && o?.pollMs && d !== undefined && (!o.pollWhile || o.pollWhile(d))) {
        timer = setTimeout(() => void tick(), o.pollMs);
      }
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [load, nonce]);

  /** Re-fetch and resume polling (e.g. after starting a new run). */
  const restart = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, refresh: load, restart, setData };
}
