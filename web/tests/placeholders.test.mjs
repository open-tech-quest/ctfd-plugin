import assert from "node:assert/strict";
import test from "node:test";
import { marked } from "marked";
import {
  BODY_PLACEHOLDERS,
  EXAMPLE_PLACEHOLDER_CONTEXT,
  participantPlaceholderContext,
  preparePlaceholderSource,
  resolvePlaceholderText,
  resolvePlaceholderURL,
  applyBodyPlaceholders,
} from "../src/placeholders.ts";

const base = "https://quest.example/quest/rooms/openshift";
const viewer = {
  authenticated: true,
  name: "Alice",
  team_id: 41,
  team_name: "Team forty-one",
};
const context = participantPlaceholderContext(viewer, 250);
const url = (source, values = context) =>
  resolvePlaceholderURL(source, values, base);

test("the catalog contains exactly the four documented tokens and explicit example values", () => {
  assert.deepEqual(
    BODY_PLACEHOLDERS.map((item) => item.token),
    ["{{team.number}}", "{{team.name}}", "{{user.name}}", "{{team.points}}"],
  );
  assert.deepEqual(EXAMPLE_PLACEHOLDER_CONTEXT, {
    "team.number": 1,
    "team.name": "Example team",
    "user.name": "Example participant",
    "team.points": 250,
  });
  assert.deepEqual(
    participantPlaceholderContext(viewer, -7, true),
    EXAMPLE_PLACEHOLDER_CONTEXT,
  );
});

test("native IDs keep their gaps and current overall points retain zero and negatives", () => {
  assert.equal(
    resolvePlaceholderText(
      "Team {{team.number}}: {{team.points}}",
      participantPlaceholderContext(viewer, 0),
    ),
    "Team 41: 0",
  );
  assert.equal(
    resolvePlaceholderText(
      "{{team.points}}",
      participantPlaceholderContext(viewer, -7),
    ),
    "-7",
  );
  assert.equal(
    url("https://team-{{team.number}}.apps.example/start").url,
    "https://team-41.apps.example/start",
  );
});

test("unassigned, pre-release and guest viewers cannot infer an assignment or team balance", () => {
  for (const partial of [
    { ...viewer, team_id: null, team_name: null },
    { ...viewer, team_id: null, team_name: "Hidden team name" },
    { ...viewer, team_id: 0 },
    { ...viewer, authenticated: false },
  ]) {
    const result = participantPlaceholderContext(partial, 999);
    assert.equal(result["team.number"], null);
    assert.equal(result["team.name"], null);
    assert.equal(result["team.points"], null);
    assert.equal(url("https://team{{team.number}}.example", result).url, null);
  }
  assert.equal(
    participantPlaceholderContext({ ...viewer, team_id: null }, 999)[
      "user.name"
    ],
    "Alice",
  );
  assert.equal(
    participantPlaceholderContext({ ...viewer, authenticated: false }, 999)[
      "user.name"
    ],
    null,
  );
});

test("missing and unknown text stays literal without invented values or expression evaluation", () => {
  const missing = participantPlaceholderContext(
    { ...viewer, team_id: null },
    null,
  );
  assert.equal(
    resolvePlaceholderText(
      "{{team.number}} {{team.name}} {{team.points}} {{user.name}}",
      missing,
    ),
    "{{team.number}} {{team.name}} {{team.points}} Alice",
  );
  assert.equal(
    resolvePlaceholderText(
      "{{team.id}} {{ team.name }} {{user.name.toUpperCase()}} {{constructor}}",
      context,
    ),
    "{{team.id}} {{ team.name }} {{user.name.toUpperCase()}} {{constructor}}",
  );
});

test("substituted names remain literal and are never recursively resolved", () => {
  const malicious = {
    ...context,
    "team.name": "**bold** <img src=x onerror=alert(1)> {{team.points}}",
    "user.name": '[evil](javascript:alert(1)) & "quotes"',
  };
  assert.equal(
    resolvePlaceholderText("{{team.name}} / {{user.name}}", malicious),
    malicious["team.name"] + " / " + malicious["user.name"],
  );
  assert.equal(
    resolvePlaceholderText("{{team.points}}", { ...context, "team.points": 0 }),
    "0",
  );
});

