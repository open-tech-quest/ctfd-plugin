import assert from "node:assert/strict";
import test from "node:test";
import { challengeDraftOf, challengePayloadOf } from "../src/challenge-draft.ts";
import { canConfirmMaintenance, maintenanceApproval, maintenanceRisk } from "../src/maintenance-risk.ts";
import { challengePoints, hintCostAdjustment } from "../src/score-presentation.ts";
import { saveChallengeWrite } from "../src/challenge-write.ts";

function savedChallenge() {
  return {
    id: 7, name: "Challenge", value: 100, state: "visible", description: "Solve it",
    connection_info: "", flags: [{content: "synthetic-answer", case_insensitive: false}],
    hints: [
      {id: 11, title: "First", content: "Clue one", cost: 25, prerequisites: []},
      {id: 12, title: "Second", content: "Clue two", cost: 50, prerequisites: [11]},
    ],
  };
}

function reviewed(impact = {}) {
  return {revision: "bound-to-content-and-scope", competitive_fields: ["value", "hints"],
    requires_confirmation: true, warnings: [], impact: {
      counted_teams: 3, solve_records: 3, hint_purchase_teams: 2, hint_receipts: 2,
      completed_teams: 1, team_scores: [], ...impact,
    }};
}

test("new and reloaded editor drafts never opt into repricing old activity", () => {
  const saved = savedChallenge(), original = structuredClone(saved);
  const draft = challengeDraftOf(saved);
  assert.equal(draft.adjust_existing_solves, false);
  assert.ok(draft.hints.every(hint => hint.adjust_existing_purchases === false));
  draft.adjust_existing_solves = true;
  draft.hints[0].adjust_existing_purchases = true;
  draft.hints[1].prerequisites.push(99);
  assert.deepEqual(saved, original);
  assert.equal(challengeDraftOf(saved).adjust_existing_solves, false);
  assert.ok(challengeDraftOf(saved).hints.every(hint => !hint.adjust_existing_purchases));
  assert.equal(challengeDraftOf().adjust_existing_solves, false);
});

test("live and paused numeric edits explicitly choose future-only by default", () => {
  const saved = savedChallenge(), draft = challengeDraftOf(saved);
  draft.value = 200;
  draft.hints[0].cost = 40;
  for (const phase of ["live", "paused"]) {
    const payload = challengePayloadOf(draft, saved, phase);
    assert.deepEqual(payload.pricing, {challenge_existing: false, hint_existing_ids: []});
    assert.equal(payload.value, 200);
    assert.equal(payload.hints[0].cost, 40);
    assert.ok(!("adjust_existing_solves" in payload));
    assert.ok(payload.hints.every(hint => !("adjust_existing_purchases" in hint) && !("prerequisites" in hint)));
  }
});

test("challenge and individual hint scopes stay independent and new hints have no old purchases", () => {
  const saved = savedChallenge(), draft = challengeDraftOf(saved);
  draft.value = 300;
  draft.hints[0].cost = 10;
  draft.hints[0].adjust_existing_purchases = true;
  draft.hints[1].cost = 100;
  draft.hints.push({title: "New", content: "New clue", cost: 0, prerequisites: [], adjust_existing_purchases: true});
  assert.deepEqual(challengePayloadOf(draft, saved, "live").pricing,
    {challenge_existing: false, hint_existing_ids: [11]});
  draft.adjust_existing_solves = true;
  assert.deepEqual(challengePayloadOf(draft, saved, "live").pricing,
    {challenge_existing: true, hint_existing_ids: [11]});
});

test("an explicit later alignment can apply the unchanged current price to old cohorts", () => {
  const saved = savedChallenge(), draft = challengeDraftOf(saved);
  assert.equal(challengePayloadOf(draft, saved, "live").pricing, undefined);
  draft.adjust_existing_solves = true;
  draft.hints[1].adjust_existing_purchases = true;
  assert.deepEqual(challengePayloadOf(draft, saved, "live").pricing,
    {challenge_existing: true, hint_existing_ids: [12]});
  draft.adjust_existing_solves = false;
  draft.hints[1].adjust_existing_purchases = false;
  draft.value = 200;
  draft.value = saved.value;
  assert.equal(challengePayloadOf(draft, saved, "live").pricing, undefined);
});

test("draft configuration and new challenge creation do not send live repricing policy", () => {
  const saved = savedChallenge(), draft = challengeDraftOf(saved);
  draft.value = 200;
  draft.adjust_existing_solves = true;
  draft.hints[0].cost = 0;
  draft.hints[0].adjust_existing_purchases = true;
  assert.equal(challengePayloadOf(draft, saved, "draft").pricing, undefined);
  assert.equal(challengePayloadOf(draft, undefined, "live").pricing, undefined);
  const text = challengeDraftOf(saved);
  text.description = "Corrected words";
  assert.equal(challengePayloadOf(text, saved, "paused").pricing, undefined);
});

