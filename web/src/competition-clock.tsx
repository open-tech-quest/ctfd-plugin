import { useEffect, useState } from "react";
import { duration } from "./api";
import { competitionElapsed } from "./competition-time";
import type { CompetitionClock as Clock, QuestEvent } from "./types";

export function CompetitionClock({
  clock,
  receivedAt,
  event,
}: {
  clock: Clock;
  receivedAt: number | null;
  event?: QuestEvent;
}) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const tick = () => setNow(performance.now());
    let timer: ReturnType<typeof setInterval> | undefined;
    const activity = () => {
      if (timer) clearInterval(timer);
      tick();
      if (!document.hidden && clock.state === "running" && receivedAt !== null)
        timer = setInterval(tick, 1000);
    };
    activity();
    document.addEventListener("visibilitychange", activity);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", activity);
    };
  }, [clock, receivedAt, event?.status, event?.paused_at, event?.end_at]);
  return <>{duration(competitionElapsed(clock, receivedAt, now, event))}</>;
}

export function EventClock({
  clock,
  receivedAt,
  event,
}: {
  clock?: Clock;
  receivedAt: number | null;
  event: QuestEvent;
}) {
  if (!clock || clock.state === "not_started" || event.status === "draft")
    return null;
  return (
    <div className="event-clock">
      <span>
        {clock.state === "complete"
          ? "All challenges completed in"
          : "Event time"}
      </span>
      <strong className="numeric">
        <CompetitionClock clock={clock} receivedAt={receivedAt} event={event} />
      </strong>
      {clock.state === "paused" && <span>Paused</span>}
      {clock.state === "ended" && <span>Final</span>}
    </div>
  );
}