test("backslash-escaped tokens survive Markdown's escape removal and even backslashes retain normal substitution", () => {
  for (const source of [
    String.raw`\{{team.name}}`,
    String.raw`\{\{team.name\}\}`,
    String.raw`{\{team.name}}`,
    String.raw`{{team.name\}}`,
  ]) {
    const prepared = preparePlaceholderSource(source);
    const parsed = marked
      .parse(prepared.source)
      .replace(/<\/?p>/g, "")
      .trim();
    assert.equal(
      resolvePlaceholderText(parsed, context, prepared.literals),
      "{{team.name}}",
      source,
    );
  }
  const even = preparePlaceholderSource(String.raw`\\{{team.name}}`);
  assert.equal(
    resolvePlaceholderText(
      marked
        .parse(even.source)
        .replace(/<\/?p>/g, "")
        .trim(),
      context,
      even.literals,
    ),
    "\\Team forty-one",
  );
  const odd = preparePlaceholderSource(String.raw`\\\{{team.name}}`);
  assert.equal(
    resolvePlaceholderText(
      marked
        .parse(odd.source)
        .replace(/<\/?p>/g, "")
        .trim(),
      context,
      odd.literals,
    ),
    String.raw`\{{team.name}}`,
  );
});

test("inline and fenced code use the same explicit token rules", () => {
  for (const source of [
    "`{{user.name}}`",
    "```sh\necho {{user.name}}\n```\n",
  ]) {
    const prepared = preparePlaceholderSource(source);
    const parsed = marked.parse(prepared.source);
    const code = /<code[^>]*>([\s\S]*?)<\/code>/.exec(parsed)[1];
    assert.ok(
      resolvePlaceholderText(code, context, prepared.literals).includes(
        "Alice",
      ),
    );
  }
});

test("marker-looking names cannot be reinterpreted as escaped literals", () => {
  const prepared = preparePlaceholderSource(
    String.raw`\{{team.name}} {{user.name}}`,
  );
  const marker = Object.keys(prepared.literals)[0];
  assert.equal(
    resolvePlaceholderText(
      prepared.source,
      { ...context, "user.name": marker },
      prepared.literals,
    ),
    "{{team.name}} " + marker,
  );
});

test("existing marker-like source text and inherited properties never become context", () => {
  const source = String.raw`OTQ_LITERAL_PLACEHOLDER_0_0_END \{{team.name}} {{team.name}}`;
  const prepared = preparePlaceholderSource(source);
  assert.equal(
    resolvePlaceholderText(prepared.source, context, prepared.literals),
    "OTQ_LITERAL_PLACEHOLDER_0_0_END {{team.name}} Team forty-one",
  );
  assert.equal(
    resolvePlaceholderText(
      "{{team.name}} {{constructor}} {{__proto__}}",
      Object.create(context),
    ),
    "{{team.name}} {{constructor}} {{__proto__}}",
  );
});

test("raw and Markdown-encoded braces resolve in path, query and fragments with strict component encoding", () => {
  const values = {
    ...context,
    "team.name": "A / B & Δ",
    "user.name": "O'Reilly?#",
  };
  assert.equal(
    url("/lab/{{team.name}}?viewer={{user.name}}#{{team.name}}", values).url,
    "/lab/A%20%2F%20B%20%26%20%CE%94?viewer=O%27Reilly%3F%23#A%20%2F%20B%20%26%20%CE%94",
  );
  const html = marked.parse(
    "[Lab](https://team-{{team.number}}.example/?points={{team.points}})",
  );
  const href = /href="([^"]+)"/.exec(html)[1];
  assert.equal(url(href).url, "https://team-41.example/?points=250");
  assert.equal(url("/x/%7b%7bteam.number%7d%7d").url, "/x/41");
});

