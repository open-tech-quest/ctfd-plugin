import type { Projector } from "./types";

export const rotationDefaults = {
  enabled: false,
  anchor_at: null,
  paused_at: null,
  overview_seconds: 60,
  room_seconds: 30,
  overall_seconds: 20,
};
export function rotationFrame(
  config: Projector | null,
  rooms: { slug: string }[],
  now: number,
) {
  const rotation = { ...rotationDefaults, ...config?.rotation };
  const manual = {
    view: config?.view ?? "overview",
    room_slug: config?.room_slug ?? null,
    elapsed: 0,
  };
  if (!rotation.enabled || !rotation.anchor_at || !rooms.length) return manual;
  const anchor = Date.parse(rotation.anchor_at);
  const endpoint = rotation.paused_at ? Date.parse(rotation.paused_at) : now;
  if (!Number.isFinite(anchor) || !Number.isFinite(endpoint)) return manual;
  const elapsed = Math.max(0, endpoint - anchor);
  const cycle =
    (rotation.overview_seconds +
      rotation.room_seconds +
      rotation.overall_seconds) *
    1000;
  const roomIndex = Math.floor(elapsed / cycle) % rooms.length;
  const within = elapsed % cycle;
  if (within < rotation.overview_seconds * 1000)
    return {
      view: "overview" as const,
      room_slug: null,
      // Accumulate only overview time so later room/tie pages get a turn on subsequent visits.
      elapsed:
        Math.floor(elapsed / cycle) * rotation.overview_seconds * 1000 + within,
    };
  if (within < (rotation.overview_seconds + rotation.room_seconds) * 1000)
    return {
      view: "room" as const,
      room_slug: rooms[roomIndex].slug,
      elapsed: within - rotation.overview_seconds * 1000,
    };
  return {
    view: "overall" as const,
    room_slug: null,
    elapsed:
      within - (rotation.overview_seconds + rotation.room_seconds) * 1000,
  };
}
// Use authoritative ranks, never create a second sporting order in the browser.
export function leadingTeams<T extends { rank: number | null }>(
  teams: T[],
  places: number,
) {
  return teams.filter((team) => team.rank !== null && team.rank <= places);
}
export function pageItems<T>(
  items: T[],
  pageSize: number,
  elapsed: number,
  seconds = 10,
) {
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const safeElapsed = Math.max(0, elapsed);
  const pageMs = seconds * 1000;
  const page = Math.floor(safeElapsed / pageMs) % pages;
  return {
    visibleElapsed:
      Math.floor(safeElapsed / (pages * pageMs)) * pageMs +
      (safeElapsed % pageMs),
    items: items.slice(page * pageSize, (page + 1) * pageSize),
    page,
    pages,
  };
}

export function manualOverviewElapsed(now: number) {
  // Epoch stays stable across polling and event lifecycle transitions. This is display paging, not a competition clock.
  return Math.max(0, now);
}
