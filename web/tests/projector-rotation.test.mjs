import assert from "node:assert/strict";
import test from "node:test";
import {
  rotationFrame,
  leadingTeams,
  pageItems,
  manualOverviewElapsed,
} from "../src/projector-rotation.ts";
const anchor = Date.parse("2026-11-09T12:00:00Z");
const config = {
  view: "overview",
  room_slug: null,
  rotation: {
    enabled: true,
    anchor_at: new Date(anchor).toISOString(),
    paused_at: null,
    overview_seconds: 60,
    room_seconds: 30,
    overall_seconds: 20,
  },
};
const rooms = [{ slug: "openshift" }, { slug: "ansible" }];
test("server anchored rotation gives room goals most time and features every room", () => {
  const frame = (ms) => rotationFrame(config, rooms, anchor + ms);
  assert.equal(frame(0).view, "overview");
  assert.equal(frame(59999).view, "overview");
  assert.deepEqual(
    [frame(60000).view, frame(60000).room_slug],
    ["room", "openshift"],
  );
  assert.equal(frame(90000).view, "overall");
  assert.equal(frame(110000).view, "overview");
  assert.equal(frame(170000).room_slug, "ansible");
  assert.equal(frame(280000).room_slug, "openshift");
});
test("paused frames freeze and a resumed server anchor continues the same slot", () => {
  const paused = {
    ...config,
    rotation: {
      ...config.rotation,
      paused_at: new Date(anchor + 71000).toISOString(),
    },
  };
  assert.deepEqual(
    rotationFrame(paused, rooms, anchor + 999999),
    rotationFrame(paused, rooms, anchor + 72000),
  );
  const resumed = {
    ...config,
    rotation: {
      ...config.rotation,
      anchor_at: new Date(anchor + 50000).toISOString(),
    },
  };
  assert.equal(rotationFrame(resumed, rooms, anchor + 121000).view, "room");
});
test("manual overrides, legacy configs and an empty room set remain safe", () => {
  assert.equal(
    rotationFrame({ view: "matrix", room_slug: null }, rooms, anchor).view,
    "matrix",
  );
  assert.equal(rotationFrame(null, rooms, anchor).view, "overview");
  assert.equal(rotationFrame(config, [], anchor + 100000).view, "overview");
  assert.equal(rotationFrame(config, rooms, anchor - 1000).elapsed, 0);
});
test("leading places include every genuine tie and do not judge eligibility by point balance", () => {
  const rows = [
    { rank: 1, points: 0 },
    { rank: 1, points: -20 },
    { rank: 3, points: -30 },
    { rank: 4, points: 500 },
    { rank: null, points: 0 },
  ];
  assert.deepEqual(leadingTeams(rows, 3), rows.slice(0, 3));
  const tied = Array.from({ length: 50 }, (_, i) => ({ rank: 1, id: i + 1 }));
  assert.equal(leadingTeams(tied, 3).length, 50);
  const shown = new Set();
  for (let n = 0; n < 17; n++)
    for (const t of pageItems(tied, 3, n * 10000).items) shown.add(t.id);
  assert.equal(shown.size, 50);
});
test("room overview paging continues across rotation visits, including short custom intervals", () => {
  const many = Array.from({ length: 24 }, (_, i) => ({ slug: String(i) }));
  const short = {
    ...config,
    rotation: {
      ...config.rotation,
      overview_seconds: 10,
      room_seconds: 10,
      overall_seconds: 20,
    },
  };
  const shown = new Set();
  for (let cycle = 0; cycle < 8; cycle++) {
    const frame = rotationFrame(short, many, anchor + cycle * 40000);
    for (const r of pageItems(many, 6, frame.elapsed, 20).items)
      shown.add(r.slug);
  }
  assert.equal(shown.size, 24);
});

test("nested room and tied-leader paging gives every leader a turn", () => {
  const twelveRooms = Array.from({ length: 12 }, (_, i) => ({ id: i }));
  const twelveLeaders = Array.from({ length: 12 }, (_, i) => ({ id: i }));
  const seen = new Set();
  for (let at = 0; at < 400000; at += 10000) {
    const rooms = pageItems(twelveRooms, 6, at, 20);
    if (rooms.items.some((r) => r.id === 0))
      for (const t of pageItems(twelveLeaders, 3, rooms.visibleElapsed).items)
        seen.add(t.id);
  }
  assert.equal(seen.size, 12);
});

test("manual pre-event pages advance independently of refreshed response timestamps", () => {
  const first = manualOverviewElapsed(anchor);
  assert.equal(manualOverviewElapsed(anchor + 25000) - first, 25000);
  assert.equal(manualOverviewElapsed(0), 0);
});
