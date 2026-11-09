import type { StandingTeam, Standings } from "./types";
type StandingRoom = Standings["rooms"][number];
// Standings must expose the same millisecond precision used for sporting ranks.
export function rankingDuration(ms: number | null | undefined) {
  if (ms == null) return "—";
  const value = Math.max(0, Math.round(ms));
  const seconds = Math.floor(value / 1000);
  const parts = [
    Math.floor(seconds / 3600),
    Math.floor(seconds / 60) % 60,
    seconds % 60,
  ];
  return (
    parts.map((part) => String(part).padStart(2, "0")).join(":") +
    "." +
    String(value % 1000).padStart(3, "0")
  );
}
export function rankingOrder(data: Standings, room: boolean) {
  if (data.scoring_version !== 2)
    return room
      ? "Ranked by consecutive solves, then active time to the last counted solve."
      : "Ranked by distinct solves, then active time to the last counted solve.";
  return room
    ? "Ranked by challenges solved, then net room points, then active time to the last counted solve."
    : "Ranked by net points across all rooms, then active time to the last counted solve. Solve count does not break overall ties.";
}
export function orderedTeams(teams: StandingTeam[], room?: StandingRoom) {
  if (!room)
    return [...teams].sort(
      (a, b) =>
        (a.rank ?? Infinity) - (b.rank ?? Infinity) ||
        a.name.localeCompare(b.name),
    );
  return [...teams].sort((a, b) => {
    const x = a.room_progress.find((p) => p.room_id === room.id),
      y = b.room_progress.find((p) => p.room_id === room.id);
    return (
      (x?.rank ?? Infinity) - (y?.rank ?? Infinity) ||
      a.name.localeCompare(b.name)
    );
  });
}
