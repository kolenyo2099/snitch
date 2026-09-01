import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One loader for every page. Three reasons this exists rather than a bare
 * `useEffect(() => api.x().then(setX))`:
 *
 *  - A rejected fetch has to become a visible error. Pages that only ever set state
 *    on success sit on "Loading…" forever when the API is down, or — worse, as the
 *    Settings page used to — render a confident page full of zeroes and "not
 *    configured" that reads as fact rather than as a lost connection.
 *  - StrictMode mounts every effect twice in development. Without a guard that is
 *    two of every request on every page load.
 *  - A response that arrives after the user navigated away must not set state.
 */
export function useApi<T>(fn: () => Promise<T>, deps: any[] = []) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const stable = useRef(fn); stable.current = fn;

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const v = await stable.current();
      if (mine === seq.current) { setData(v); setError(undefined); }
    } catch (e: any) {
      if (mine === seq.current) setError(e?.message || "Could not reach the API.");
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, deps);
  return { data, error, loading, reload, setData };
}