test("unsafe name characters cannot escape a URL component or create markup", () => {
  const values = {
    ...context,
    "user.name": '\" onmouseover=\"alert(1) <script> /@evil.example',
  };
  const result = url("https://lab.example/start?name={{user.name}}", values);
  assert.ok(result.url.startsWith("https://lab.example/start?name=%22"));
  assert.equal(new URL(result.url).origin, "https://lab.example");
  assert.equal(
    new URL(result.url).searchParams.get("name"),
    values["user.name"],
  );
});

test("names cannot choose protocols, hostnames, credentials or ports", () => {
  for (const source of [
    "{{user.name}}://lab.example",
    "https://{{team.name}}.example",
    "//{{user.name}}.example/x",
    "https://{{user.name}}@lab.example",
    "https://lab.example:{{team.number}}/x",
    "mailto:{{user.name}}@example.test",
  ]) {
    assert.equal(url(source).url, null, source);
  }
  assert.equal(url("javascript:alert({{team.number}})").url, null);
  assert.equal(url("//team{{team.number}}.example").url, "//team41.example");
});

test("browser-normalized HTTP forms cannot move names from a path into the hostname", () => {
  const values = { ...context, "team.name": "other-lab" };
  for (const source of [
    "http:/{{team.name}}.example/task",
    "http:{{team.name}}.example/task",
    String.raw`https:\\{{team.name}}.example/task`,
    " https://{{team.name}}.example/task",
    "\thttps://{{team.name}}.example/task",
    "ht\ttps://{{team.name}}.example/task",
    "https:\n//{{team.name}}.example/task",
    "https://\r{{team.name}}.example/task",
    "///{{team.name}}.example/task",
    "https:////{{team.name}}.example/task",
  ])
    assert.equal(url(source, values).url, null, source);
  // Existing non-templated links retain the sanitizer's established behavior.
  assert.equal(
    url("http:/ordinary.example/task").url,
    "http:/ordinary.example/task",
  );
});

test("missing or unknown URL values disable the destination with an explanation", () => {
  const missing = { ...context, "team.name": null };
  assert.match(
    url("/team/{{team.name}}", missing).reason,
    /Team name.*not available/,
  );
  assert.match(url("/team/{{team.id}}").reason, /Unknown body placeholder/);
  assert.equal(url("/team/{{team.id}}").url, null);
  assert.equal(url("/team/%7B%7Bteam.id%7D%7D").url, null);
});

test("names that would normalize to parent/current URL segments are refused, but query values remain data", () => {
  for (const name of [".", ".."]) {
    assert.equal(
      url("/labs/{{team.name}}/task", { ...context, "team.name": name }).url,
      null,
    );
    assert.equal(
      url("/labs/?name={{team.name}}", { ...context, "team.name": name }).url,
      "/labs/?name=" + name,
    );
  }
});

test("escaped literal URL tokens stay literal and ordinary links retain their original behavior", () => {
  const prepared = preparePlaceholderSource(
    String.raw`/literal/\{{team.name}}`,
  );
  assert.equal(
    resolvePlaceholderURL(prepared.source, context, base, prepared.literals)
      .url,
    "/literal/%7B%7Bteam.name%7D%7D",
  );
  for (const source of [
    "mailto:staff@example.test",
    "tel:+491234",
    "#details",
    "/files/notes.txt",
    "https://lab.example/task?target=_blank",
  ])
    assert.deepEqual(url(source), { url: source, reason: null });
});

test("sanitized SVG anchors use href despite their lowercase tagName", () => {
  const previousDocument = globalThis.document,
    previousFilter = globalThis.NodeFilter;
  const attributes = new Map([["href", "https://lab.example/{{team.number}}"]]);
  const anchor = {
    tagName: "a",
    localName: "a",
    getAttribute: (name) => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, value),
  };
  globalThis.document = { createTreeWalker: () => ({ nextNode: () => false }) };
  globalThis.NodeFilter = { SHOW_TEXT: 4 };
  try {
    applyBodyPlaceholders(
      {
        querySelectorAll: (selector) =>
          selector === "a[href], img[src]" ? [anchor] : [],
      },
      context,
      base,
    );
    assert.equal(attributes.get("href"), "https://lab.example/41");
  } finally {
    globalThis.document = previousDocument;
    globalThis.NodeFilter = previousFilter;
  }
});
