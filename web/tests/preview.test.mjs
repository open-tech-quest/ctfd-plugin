import assert from "node:assert/strict";
import test from "node:test";
import {
  createPreviewState,
  mergePreviewCatalog,
  previewSteps,
  previewHintMetadata,
  previewRoomScore,
  previewScore,
  previewRoomView,
  staffBrowseRoomView,
  recordPreviewResult,
  reloadPreviewRoom,
  revealPreviewHint,
  startPreviewRoom,
} from "../src/preview-state.ts";
import {
  isPreviewPath,
  previewRoom,
  previewSlug,
} from "../src/preview-routing.ts";
function catalog() {
  const step = (id, visibility = "visible") => ({
    id,
    title: `Step ${id}`,
    position: id,
    value: 100,
    visibility,
  });
  const room = (slug, steps) => ({
    id: slug === "openshift" ? 1 : 2,
    slug,
    name: slug,
    subtitle: "",
    description: "Original",
    position: 0,
    theme: {},
    revision: slug + "-v1",
    steps,
  });
  const challenge = (id, room_slug) => ({
    id,
    room_slug,
    name: `Step ${id}`,
    state: "visible",
    description: "Instructions",
    value: 100,
    files: [],
    unsupported_reason: null,
    hints: [
      {
        id: id === 1 ? 10 : id * 100,
        content: "Read-only",
        cost: 0,
        prerequisites: [],
      },
    ],
  });
  return {
    revision: "v1",
    rooms: [
      room("openshift", [
        step(1),
        step(2, "hidden"),
        step(3),
        step(4, "locked"),
      ]),
      room("ansible", [step(5)]),
    ],
    challenges: [1, 2, 3, 4]
      .map((id) => challenge(id, "openshift"))
      .concat(challenge(5, "ansible")),
  };
}

test("staff browsing and local journeys mask any real room incident", () => {
  const saved = catalog();
  saved.rooms[0].pause = {
    paused: true,
    started_at: "2026-11-12T09:00:00Z",
    reason: "Real lab outage",
    revision: "live-state",
  };
  const state = createPreviewState(saved);
  assert.equal(previewRoomView(state.rooms[0]).pause.paused, false);
  assert.equal(staffBrowseRoomView(saved.rooms[0], saved).pause.paused, false);
  assert.equal(
    previewRoomView(startPreviewRoom(state, "openshift").rooms[0]).progress
      .status,
    "active",
  );
});
const get = (state, slug = "openshift") =>
  state.rooms.find((r) => r.source.slug === slug);
function fundedCatalog() {
  const source = catalog();
  source.challenges.find((challenge) => challenge.id === 5).value = 2000;
  source.rooms[1].steps[0].value = 2000;
  return source;
}
function earnOtherRoom(state) {
  state = startPreviewRoom(state, "ansible");
  return recordPreviewResult(
    state,
    "ansible",
    5,
    "correct",
    get(state, "ansible").generation,
  );
}

