import assert from "node:assert/strict";
import test from "node:test";
import {
  reviewChallengeWrite,
  saveChallengeWrite,
} from "../src/challenge-write.ts";
import { purchaseQuotedHint } from "../src/hint-purchase.ts";

test("a safe live text fix binds the saved room revision without a competitive approval", async () => {
  const calls = [];
  const send = async (...args) => {
    calls.push(args);
    return { id: 12 };
  };
  await saveChallengeWrite(
    send,
    { room: "security", id: 12, revision: "saved-v1" },
    {
      description: "Corrected instructions",
      connection_info: "https://lab.example.test",
    },
  );
  assert.deepEqual(calls, [
    [
      "/manage/rooms/security/challenges/12",
      "PATCH",
      {
        description: "Corrected instructions",
        connection_info: "https://lab.example.test",
      },
      undefined,
      { "X-Quest-Content-Revision": "saved-v1" },
    ],
  ]);
});

test("review has no mutation side effect and its approval is sent only on explicit save", async () => {
  const calls = [],
    changes = {
      value: 150,
      hints: [{ id: 71, title: "Clue", content: "Fixture clue", cost: 25 }],
    };
  const send = async (...args) => {
    calls.push(args);
    return { revision: "review-token", requires_confirmation: true };
  };
  const target = { room: "security", id: 12, revision: "saved-v1" };
  const review = await reviewChallengeWrite(send, target, changes);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/manage/rooms/security/challenges/12/review");
  assert.deepEqual(calls[0][2], { changes, revision: "saved-v1" });
  // Cancelling here produces no PATCH; a later explicit confirmation supplies
  // the opaque review token and reason alongside exactly the reviewed fields.
  await saveChallengeWrite(send, target, changes, {
    revision: review.revision,
    reason: "Repair scoring",
    confirm: true,
  });
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1][2], {
    ...changes,
    maintenance: {
      revision: "review-token",
      reason: "Repair scoring",
      confirm: true,
    },
  });
  assert.equal(calls[1][2].hints[0].id, 71);
  assert.equal(calls[1][4]["X-Quest-Content-Revision"], "saved-v1");
});

test("live append uses collection review and preserves the collection revision on creation", async () => {
  const calls = [],
    changes = { name: "New challenge", state: "visible", value: 100 };
  const send = async (...args) => {
    calls.push(args);
    return {};
  };
  const target = { room: "security", revision: "saved-room" };
  await reviewChallengeWrite(send, target, changes);
  await saveChallengeWrite(send, target, changes, {
    revision: "append-review",
    reason: "Extend room",
    confirm: true,
  });
  assert.equal(calls[0][0], "/manage/rooms/security/challenges/review");
  assert.equal(calls[1][0], "/manage/rooms/security/challenges");
  assert.equal(calls[1][1], "POST");
  assert.equal(calls[1][4]["X-Quest-Content-Revision"], "saved-room");
});

test("a stale write failure propagates without retrying, changing IDs or clearing the submitted draft", async () => {
  const changes = {
    state: "hidden",
    hints: [{ id: 71, content: "Retain authored clue", cost: 25 }],
  };
  const original = structuredClone(changes);
  const failure = new Error(
    "Saved room content changed. Review your changes again.",
  );
  let calls = 0;
  await assert.rejects(
    saveChallengeWrite(
      async () => {
        calls++;
        throw failure;
      },
      { room: "security", id: 12, revision: "old-content" },
      changes,
      { revision: "old-review", reason: "Withdraw broken step", confirm: true },
    ),
    (error) => error === failure,
  );
  assert.equal(calls, 1);
  assert.deepEqual(changes, original);
});

test("native hint confirmation sends the originally displayed quote without extra native schema fields", async () => {
  const calls = [];
  const hint = { id: 71, quote_revision: "price25-content-v1" };
  await purchaseQuotedHint(async (...args) => {
    calls.push(args);
  }, hint);
  assert.deepEqual(calls, [
    [
      "/api/v1/unlocks",
      "POST",
      { target: 71, type: "hints" },
      undefined,
      { "X-Quest-Hint-Quote": "price25-content-v1" },
    ],
  ]);
});

test("a missing quote never sends a native purchase; the participant must reopen the hint", () => {
  let calls = 0;
  assert.throws(
    () =>
      purchaseQuotedHint(
        async () => {
          calls++;
        },
        { id: 71 },
      ),
    /Refresh the page and reopen/,
  );
  assert.equal(calls, 0);
});

test("a rejected old price is never retried or silently substituted with a fresh quote", async () => {
  const failure = new Error("This hint changed. Reopen it before confirming.");
  let calls = 0;
  await assert.rejects(
    purchaseQuotedHint(
      async () => {
        calls++;
        throw failure;
      },
      { id: 71, quote_revision: "old-price" },
    ),
    (error) => error === failure,
  );
  assert.equal(calls, 1);
});

test("tab-local journey uses its scoped transport without requiring or fabricating a native quote", async () => {
  const calls = [];
  await purchaseQuotedHint(
    async (...args) => {
      calls.push(args);
    },
    { id: 71 },
    true,
  );
  assert.deepEqual(calls, [
    [
      "/api/v1/unlocks",
      "POST",
      { target: 71, type: "hints" },
      undefined,
      undefined,
    ],
  ]);
});
