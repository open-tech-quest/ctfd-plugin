import type { request } from "./api";
import type { AdminState, TeamLocation } from "./types";

export interface WaitingPlacement {
  token: string;
  capacity: number;
  max_batch: number;
  event_status: string;
  expires_at: string;
  participants: { id: number; name: string }[];
  groups: {
    team_id: number | null;
    name: string;
    user_ids: number[];
    existing_user_ids: number[];
    location: TeamLocation | null;
  }[];
}

export const previewWaitingPlacement = (send: typeof request) =>
  send<WaitingPlacement>("/admin/onboarding/waiting", "POST", {
    preview: true,
  });

export const applyWaitingPlacementBody = (token: string, reason: string) => ({
  preview: false,
  token,
  reason: reason.trim(),
});

const locationKey = (value?: TeamLocation | null) =>
  JSON.stringify(
    value ? [value.venue_room_id, value.table_number, value.room_name] : null,
  );

export function placementIsStale(plan: WaitingPlacement, data: AdminState) {
  const state = data.onboarding;
  if (
    !state.late_admission?.allowed ||
    state.phase !== "finalized" ||
    data.event.status !== plan.event_status ||
    state.team_size !== plan.capacity ||
    Date.parse(data.event.server_now) >= Date.parse(plan.expires_at)
  )
    return true;
  if (
    plan.participants.some((selected) => {
      const person = data.participants.find((p) => p.id === selected.id);
      return (
        !person ||
        person.banned ||
        person.team_id !== null ||
        (state.require_attendance !== false && !person.ready_at)
      );
    })
  )
    return true;
  return plan.groups.some((group) => {
    if (group.team_id === null)
      return data.teams.some(
        (t) => t.name.toLowerCase() === group.name.toLowerCase(),
      );
    const team = data.teams.find((t) => t.id === group.team_id);
    return (
      !team ||
      team.hidden ||
      team.banned ||
      team.excluded ||
      team.name !== group.name ||
      [...team.user_ids].sort((a, b) => a - b).join(",") !==
        [...group.existing_user_ids].sort((a, b) => a - b).join(",") ||
      locationKey(team.location) !== locationKey(group.location)
    );
  });
}
