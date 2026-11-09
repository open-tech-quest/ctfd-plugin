import { useEffect, useState } from "react";
// A local display clock anchored to the latest server response, independent of host wall-clock skew.
export function useProjectorClock(serverNow?: string) {
  const [now, setNow] = useState(() => (serverNow ? Date.parse(serverNow) : 0));
  useEffect(() => {
    const base = serverNow ? Date.parse(serverNow) : 0;
    if (!Number.isFinite(base) || !base) return;
    const received = performance.now();
    let timer: ReturnType<typeof setInterval> | undefined;
    const tick = () => setNow(base + Math.max(0, performance.now() - received));
    const activity = () => {
      if (timer) clearInterval(timer);
      if (!document.hidden) {
        tick();
        timer = setInterval(tick, 1000);
      }
    };
    activity();
    document.addEventListener("visibilitychange", activity);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", activity);
    };
  }, [serverNow]);
  return now;
}
