import assert from "node:assert/strict";
import test from "node:test";
import { insertDescriptionToken } from "../src/description-insertion.ts";
const tokens = [
  "{{team.number}}",
  "{{team.name}}",
  "{{user.name}}",
  "{{team.points}}",
];
test("each supported token inserts at a collapsed caret and preserves surrounding Markdown", () => {
  for (const token of tokens) {
    const result = insertDescriptionToken("Before  after", token, 7, 7, 10000);
    assert.deepEqual(result, {
      value: "Before " + token + " after",
      caret: 7 + token.length,
    });
  }
});
test("a selection is replaced atomically inside a Markdown link without modifying its surrounding URL", () => {
  const value =
    "[Open lab](https://team-PLACEHOLDER.example/path)\nKeep this text";
  const start = value.indexOf("PLACEHOLDER"),
    token = "{{team.number}}";
  const result = insertDescriptionToken(value, token, start, start + 11, 10000);
  assert.equal(
    result.value,
    "[Open lab](https://team-{{team.number}}.example/path)\nKeep this text",
  );
  assert.equal(result.caret, start + token.length);
});
test("textarea UTF-16 offsets preserve Unicode and multiline text around the replacement", () => {
  const value = "😀 Team\nreplace\nRésumé",
    start = value.indexOf("replace"),
    token = "{{user.name}}";
  assert.deepEqual(
    insertDescriptionToken(value, token, start, start + 7, 10000),
    { value: "😀 Team\n{{user.name}}\nRésumé", caret: start + token.length },
  );
});
test("the text limit permits exact fits and selected-text replacement, but never truncates a token or existing text", () => {
  const token = "{{team.points}}",
    value = "x".repeat(20);
  assert.equal(insertDescriptionToken(value, token, 20, 20, 20), null);
  assert.deepEqual(insertDescriptionToken(value, token, 0, token.length, 20), {
    value: token + "x".repeat(20 - token.length),
    caret: token.length,
  });
  assert.equal(
    insertDescriptionToken(value, token, 0, token.length - 1, 20),
    null,
  );
  assert.equal(value, "x".repeat(20));
});
test("a selection retained across a shorter draft reload is bounded to the current text", () => {
  assert.deepEqual(
    insertDescriptionToken("abc", "{{team.name}}", 50, 60, 10000),
    { value: "abc{{team.name}}", caret: 3 + "{{team.name}}".length },
  );
});