test("a failed priced write leaves the selected scopes and reviewed body intact without retry", async () => {
  const saved = savedChallenge(), draft = challengeDraftOf(saved);
  draft.value = 50;
  draft.adjust_existing_solves = true;
  draft.hints[0].cost = 80;
  draft.hints[0].adjust_existing_purchases = true;
  const before = structuredClone(draft), payload = challengePayloadOf(draft, saved, "live"), calls = [];
  const approval = {revision: "quoted-price-scope", reason: "Correct points", confirm: true, acknowledge_negative: true};
  await assert.rejects(saveChallengeWrite(async (...args) => {
    calls.push(args); throw new Error("New negative balances; review again");
  }, {room: "rhel", id: 7, revision: "saved"}, payload, approval), /review again/);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0][2].pricing, {challenge_existing: true, hint_existing_ids: [11]});
  assert.deepEqual(calls[0][2].maintenance, approval);
  assert.deepEqual(draft, before);
});

test("new negative room points require acknowledgment even when overall points stay positive", () => {
  const review = reviewed({teams_with_score_changes: 1, team_scores: [{team_id: 3,
    before_points: 100, after_points: 50, before_room_points: 20, after_room_points: -30,
    before_solved: 1, after_solved: 1}]});
  const risk = maintenanceRisk(review.impact);
  assert.equal(risk.affectedTeams, 1);
  assert.equal(risk.newlyNegativeOverall, 0);
  assert.equal(risk.newlyNegativeRoom, 1);
  assert.equal(canConfirmMaintenance(review, "Fix the hint price", false), false);
  assert.throws(() => maintenanceApproval(review, "Fix the hint price", false), /acknowledge/);
  assert.deepEqual(maintenanceApproval(review, "  Fix the hint price  ", true), {
    revision: review.revision, reason: "Fix the hint price", confirm: true, acknowledge_negative: true,
  });
});

test("server risk totals are retained separately and cannot be hidden by a short team list", () => {
  const review = reviewed({teams_with_score_changes: 50, negative_overall_teams: 12,
    negative_room_teams: 18, new_negative_overall_teams: 8, new_negative_room_teams: 15});
  assert.deepEqual(maintenanceRisk(review.impact), {affectedTeams: 50, negativeOverall: 12,
    negativeRoom: 18, newlyNegativeOverall: 8, newlyNegativeRoom: 15, needsAcknowledgment: true});
  assert.equal(canConfirmMaintenance(review, "Reviewed price update", false), false);
  assert.equal(canConfirmMaintenance(review, "Reviewed price update", true), true);
});

test("existing negative balances remain visible without inventing new crossings", () => {
  const review = reviewed({team_scores: [{team_id: 3, before_points: -40, after_points: -30,
    before_room_points: -40, after_room_points: -30, before_solved: 1, after_solved: 1}]});
  const risk = maintenanceRisk(review.impact);
  assert.equal(risk.negativeOverall, 1);
  assert.equal(risk.negativeRoom, 1);
  assert.equal(risk.needsAcknowledgment, false);
  assert.deepEqual(maintenanceApproval(review, "Return some points", false), {
    revision: review.revision, reason: "Return some points", confirm: true,
  });
});

test("acknowledgment never overrides stale review, missing reason or an invalid audit reason", () => {
  const review = reviewed({new_negative_overall_teams: 1});
  assert.equal(canConfirmMaintenance(review, "Reviewed", true, true), false);
  for (const reason of [" ", "ab", "x".repeat(1001), "bad\0reason"]) {
    assert.equal(canConfirmMaintenance(review, reason, true), false);
  }
});

test("solved cards show actual cohort credit including zero; unsolved and local previews use current values", () => {
  const step = {id: 7, title: "Solve", state: "solved", value: 200, earned_points: 100};
  assert.equal(challengePoints(step, 250), 100);
  assert.equal(challengePoints({...step, earned_points: 0}, 250), 0);
  assert.equal(challengePoints({...step, state: "available"}, 250), 250);
  assert.equal(challengePoints({...step, earned_points: undefined}), 200);
});

test("hint adjustments explain the effective deduction while retaining the original receipt", () => {
  assert.equal(hintCostAdjustment(50, 50), null);
  assert.equal(hintCostAdjustment(undefined, 50), null);
  assert.equal(hintCostAdjustment(80, undefined), null);
  assert.match(hintCostAdjustment(80, 50), /Original purchase: 50 points.*deduction.*80 points/);
  assert.match(hintCostAdjustment(0, 50), /Original purchase: 50 points.*deduction.*0 points/);
  assert.match(hintCostAdjustment(50, 0), /Original purchase: 0 points.*deduction.*50 points/);
});
