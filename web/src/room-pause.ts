import type { Phase, RoomPause } from "./types";

export function roomIsPaused(
  pause: RoomPause | undefined,
  staffPreview = false,
) {
  return !staffPreview && pause?.paused === true;
}

export function roomPlayEnabled(
  phase: Phase,
  pause: RoomPause | undefined,
  staffPreview = false,
) {
  return staffPreview || (phase === "live" && !roomIsPaused(pause));
}

export function roomHintReadable(playEnabled: boolean, redeemed: boolean) {
  return playEnabled || redeemed;
}

export function changeRoomPause(
  send: <T>(path: string, method: string, body: unknown) => Promise<T>,
  slug: string,
  action: "pause" | "resume",
  reason: string,
  revision: string,
) {
  if (!reason.trim() || !revision)
    throw new Error(
      "Refresh the room status and provide a reason before confirming.",
    );
  return send<unknown>(
    `/admin/rooms/${encodeURIComponent(slug)}/pause`,
    "POST",
    {
      action,
      reason: reason.trim(),
      revision,
    },
  );
}

export function roomPauseFailure(message: string, status?: number) {
  if (status === 0 || (status !== undefined && status >= 500))
    return "The room pause change was not confirmed. Refresh room status before trying again; the request may already have completed.";
  return `${message} Refresh room status before reviewing the action again.`;
}
