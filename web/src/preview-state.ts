import type { Challenge, Room } from "./types";
import { hintProgression } from "./hint-progression.ts";
export interface PreviewStep {
  id: number;
  title: string;
  position: number;
  value: number;
  visibility: "visible" | "hidden" | "locked";
}
export interface PreviewRoomSource {
  id: number;
  slug: string;
  name: string;
  subtitle: string;
  description: string;
  position: number;
  theme: Room["theme"];
  revision: string;
  steps: PreviewStep[];
}
export interface PreviewChallenge extends Omit<Challenge, "hints"> {
  room_slug: string;
  state: string;
  hints: {
    id: number;
    title?: string | null;
    content: string;
    cost: number;
    prerequisites: number[];
  }[];
  unsupported_reason: string | null;
}
export interface PreviewCatalog {
  revision: string;
  rooms: PreviewRoomSource[];
  challenges: PreviewChallenge[];
}
export interface LocalPreviewRoom {
  source: PreviewRoomSource;
  challenges: PreviewChallenge[];
  started: boolean;
  solved: number[];
  revealed: number[];
  stale: boolean;
  generation: number;
}
export interface PreviewState {
  latest: PreviewCatalog;
  rooms: LocalPreviewRoom[];
}
function fresh(
  source: PreviewRoomSource,
  catalog: PreviewCatalog,
  generation = 0,
): LocalPreviewRoom {
  return {
    source,
    challenges: catalog.challenges.filter((c) => c.room_slug === source.slug),
    started: false,
    solved: [],
    revealed: [],
    stale: false,
    generation,
  };
}
export function createPreviewState(catalog: PreviewCatalog): PreviewState {
  return {
    latest: catalog,
    rooms: catalog.rooms.map((room) => fresh(room, catalog)),
  };
}
export function mergePreviewCatalog(
  state: PreviewState,
  catalog: PreviewCatalog,
): PreviewState {
  return {
    latest: catalog,
    rooms: catalog.rooms.map((source) => {
      const old = state.rooms.find((room) => room.source.slug === source.slug);
      if (!old) return fresh(source, catalog);
      if (old.source.revision === source.revision) return old;
      return old.started
        ? { ...old, stale: true }
        : fresh(source, catalog, old.generation + 1);
    }),
  };
}
export function reloadPreviewRoom(
  state: PreviewState,
  slug: string,
): PreviewState {
  return {
    ...state,
    rooms: state.rooms.map((room) =>
      room.source.slug === slug
        ? fresh(
            state.latest.rooms.find((source) => source.slug === slug)!,
            state.latest,
            room.generation + 1,
          )
        : room,
    ),
  };
}
function update(
  state: PreviewState,
  slug: string,
  apply: (room: LocalPreviewRoom) => LocalPreviewRoom,
): PreviewState {
  return {
    ...state,
    rooms: state.rooms.map((room) =>
      room.source.slug === slug ? apply(room) : room,
    ),
  };
}
export function previewSteps(room: LocalPreviewRoom): Room["steps"] {
  let open = room.started;
  return room.source.steps
    .filter((step) => step.visibility !== "hidden")
    .map((step) => {
      const supported = !room.challenges.find((c) => c.id === step.id)
        ?.unsupported_reason;
      const solved =
        open &&
        supported &&
        step.visibility === "visible" &&
        room.solved.includes(step.id);
      const state = solved
        ? "solved"
        : open && supported && step.visibility === "visible"
          ? "available"
          : "locked";
      if (!solved) open = false;
      return {
        id: step.id,
        title: step.title,
        value: step.value,
        position: step.position,
        state,
        solved_at: null,
      };
    });
}
export function startPreviewRoom(
  state: PreviewState,
  slug: string,
): PreviewState {
  return update(state, slug, (room) => {
    const probe = { ...room, started: true };
    if (
      room.stale ||
      !previewSteps(probe).some((step) => step.state === "available")
    )
      throw new Error(
        "This room’s first challenge is hidden, locked or unsupported.",
      );
    return probe;
  });
}
export function recordPreviewResult(
  state: PreviewState,
  slug: string,
  id: number,
  status: string,
  generation: number,
): PreviewState {
  return update(state, slug, (room) => {
    if (room.generation !== generation || room.stale)
      throw new Error(
        "The preview changed. Check the room before trying again.",
      );
    if (status !== "correct") return room;
    if (
      !previewSteps(room).some(
        (step) => step.id === id && step.state === "available",
      )
    )
      throw new Error(
        "Start this room and complete its earlier visible challenges first.",
      );
    return { ...room, solved: [...room.solved, id] };
  });
}
export function revealPreviewHint(
  state: PreviewState,
  slug: string,
  hintId: number,
): PreviewState {
  return update(state, slug, (room) => {
    const challenge = room.challenges.find((c) =>
      c.hints.some((h) => h.id === hintId),
    );
    if (
      room.stale ||
      !challenge ||
      !previewSteps(room).some(
        (step) => step.id === challenge.id && step.state !== "locked",
      )
    )
      throw new Error(
        "This hint is not available in the current preview step.",
      );
    if (room.revealed.includes(hintId)) return room;
    if (
      hintProgression(challenge.hints, new Set(room.revealed)).nextId !== hintId
    )
      throw new Error("Reveal this challenge’s earlier hints first.");
    const hint = challenge.hints.find((hint) => hint.id === hintId)!;
    if (
      hint.prerequisites.some(
        (required) =>
          required >= hintId &&
          !room.revealed.includes(required) &&
          challenge.hints.some((candidate) => candidate.id === required),
      )
    )
      throw new Error(
        "This hint requires itself or a later hint in this challenge. Ask an organizer to review its native prerequisites.",
      );
    return { ...room, revealed: [...room.revealed, hintId] };
  });
}
export function previewRoomView(room: LocalPreviewRoom): Room {
  const steps = previewSteps(room),
    solved = steps.filter((step) => step.state === "solved").length;
  // Shared participant presentation shape only; Preview never calculates scores or clocks.
  return {
    ...room.source,
    steps,
    startable: previewSteps({ ...room, started: true }).some(
      (step) => step.state === "available",
    ),
    team_score: { points: 0, gross_points: 0, hint_costs: 0 },
    progress: {
      points: 0,
      gross_points: 0,
      hint_costs: 0,
      used_hint_ids: [],
      used_hint_costs: {},
      available_points: 0,
      credited_solved: solved,
      solved,
      total: steps.length,
      score_elapsed_ms: null,
      started_at: room.started ? "preview" : null,
      elapsed_ms: null,
      milestone_elapsed_ms: null,
      status:
        solved && solved === steps.length
          ? "complete"
          : room.started
            ? "active"
            : "not_started",
    },
  };
}
