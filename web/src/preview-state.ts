import type { Challenge, Room, Score } from "./types";
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
    const challengeIds = new Set(room.source.steps.map((step) => step.id));
    const hintIds = new Set(
      room.challenges
        .flatMap((challenge) => challenge.hints)
        .map((hint) => hint.id),
    );
    const conflict = state.rooms.find(
      (other) =>
        other !== room &&
        other.stale &&
        (other.source.steps.some((step) => challengeIds.has(step.id)) ||
          other.challenges.some((challenge) =>
            challenge.hints.some((hint) => hintIds.has(hint.id)),
          )),
    );
    if (conflict)
      throw new Error(
        `Reload the saved changes in ${conflict.source.name} before starting ${room.source.name}.`,
      );
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
        "The room changed. Check the updated challenge path before trying again.",
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
    savedPoints(
      room.challenges.find((challenge) => challenge.id === id)?.value!,
    );
    return { ...room, solved: [...room.solved, id] };
  });
}
function availablePreviewHint(room: LocalPreviewRoom, hintId: number) {
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
    throw new Error("This hint is not available for the current challenge.");
  const hint = challenge.hints.find((hint) => hint.id === hintId)!;
  if (room.revealed.includes(hintId)) return hint;
  savedPoints(hint.cost);
  if (
    hintProgression(challenge.hints, new Set(room.revealed)).nextId !== hintId
  )
    throw new Error("Reveal this challenge’s earlier hints first.");
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
  return hint;
}
/** Read metadata for the confirmation dialog without revealing or advancing. */
export function previewHintMetadata(
  state: PreviewState,
  slug: string,
  hintId: number,
) {
  const room = state.rooms.find((room) => room.source.slug === slug);
  if (!room) throw new Error("This room is no longer available.");
  const hint = availablePreviewHint(room, hintId);
  const { content, ...metadata } = hint;
  return room.revealed.includes(hintId) ? { ...metadata, content } : metadata;
}
function savedPoints(value: number) {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new Error(
      "Saved challenge points or hint costs need organizer review.",
    );
  return value;
}
export function previewRoomScore(room: LocalPreviewRoom): Score {
  const solved = new Set(room.solved),
    revealed = new Set(room.revealed);
  const values = new Map(
    room.challenges.map((challenge) => [challenge.id, challenge.value]),
  );
  const gross_points = [...solved].reduce(
    (total, id) =>
      values.has(id) ? total + savedPoints(values.get(id)!) : total,
    0,
  );
  const hints = new Map(
    room.challenges
      .flatMap((challenge) => challenge.hints)
      .map((hint) => [hint.id, hint.cost]),
  );
  const hint_costs = [...revealed].reduce(
    (total, id) =>
      hints.has(id) ? total + savedPoints(hints.get(id)!) : total,
    0,
  );
  if (!Number.isSafeInteger(gross_points) || !Number.isSafeInteger(hint_costs))
    throw new Error("The saved point totals need organizer review.");
  return { gross_points, hint_costs, points: gross_points - hint_costs };
}
export function previewScore(state: PreviewState): Score {
  return state.rooms.map(previewRoomScore).reduce(
    (total, room) => ({
      gross_points: total.gross_points + room.gross_points,
      hint_costs: total.hint_costs + room.hint_costs,
      points: total.points + room.points,
    }),
    { gross_points: 0, hint_costs: 0, points: 0 },
  );
}
export function revealPreviewHint(
  state: PreviewState,
  slug: string,
  hintId: number,
): PreviewState {
  return update(state, slug, (room) => {
    const hint = availablePreviewHint(room, hintId);
    if (room.revealed.includes(hintId)) return room;
    const cost = savedPoints(hint.cost),
      balance = previewScore(state).points;
    if (cost > 0 && cost > balance)
      throw new Error(
        `Your team has ${balance} points; this hint costs ${cost}`,
      );
    return { ...room, revealed: [...room.revealed, hintId] };
  });
}
export function previewRoomView(
  room: LocalPreviewRoom,
  teamScore: Score = previewRoomScore(room),
): Room {
  const steps = previewSteps(room),
    solved = steps.filter((step) => step.state === "solved").length;
  const score = previewRoomScore(room);
  const costs = Object.fromEntries(
    room.challenges
      .flatMap((challenge) => challenge.hints)
      .filter((hint) => room.revealed.includes(hint.id))
      .map((hint) => [hint.id, hint.cost]),
  );
  // Clocks remain static; scores are derived solely from this tab's existing IDs.
  return {
    ...room.source,
    pause: {
      paused: false,
      started_at: null,
      reason: null,
      revision: "preview",
    },
    steps,
    startable: previewSteps({ ...room, started: true }).some(
      (step) => step.state === "available",
    ),
    team_score: teamScore,
    progress: {
      ...score,
      used_hint_ids: [...new Set(room.revealed)],
      used_hint_costs: costs,
      available_points: steps.reduce((sum, step) => sum + step.value, 0),
      credited_solved: solved,
      solved,
      total: steps.length,
      score_elapsed_ms: null,
      started_at: room.started ? "preview" : null,
      elapsed_ms: room.started ? 0 : null,
      milestone_elapsed_ms: solved > 0 && solved === steps.length ? 0 : null,
      status:
        solved && solved === steps.length
          ? "complete"
          : room.started
            ? "active"
            : "not_started",
    },
  };
}

/** Read-only staff inspection has no local journey state or prerequisite gating. */
export function staffBrowseRoomView(
  source: PreviewRoomSource,
  catalog: PreviewCatalog,
): Room {
  const view = previewRoomView(fresh(source, catalog));
  const steps = source.steps.map((step) => ({
    id: step.id,
    title: step.title,
    value: step.value,
    position: step.position,
    state: "available" as const,
    solved_at: null,
  }));
  return {
    ...view,
    steps,
    progress: {
      ...view.progress,
      total: steps.length,
      available_points: steps.reduce((sum, step) => sum + step.value, 0),
      elapsed_ms: 0,
    },
  };
}
