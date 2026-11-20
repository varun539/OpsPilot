"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage } from "@/lib/api/client";

interface ResourceState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Small fetch-on-mount/refresh hook with abort support.
 *
 * `key` identifies the request parameters; change it to refetch.
 */
export function useApiResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
) {
  const [state, setState] = useState<ResourceState<T>>({
    data: null,
    error: null,
    loading: true,
  });
  const [counter, setCounter] = useState(0);
  const loaderRef = useRef(load);

  useEffect(() => {
    loaderRef.current = load;
  });

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((current) => ({ ...current, loading: true, error: null }));
    loaderRef
      .current(controller.signal)
      .then((data) => {
        if (active) setState({ data, error: null, loading: false });
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        setState({ data: null, error: errorMessage(error), loading: false });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [key, counter]);

  const refresh = useCallback(() => setCounter((value) => value + 1), []);

  return { ...state, refresh };
}
