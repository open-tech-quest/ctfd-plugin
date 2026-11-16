import type { StandingsAccess, Viewer } from "./types";

export type StandingsView = "team" | "room" | "overall";
export const OPEN_STANDINGS: StandingsAccess = {
  room_comparison: true,
  overall_comparison: true,
  projector: true,
};

export function comparisonAccess(
  viewer: Pick<Viewer, "admin"> | undefined,
  configured?: StandingsAccess,
): StandingsAccess {
  return viewer?.admin ? OPEN_STANDINGS : (configured ?? OPEN_STANDINGS);
}

export function standingsView(
  requested: string | null,
  participant: boolean,
  access: StandingsAccess,
): StandingsView | null {
  if (requested === "team" && participant) return "team";
  if (requested === "room" && access.room_comparison) return "room";
  if (requested === "overall" && access.overall_comparison) return "overall";
  if (participant) return "team";
  return access.room_comparison
    ? "room"
    : access.overall_comparison
      ? "overall"
      : null;
}
