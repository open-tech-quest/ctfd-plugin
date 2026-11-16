import type { CompetitionClock, QuestEvent } from "./types";

export function previewClock(complete = false): CompetitionClock {
  return {
    elapsed_ms: 0,
    state: complete ? "complete" : "running",
    sampled_at: "",
    remaining_ms: null,
    finished_at: null,
  };
}

/** Extrapolate only the server's running clock, using monotonic receipt time. */
export function competitionElapsed(
  clock: CompetitionClock,
  receivedAt: number | null,
  now: number,
  event?: Pick<QuestEvent, "status" | "paused_at" | "end_at">,
): number | null {
  if (clock.elapsed_ms === null) return null;
  if (clock.state !== "running" || receivedAt === null) return clock.elapsed_ms;
  let delta = Math.max(0, now - receivedAt);
  if (clock.remaining_ms !== null) delta = Math.min(delta, clock.remaining_ms);
  const stop = event?.status === "paused" ? event.paused_at : event?.end_at;
  if (stop) {
    const limit = Date.parse(stop) - Date.parse(clock.sampled_at);
    if (Number.isFinite(limit)) delta = Math.min(delta, Math.max(0, limit));
  }
  return clock.elapsed_ms + Math.max(0, delta - (clock.delay_ms ?? 0));
}
