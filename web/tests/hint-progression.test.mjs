import assert from "node:assert/strict";
import test from "node:test";
import { hintProgression, redeemedHintIds } from "../src/hint-progression.ts";
const source = [
  { id: 30, cost: 10, title: "Last" },
  { id: 10, cost: 0, title: "First" },
  { id: 20, cost: 50, title: "Middle" },
];
const visible = (result) =>
  result.visible.map((row) => [row.hint.id, row.number, row.redeemed]);
test("only the next unredeemed hint is offered in stable native-ID order without changing source arrays", () => {
  assert.deepEqual(visible(hintProgression(source, new Set())), [
    [10, 1, false],
  ]);
  assert.deepEqual(visible(hintProgression(source, new Set([10]))), [
    [10, 1, true],
    [20, 2, false],
  ]);
  assert.deepEqual(visible(hintProgression(source, new Set([10, 20]))), [
    [10, 1, true],
    [20, 2, true],
    [30, 3, false],
  ]);
  assert.deepEqual(
    source.map((h) => h.id),
    [30, 10, 20],
  );
});
test("free zero receipts, empty text and teammate-unlocked native content all count as redeemed", () => {
  const hints = [
    { id: 10 },
    { id: 20, content: "" },
    { id: 30 },
    { id: 40, content: "Shared teammate text" },
  ];
  const ids = redeemedHintIds(hints, { 10: 0 }, { 30: "" });
  assert.deepEqual([...ids], [10, 20, 30, 40]);
  assert.equal(hintProgression(hints, ids).complete, true);
});
test("legacy out-of-order purchases remain shown while the first earlier gap remains the offer", () => {
  const later = hintProgression(source, new Set([30]));
  assert.deepEqual(visible(later), [
    [10, 1, false],
    [30, 3, true],
  ]);
  assert.equal(later.nextId, 10);
  assert.deepEqual(visible(hintProgression(source, new Set([10, 30]))), [
    [10, 1, true],
    [20, 2, false],
    [30, 3, true],
  ]);
});
test("empty and completed sequences have distinct terminal states; inspect-all preview still sees every hint", () => {
  assert.deepEqual(hintProgression([], new Set()), {
    visible: [],
    nextId: null,
    empty: true,
    complete: false,
  });
  const finished = hintProgression(source, new Set([10, 20, 30]));
  assert.equal(finished.nextId, null);
  assert.equal(finished.complete, true);
  assert.equal(finished.visible.length, 3);
  assert.deepEqual(visible(hintProgression(source, new Set(), true)), [
    [10, 1, false],
    [20, 2, false],
    [30, 3, false],
  ]);
});
test("cancellation or a failed read with no receipt/text cannot move the next hint", () => {
  const before = hintProgression(source, redeemedHintIds(source, {}, {}));
  assert.equal(before.nextId, 10);
  assert.equal(
    hintProgression(source, redeemedHintIds(source, {}, {})).nextId,
    10,
  );
  assert.equal(
    hintProgression(source, redeemedHintIds(source, { 10: 0 }, {})).nextId,
    20,
  );
});
