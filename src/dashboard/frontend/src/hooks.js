import { useState, useEffect, useCallback } from 'react';

export function useAPI(fetcher, deps = [], interval = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const result = await fetcher();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, deps);

  useEffect(() => {
    load();
    if (interval) {
      const id = setInterval(load, interval);
      return () => clearInterval(id);
    }
  }, [load, interval]);

  return { data, loading, error, reload: load };
}