test("visible path omits hidden steps; start and correct answers never unlock locked/unsupported content", () => {
  let state = createPreviewState(catalog());
  assert.deepEqual(
    previewSteps(get(state)).map((s) => s.state),
    ["locked", "locked", "locked"],
  );
  state = startPreviewRoom(state, "openshift");
  assert.deepEqual(
    previewSteps(get(state)).map((s) => [s.id, s.state]),
    [
      [1, "available"],
      [3, "locked"],
      [4, "locked"],
    ],
  );
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  assert.equal(previewSteps(get(state))[1].state, "available");
  state = recordPreviewResult(state, "openshift", 3, "correct", 0);
  assert.equal(previewSteps(get(state))[2].state, "locked");
  assert.throws(() => recordPreviewResult(state, "openshift", 4, "correct", 0));
  const hidden = catalog();
  hidden.rooms[0].steps.forEach((s) => (s.visibility = "hidden"));
  assert.throws(() =>
    startPreviewRoom(createPreviewState(hidden), "openshift"),
  );
  const unsupported = catalog();
  unsupported.challenges[0].unsupported_reason = "Custom grading";
  assert.throws(() =>
    startPreviewRoom(createPreviewState(unsupported), "openshift"),
  );
});
test("only current correct grading advances; incorrect/partial/rate responses and out-of-order answers do not", () => {
  const state = startPreviewRoom(createPreviewState(catalog()), "openshift");
  for (const result of [
    "incorrect",
    "partial",
    "ratelimited",
    "already_solved",
  ])
    assert.deepEqual(
      get(recordPreviewResult(state, "openshift", 1, result, 0)).solved,
      [],
    );
  assert.throws(() => recordPreviewResult(state, "openshift", 3, "correct", 0));
  assert.deepEqual(
    get(recordPreviewResult(state, "openshift", 1, "correct", 0)).solved,
    [1],
  );
});
test("free hints reveal authorized text at zero points without a real purchase", () => {
  const initial = createPreviewState(catalog());
  assert.throws(() => revealPreviewHint(initial, "openshift", 10));
  const state = startPreviewRoom(initial, "openshift"),
    revealed = revealPreviewHint(state, "openshift", 10);
  assert.deepEqual(get(revealed).revealed, [10]);
  assert.equal(get(revealed).challenges[0].hints[0].cost, 0);
  assert.deepEqual(get(revealed).solved, []);
  assert.deepEqual(get(state).revealed, []);
  assert.throws(() => revealPreviewHint(revealed, "openshift", 300));
});
test("saved edits freeze started content; reload resets only that room and rejects its late older answer", () => {
  let state = startPreviewRoom(createPreviewState(catalog()), "openshift");
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  state = revealPreviewHint(state, "openshift", 10);
  const changed = catalog();
  changed.revision = "v2";
  changed.rooms.forEach((r) => {
    r.revision += "-edited";
    r.description = "New";
  });
  changed.challenges[0].description = "New instructions";
  state = mergePreviewCatalog(state, changed);
  assert.equal(get(state).stale, true);
  assert.equal(get(state).source.description, "Original");
  assert.equal(get(state).challenges[0].description, "Instructions");
  assert.equal(get(state, "ansible").source.description, "New");
  assert.equal(get(state, "ansible").stale, false);
  assert.throws(() => recordPreviewResult(state, "openshift", 3, "correct", 0));
  state = startPreviewRoom(state, "ansible");
  state = recordPreviewResult(state, "ansible", 5, "correct", 1);
  state = reloadPreviewRoom(state, "openshift");
  assert.equal(get(state).source.description, "New");
  assert.equal(get(state).started, false);
  assert.deepEqual(get(state).solved, []);
  assert.deepEqual(get(state).revealed, []);
  assert.deepEqual(get(state, "ansible").solved, [5]);
  state = startPreviewRoom(state, "openshift");
  assert.throws(() => recordPreviewResult(state, "openshift", 1, "correct", 0));
});
test("unrelated edits preserve current progress; removed rooms disappear and new rooms start fresh", () => {
  let state = startPreviewRoom(createPreviewState(catalog()), "openshift");
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  const changed = catalog();
  changed.rooms[1].revision = "ansible-v2";
  state = mergePreviewCatalog(state, changed);
  assert.equal(get(state).stale, false);
  assert.deepEqual(get(state).solved, [1]);
  state = mergePreviewCatalog(state, {
    ...changed,
    rooms: changed.rooms.filter((r) => r.slug !== "openshift"),
  });
  assert.equal(get(state), undefined);
  state = mergePreviewCatalog(state, catalog());
  assert.equal(get(state).started, false);
  assert.deepEqual(get(state).solved, []);
});
test("fresh tabs, leaving and reload have no shared persisted progress; paths carry only the selected room", () => {
  const source = catalog();
  let first = startPreviewRoom(createPreviewState(source), "openshift");
  first = recordPreviewResult(first, "openshift", 1, "correct", 0);
  for (let i = 0; i < 3; i++) {
    const fresh = createPreviewState(source);
    assert.equal(get(fresh).started, false);
    assert.deepEqual(get(fresh).solved, []);
  }
  assert.deepEqual(get(first).solved, [1]);
  assert.equal(isPreviewPath("/quest/preview"), true);
  assert.equal(previewSlug(previewRoom("openshift")), "openshift");
  for (const path of [
    "/quest",
    "/quest/rooms/openshift",
    "/quest/preview-other",
  ])
    assert.equal(isPreviewPath(path), false);
  assert.equal(previewSlug("/quest/preview/rooms/%2fadmin"), null);
});

