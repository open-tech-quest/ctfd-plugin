import assert from "node:assert/strict";
import test from "node:test";
import {
  createPreviewState,
  mergePreviewCatalog,
  previewSteps,
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
      { id: id * 10, content: "Read-only", cost: 1000, prerequisites: [] },
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
const get = (state, slug = "openshift") =>
  state.rooms.find((r) => r.source.slug === slug);
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
test("hints reveal authorized text without a balance or purchase, preserving configured costs", () => {
  const initial = createPreviewState(catalog());
  assert.throws(() => revealPreviewHint(initial, "openshift", 10));
  const state = startPreviewRoom(initial, "openshift"),
    revealed = revealPreviewHint(state, "openshift", 10);
  assert.deepEqual(get(revealed).revealed, [10]);
  assert.equal(get(revealed).challenges[0].hints[0].cost, 1000);
  assert.deepEqual(get(revealed).solved, []);
  assert.deepEqual(get(state).revealed, []);
  assert.throws(() => revealPreviewHint(revealed, "openshift", 30));
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
