import assert from "node:assert/strict";
import test from "node:test";
import { markdownLinkTarget } from "../src/markdown-links.ts";
const base = "https://quest.example/quest/rooms/openshift";
test("external resource links open a new tab without altering their URLs", () => {
  for (const href of [
    "https://red.ht/otq-fusion-hci",
    "https://red.ht/otq-fusion-hci?target=_blank",
    "//lab.example/task",
    "http://quest.example/task",
    "https://quest.example:8443/task",
  ])
    assert.equal(markdownLinkTarget(href, base, null), "_blank");
});
test("same-origin paths, fragments and non-web links keep their normal navigation", () => {
  for (const href of [
    "/quest/standings",
    "../ansible",
    "#details",
    "",
    "https://quest.example/files/readme.txt",
    "mailto:staff@example.test",
    "tel:+491234",
    "javascript:alert(1)",
    "data:text/html,test",
  ])
    assert.equal(markdownLinkTarget(href, base, null), null);
});
test("raw HTML can explicitly choose the current tab or a separate tab", () => {
  assert.equal(
    markdownLinkTarget("https://red.ht/otq-fusion-hci", base, "_self"),
    "_self",
  );
  assert.equal(
    markdownLinkTarget("/quest/standings", base, "_blank"),
    "_blank",
  );
  assert.equal(
    markdownLinkTarget("https://red.ht/otq-fusion-hci", base, "reference"),
    "reference",
  );
});
test("malformed destinations do not break rendering", () => {
  assert.equal(markdownLinkTarget("https://[", base, null), null);
});