test("participant preview reveals hints in native order locally and refuses programmatic skips", () => {
  const source = fundedCatalog();
  source.challenges[0].hints = [
    { id: 30, content: "Third", cost: 10, prerequisites: [] },
    { id: 10, content: "First free", cost: 0, prerequisites: [] },
    { id: 20, content: "Second paid", cost: 1000, prerequisites: [] },
  ];
  let state = startPreviewRoom(
    earnOtherRoom(createPreviewState(source)),
    "openshift",
  );
  assert.throws(
    () => revealPreviewHint(state, "openshift", 20),
    /earlier hints/,
  );
  state = revealPreviewHint(state, "openshift", 10);
  assert.deepEqual(get(state).revealed, [10]);
  assert.throws(
    () => revealPreviewHint(state, "openshift", 30),
    /earlier hints/,
  );
  state = revealPreviewHint(state, "openshift", 20);
  state = revealPreviewHint(state, "openshift", 20);
  state = revealPreviewHint(state, "openshift", 30);
  assert.deepEqual(get(state).revealed, [10, 20, 30]);
  assert.deepEqual(get(state).solved, []);
});
test("legacy local later hints remain readable but cannot bypass the first missing hint", () => {
  const source = catalog();
  source.challenges[0].hints = [
    { id: 10, content: "First", cost: 0, prerequisites: [] },
    { id: 20, content: "Next", cost: 5, prerequisites: [] },
    { id: 30, content: "Legacy later", cost: 5, prerequisites: [] },
  ];
  let state = startPreviewRoom(createPreviewState(source), "openshift");
  get(state).revealed = [30];
  assert.deepEqual(
    get(revealPreviewHint(state, "openshift", 30)).revealed,
    [30],
  );
  assert.throws(
    () => revealPreviewHint(state, "openshift", 20),
    /earlier hints/,
  );
  state = revealPreviewHint(state, "openshift", 10);
  assert.deepEqual(get(state).revealed, [30, 10]);
});

test("preview flags a missing self/later native hint prerequisite without revealing or advancing", () => {
  const source = catalog();
  source.challenges[0].hints = [
    { id: 10, content: "First", cost: 0, prerequisites: [20] },
    { id: 20, content: "Later", cost: 5, prerequisites: [] },
  ];
  const state = startPreviewRoom(createPreviewState(source), "openshift");
  assert.throws(
    () => revealPreviewHint(state, "openshift", 10),
    /requires itself or a later hint.*organizer/,
  );
  assert.deepEqual(get(state).revealed, []);
  assert.deepEqual(get(state).solved, []);
  get(state).revealed = [20];
  assert.deepEqual(
    get(revealPreviewHint(state, "openshift", 20)).revealed,
    [20],
  );
  assert.deepEqual(
    get(revealPreviewHint(state, "openshift", 10)).revealed,
    [20, 10],
  );
  const crossRoom = catalog();
  crossRoom.challenges[0].hints[0].prerequisites = [500];
  assert.deepEqual(
    get(
      revealPreviewHint(
        startPreviewRoom(createPreviewState(crossRoom), "openshift"),
        "openshift",
        10,
      ),
    ).revealed,
    [10],
  );
  const self = catalog();
  self.challenges[0].hints[0].prerequisites = [10];
  assert.throws(
    () =>
      revealPreviewHint(
        startPreviewRoom(createPreviewState(self), "openshift"),
        "openshift",
        10,
      ),
    /requires itself/,
  );
});

