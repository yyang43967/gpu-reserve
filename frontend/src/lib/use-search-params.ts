import { useCallback, useEffect, useRef } from "react";
import { useSearchParams, type NavigateOptions } from "react-router";

/**
 * Like useSearchParams, but updates always build on the newest params.
 *
 * React Router passes the params from the last render to a functional
 * setSearchParams, so two updates before a re-render both start from the same
 * stale params and the second drops the first. Here each update builds on the
 * last params this hook navigated to, until the router has rendered them.
 */
export function useLatestSearchParams() {
  const [searchParams, setSearchParams] = useSearchParams();
  // Query strings navigated to but not rendered yet, oldest first.
  const inFlight = useRef<string[]>([]);

  useEffect(() => {
    const rendered = searchParams.toString();
    const index = inFlight.current.indexOf(rendered);
    // One of ours rendered: it and anything older are done. Anything else (Back,
    // a link) replaces whatever was in flight, so later updates build on it.
    inFlight.current = index === -1 ? [] : inFlight.current.slice(index + 1);
  }, [searchParams]);

  const update = useCallback(
    (build: (current: URLSearchParams) => URLSearchParams, options?: NavigateOptions) => {
      const latest = inFlight.current.at(-1) ?? searchParams.toString();
      const next = build(new URLSearchParams(latest));
      inFlight.current.push(next.toString());
      setSearchParams(next, options);
    },
    [searchParams, setSearchParams],
  );

  return [searchParams, update] as const;
}
