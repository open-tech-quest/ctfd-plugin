import assert from "node:assert/strict";
import test from "node:test";
import {
  orderedTeams,
  rankingDuration,
  rankingOrder,
} from "../src/standings-presentation.ts";

test("ranking times retain millisecond differences and handle missing/zero times", () => {
  assert.equal(rankingDuration(10100), "00:00:10.100");
  assert.equal(rankingDuration(10900), "00:00:10.900");
  assert.equal(rankingDuration(3600001), "01:00:00.001");
  assert.equal(rankingDuration(0), "00:00:00.000");
  assert.equal(rankingDuration(null), "—");
});

test("overall presentation follows server ranks including negative scores and genuine ties", () => {
  const teams = [
    {
      id: 1,
      name: "No solves",
      rank: null,
      total: 0,
      points: 0,
      room_progress: [],
    },
    { id: 2, name: "Zero", rank: 2, total: 5, points: 0, room_progress: [] },
    {
      id: 3,
      name: "Negative",
      rank: 3,
      total: 1,
      points: -50,
      room_progress: [],
    },
    { id: 4, name: "Atlas", rank: 1, total: 3, points: 200, room_progress: [] },
    {
      id: 5,
      name: "Aurora",
      rank: 1,
      total: 2,
      points: 200,
      room_progress: [],
    },
  ];
  assert.deepEqual(
    orderedTeams(teams).map((t) => t.id),
    [4, 5, 2, 3, 1],
  );
  assert.deepEqual(
    teams.map((t) => t.id),
    [1, 2, 3, 4, 5],
  );
  assert.equal(teams[3].rank, teams[4].rank);
});

test("room presentation honors room ranks rather than overall points or ranks", () => {
  const teams = [
    {
      id: 1,
      name: "Points leader",
      rank: 1,
      points: 900,
      room_progress: [{ room_id: 7, rank: 2, solved: 8, points: 900 }],
    },
    {
      id: 2,
      name: "Farthest",
      rank: 3,
      points: 500,
      room_progress: [{ room_id: 7, rank: 1, solved: 9, points: 500 }],
    },
    {
      id: 3,
      name: "Not started",
      rank: 2,
      points: 700,
      room_progress: [{ room_id: 7, rank: null, solved: 0, points: -50 }],
    },
  ];
  assert.deepEqual(
    orderedTeams(teams, { id: 7 }).map((t) => t.id),
    [2, 1, 3],
  );
});

test("legacy snapshots keep their stored order and count-based explanation", () => {
  const teams = [
    { id: 1, name: "Legacy leader", rank: 1, total: 10, room_progress: [] },
    { id: 2, name: "Runner up", rank: 2, total: 9, room_progress: [] },
  ];
  assert.deepEqual(
    orderedTeams(teams).map((t) => t.id),
    [1, 2],
  );
  assert.match(rankingOrder({}, true), /consecutive solves/);
  assert.match(
    rankingOrder({ scoring_version: 2 }, true),
    /challenges solved, then net room points, then active time/,
  );
  assert.match(
    rankingOrder({ scoring_version: 2 }, false),
    /Solve count does not break overall ties/,
  );
});