test("preview hint metadata opens confirmation without revealing; only local confirmation advances", () => {
  const source = fundedCatalog();
  source.challenges[0].hints = [
    { id: 10, title: "Free", content: "First", cost: 0, prerequisites: [] },
    { id: 20, title: "Paid", content: "Second", cost: 25, prerequisites: [] },
    {
      id: 30,
      title: "Expensive",
      content: "Third",
      cost: 300,
      prerequisites: [],
    },
    { id: 40, title: "Final free", content: "", cost: 0, prerequisites: [] },
  ];
  let state = startPreviewRoom(
    earnOtherRoom(createPreviewState(source)),
    "openshift",
  );
  const before = JSON.stringify(state);
  assert.deepEqual(previewHintMetadata(state, "openshift", 10), {
    id: 10,
    title: "Free",
    cost: 0,
    prerequisites: [],
  });
  assert.equal(JSON.stringify(state), before);
  // Cancelling or repeating the metadata read keeps the same next hint.
  assert.equal("content" in previewHintMetadata(state, "openshift", 10), false);
  assert.throws(
    () => previewHintMetadata(state, "openshift", 20),
    /earlier hints/,
  );
  assert.deepEqual(get(state).revealed, []);
  for (const [hintId, cost, text] of [
    [10, 0, "First"],
    [20, 25, "Second"],
    [30, 300, "Third"],
    [40, 0, ""],
  ]) {
    assert.equal(previewHintMetadata(state, "openshift", hintId).cost, cost);
    assert.equal(
      "content" in previewHintMetadata(state, "openshift", hintId),
      false,
    );
    state = revealPreviewHint(state, "openshift", hintId);
    assert.equal(previewHintMetadata(state, "openshift", hintId).content, text);
  }
  // Costs come from already-earned local points; no balance is stored.
  assert.deepEqual(get(state).revealed, [10, 20, 30, 40]);
  assert.deepEqual(get(state).solved, []);
  const confirmed = JSON.stringify(state);
  previewHintMetadata(state, "openshift", 10);
  assert.equal(JSON.stringify(state), confirmed);
});
test("a preview confirmation after stale content, reload or leaving cannot advance the old hint", () => {
  let state = startPreviewRoom(createPreviewState(catalog()), "openshift");
  previewHintMetadata(state, "openshift", 10);
  const changed = catalog();
  changed.rooms[0].revision = "edited";
  const stale = mergePreviewCatalog(state, changed),
    before = JSON.stringify(stale);
  assert.throws(
    () => revealPreviewHint(stale, "openshift", 10),
    /not available/,
  );
  assert.equal(JSON.stringify(stale), before);
  const reloaded = reloadPreviewRoom(state, "openshift");
  assert.throws(
    () => revealPreviewHint(reloaded, "openshift", 10),
    /not available/,
  );
  const left = createPreviewState(catalog());
  assert.throws(
    () => previewHintMetadata(left, "openshift", 10),
    /not available/,
  );
  assert.deepEqual(get(left).revealed, []);
  const conflict = catalog();
  conflict.challenges[0].hints[0].prerequisites = [10];
  const blocked = startPreviewRoom(createPreviewState(conflict), "openshift");
  assert.throws(
    () => previewHintMetadata(blocked, "openshift", 10),
    /requires itself/,
  );
  assert.deepEqual(get(blocked).revealed, []);
});

test("journey points start at zero, count native values once and charge confirmed hints once across rooms", () => {
  const source = catalog();
  source.challenges[0].hints = [
    { id: 10, content: "Paid", cost: 25, prerequisites: [] },
    { id: 20, content: "Free", cost: 0, prerequisites: [] },
    { id: 30, content: "Expensive", cost: 100, prerequisites: [] },
  ];
  let state = startPreviewRoom(createPreviewState(source), "openshift");
  assert.deepEqual(previewScore(state), {
    gross_points: 0,
    hint_costs: 0,
    points: 0,
  });
  assert.equal(previewHintMetadata(state, "openshift", 10).cost, 25);
  assert.throws(
    () => revealPreviewHint(state, "openshift", 10),
    /team has 0 points/,
  );
  assert.deepEqual(get(state).revealed, []);
  state = earnOtherRoom(state);
  assert.equal(previewScore(state).points, 100);
  state = revealPreviewHint(state, "openshift", 10);
  assert.deepEqual(previewScore(state), {
    gross_points: 100,
    hint_costs: 25,
    points: 75,
  });
  state = revealPreviewHint(state, "openshift", 10);
  assert.equal(previewScore(state).points, 75);
  state = reloadPreviewRoom(state, "ansible");
  assert.equal(previewScore(state).points, -25);
  state = revealPreviewHint(state, "openshift", 10);
  assert.equal(previewScore(state).points, -25);
  state = revealPreviewHint(state, "openshift", 20);
  assert.equal(previewScore(state).points, -25);
  assert.equal(previewHintMetadata(state, "openshift", 30).cost, 100);
  assert.throws(
    () => revealPreviewHint(state, "openshift", 30),
    /team has -25 points/,
  );
  state = earnOtherRoom(state);
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  assert.equal(previewScore(state).points, 175);
  state = revealPreviewHint(state, "openshift", 30);
  assert.equal(previewScore(state).points, 75);
  assert.deepEqual(previewRoomScore(get(state)), {
    gross_points: 100,
    hint_costs: 125,
    points: -25,
  });
  const view = previewRoomView(get(state), previewScore(state));
  assert.equal(view.team_score.points, 75);
  assert.equal(view.progress.points, -25);
  assert.equal(view.progress.used_hint_costs[20], 0);
  assert.equal(view.progress.elapsed_ms, 0);
  state = reloadPreviewRoom(state, "openshift");
  assert.equal(previewScore(state).points, 100);
  assert.deepEqual(get(state, "ansible").solved, [5]);
});
test("derived scores deduplicate IDs and reject invalid authored values or prices before progress changes", () => {
  let state = createPreviewState(catalog());
  get(state).solved = [1, 1, 99999];
  get(state).revealed = [10, 10, 99999];
  assert.deepEqual(previewScore(state), {
    gross_points: 100,
    hint_costs: 0,
    points: 100,
  });
  for (const cost of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    const source = catalog();
    source.challenges[0].hints[0].cost = cost;
    const state = startPreviewRoom(createPreviewState(source), "openshift");
    assert.throws(
      () => previewHintMetadata(state, "openshift", 10),
      /organizer review/,
    );
    assert.throws(
      () => revealPreviewHint(state, "openshift", 10),
      /organizer review/,
    );
    assert.deepEqual(get(state).revealed, []);
  }
  const invalid = catalog();
  invalid.challenges[0].value = -1;
  state = startPreviewRoom(createPreviewState(invalid), "openshift");
  assert.throws(
    () => recordPreviewResult(state, "openshift", 1, "correct", 0),
    /organizer review/,
  );
  assert.deepEqual(get(state).solved, []);
});
test("staff free-browse view exposes every mapped visibility without starting or earning journey progress", () => {
  const source = catalog(),
    before = JSON.stringify(source),
    room = staffBrowseRoomView(source.rooms[0], source);
  assert.deepEqual(
    room.steps.map((step) => [step.id, step.state]),
    [
      [1, "available"],
      [2, "available"],
      [3, "available"],
      [4, "available"],
    ],
  );
  assert.equal(room.progress.started_at, null);
  assert.equal(room.progress.solved, 0);
  assert.equal(room.team_score.points, 0);
  assert.equal(JSON.stringify(source), before);
});

