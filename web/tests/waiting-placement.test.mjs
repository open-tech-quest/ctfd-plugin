import assert from "node:assert/strict";
import test from "node:test";
import {
  applyWaitingPlacementBody,
  placementIsStale,
  previewWaitingPlacement,
} from "../src/waiting-placement.ts";

const location = {
  venue_room_id: 2,
  table_number: 4,
  room_name: "Titanium",
  label: "Titanium · Table 4",
};
const plan = {
  token: "signed-review",
  capacity: 3,
  event_status: "draft",
  expires_at: "2035-01-01T00:00:00Z",
  participants: [3, 4, 5].map((id) => ({ id, name: `Person ${id}` })),
  groups: [
    {
      team_id: 1,
      name: "Team 1",
      existing_user_ids: [1, 2],
      user_ids: [3],
      location,
    },
    {
      team_id: null,
      name: "Team 2",
      existing_user_ids: [],
      user_ids: [4, 5],
      location: null,
    },
  ],
};
const state = () => ({
  event: { status: "draft", server_now: "2026-11-17T10:00:00Z" },
  onboarding: {
    phase: "finalized",
    team_size: 3,
    require_attendance: false,
    late_admission: { allowed: true },
    fingerprint: "initial",
  },
  participants: [3, 4, 5].map((id) => ({
    id,
    team_id: null,
    banned: false,
    ready_at: null,
  })),
  teams: [
    {
      id: 1,
      name: "Team 1",
      user_ids: [1, 2],
      hidden: false,
      banned: false,
      excluded: false,
      location,
    },
  ],
});

test("new signups and unrelated roster/progress changes preserve an open placement review", () => {
  const data = state();
  data.participants.push({
    id: 6,
    team_id: null,
    banned: false,
    ready_at: null,
  });
  data.onboarding.fingerprint = "changed by new registration";
  data.teams.push({
    id: 9,
    name: "Another team",
    user_ids: [20],
    location: null,
  });
  data.standings = { new_solves: 12 };
  assert.equal(placementIsStale(plan, data), false);
  data.teams[0].user_ids = [2, 1];
  data.teams[0].location = {
    room_name: "Titanium",
    label: location.label,
    table_number: 4,
    venue_room_id: 2,
  };
  assert.equal(placementIsStale(plan, data), false);
});

test("selected-account and destination changes require review again", () => {
  for (const change of [
    (d) => d.participants.shift(),
    (d) => (d.participants[0].team_id = 9),
    (d) => (d.participants[0].banned = true),
    (d) => d.teams[0].user_ids.push(6),
    (d) => (d.teams[0].hidden = true),
    (d) => (d.teams[0].banned = true),
    (d) => (d.teams[0].excluded = true),
    (d) => (d.teams[0].name = "Renamed"),
    (d) => (d.teams[0].location = { ...location, table_number: 5 }),
    (d) => d.teams.push({ id: 2, name: "team 2", user_ids: [] }),
    (d) => (d.event.status = "live"),
    (d) => (d.onboarding.team_size = 4),
    (d) => (d.onboarding.late_admission.allowed = false),
    (d) => (d.onboarding.phase = "naming"),
    (d) => (d.event.server_now = "2035-01-01T00:00:00Z"),
  ]) {
    const data = state();
    change(data);
    assert.equal(placementIsStale(plan, data), true);
  }
  const data = state();
  data.onboarding.require_attendance = true;
  assert.equal(placementIsStale(plan, data), true);
  data.participants.forEach((p) => (p.ready_at = "2026-11-17T09:00:00Z"));
  assert.equal(placementIsStale(plan, data), false);
});

test("preview sends one frozen-batch request; apply carries only its signed review and reason", async () => {
  const calls = [];
  const send = async (...args) => {
    calls.push(args);
    return plan;
  };
  assert.deepEqual(await previewWaitingPlacement(send), plan);
  assert.deepEqual(calls, [
    ["/admin/onboarding/waiting", "POST", { preview: true }],
  ]);
  assert.deepEqual(applyWaitingPlacementBody(plan.token, "  Arrival batch  "), {
    preview: false,
    token: plan.token,
    reason: "Arrival batch",
  });
  let attempts = 0;
  await assert.rejects(
    () =>
      previewWaitingPlacement(async () => {
        attempts++;
        throw new Error("Connection lost");
      }),
    /Connection lost/,
  );
  assert.equal(attempts, 1);
});
