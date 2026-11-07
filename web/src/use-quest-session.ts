import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./api";
import { createQuestSession, type QuestSessionState } from "./quest-session";
import type { Bootstrap, QuestSync } from "./types";

export function useQuestSession(path: string) {
  const [state, setState] = useState<QuestSessionState>({
    data: null,
    live: null,
    loading: true,
    error: "",
    syncError: "",
  });
  const session = useRef<ReturnType<typeof createQuestSession> | null>(null);
  const previousPath = useRef(path);
  const refresh = useCallback(
    () => session.current?.refresh() ?? Promise.resolve(),
    [],
  );
  useEffect(() => {
    const current = createQuestSession({
      active: () => !document.hidden && navigator.onLine !== false,
      loadBootstrap: (signal) =>
        request<Bootstrap>("/bootstrap", "GET", undefined, signal),
      loadSync: (signal) =>
        request<QuestSync>("/sync", "GET", undefined, signal),
      publish(next) {
        // Publish identity and nonce atomically before consumers can issue POSTs.
        window.QUEST_INIT = {
          ...window.QUEST_INIT,
          viewer: next.live?.viewer,
          csrfNonce: next.live?.csrfNonce ?? undefined,
        };
        setState(next);
      },
    });
    session.current = current;
    current.start();
    const activity = () => current.activity();
    window.addEventListener("online", activity);
    window.addEventListener("offline", activity);
    window.addEventListener("focus", activity);
    document.addEventListener("visibilitychange", activity);
    return () => {
      current.stop();
      if (session.current === current) session.current = null;
      window.removeEventListener("online", activity);
      window.removeEventListener("offline", activity);
      window.removeEventListener("focus", activity);
      document.removeEventListener("visibilitychange", activity);
    };
  }, []);
  useEffect(() => {
    if (previousPath.current === path) return;
    previousPath.current = path;
    if (path.replace(/\/$/, "") === "/quest")
      void session.current?.navigation();
  }, [path]);
  return { ...state, refresh };
}