test("moved/repriced challenges cannot earn twice across frozen and fresh room snapshots", () => {
  const source = catalog();
  source.challenges[0].hints[0].cost = 25;
  let state = startPreviewRoom(createPreviewState(source), "openshift");
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  state = revealPreviewHint(state, "openshift", 10);
  assert.equal(previewScore(state).points, 75);
  const moved = catalog();
  moved.rooms.forEach((room) => (room.revision += "-moved"));
  moved.rooms[0].steps = moved.rooms[0].steps.filter((step) => step.id !== 1);
  moved.rooms[1].steps.unshift({ ...source.rooms[0].steps[0], value: 250 });
  moved.challenges[0].room_slug = "ansible";
  moved.challenges[0].value = 250;
  moved.challenges[0].hints[0].cost = 60;
  moved.rooms.reverse();
  state = mergePreviewCatalog(state, moved);
  assert.equal(previewScore(state).points, 75);
  assert.throws(
    () => startPreviewRoom(state, "ansible"),
    /Reload.*openshift.*before starting ansible/,
  );
  assert.equal(get(state, "ansible").started, false);
  assert.equal(previewScore(state).points, 75);
  state = reloadPreviewRoom(state, "openshift");
  assert.equal(previewScore(state).points, 0);
  state = startPreviewRoom(state, "ansible");
  state = recordPreviewResult(
    state,
    "ansible",
    1,
    "correct",
    get(state, "ansible").generation,
  );
  assert.equal(previewScore(state).points, 250);
  state = revealPreviewHint(state, "ansible", 10);
  assert.equal(previewScore(state).points, 190);
  state = revealPreviewHint(state, "ansible", 10);
  assert.equal(previewScore(state).points, 190);
});
test("a moved hint also requires reloading its retained stale room before destination starts", () => {
  const source = catalog();
  source.challenges[0].hints[0].cost = 25;
  let state = startPreviewRoom(createPreviewState(source), "openshift");
  state = recordPreviewResult(state, "openshift", 1, "correct", 0);
  state = revealPreviewHint(state, "openshift", 10);
  const moved = catalog();
  moved.rooms.forEach((room) => (room.revision += "-hint-moved"));
  moved.challenges[0].hints = [];
  moved.challenges.find((challenge) => challenge.id === 5).hints = [
    { id: 10, content: "Moved", cost: 40, prerequisites: [] },
  ];
  state = mergePreviewCatalog(state, moved);
  assert.equal(previewScore(state).points, 75);
  assert.throws(() => startPreviewRoom(state, "ansible"), /Reload.*openshift/);
  assert.deepEqual(get(state, "openshift").revealed, [10]);
  state = reloadPreviewRoom(state, "openshift");
  state = earnOtherRoom(state);
  state = revealPreviewHint(state, "ansible", 10);
  assert.equal(previewScore(state).points, 60);
  assert.deepEqual(get(state, "ansible").revealed, [10]);
});
