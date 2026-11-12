import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, request } from "./api";
import { createPoller } from "./polling";
export function useResource<T>(
  path: string | null,
  interval = 10000,
  transport: typeof request = request,
  periodic = true,
  requestTimeoutMs = 15000,
) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const currentPath = useRef(path);
  currentPath.current = path;
  const resource = useRef<{
    path: string;
    poller: ReturnType<typeof createPoller<T>>;
  } | null>(null);
  const refresh = useCallback(
    () =>
      resource.current?.path === path
        ? resource.current.poller.refresh()
        : Promise.resolve(),
    [path],
  );
  useEffect(() => {
    setData(null);
    setLoading(true);
    setError("");
    if (!path) return;
    const poller = createPoller<T>({
      interval,
      periodic,
      requestTimeoutMs,
      active: () => !document.hidden && navigator.onLine !== false,
      load: (signal) => transport<T>(path, "GET", undefined, signal),
      accept: (result) => {
        if (currentPath.current !== path) return;
        setData(result);
        setUpdatedAt(Date.now());
        setError("");
        setLoading(false);
      },
      reject: (e) => {
        if (currentPath.current !== path) return;
        if (
          e instanceof ApiError &&
          (e.status === 401 || e.status === 403 || e.status === 404)
        )
          setData(null);
        setError(e instanceof Error ? e.message : "Unable to load");
        setLoading(false);
      },
    });
    resource.current = { path, poller };
    poller.start();
    const activity = () => {
      if (periodic) poller.activity();
    };
    window.addEventListener("online", activity);
    window.addEventListener("offline", activity);
    window.addEventListener("focus", activity);
    document.addEventListener("visibilitychange", activity);
    return () => {
      poller.stop();
      if (resource.current?.poller === poller) resource.current = null;
      window.removeEventListener("online", activity);
      window.removeEventListener("offline", activity);
      window.removeEventListener("focus", activity);
      document.removeEventListener("visibilitychange", activity);
    };
  }, [path, interval, transport, periodic, requestTimeoutMs]);
  return { data, error, loading, refresh, setData, updatedAt };
}
export function usePath() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const update = () => {
      setPath(location.pathname);
      window.scrollTo(0, 0);
    };
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  return path;
}
export function navigate(path: string) {
  history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
