import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {
  ARCHIVE_FORMAT,
  MAX_ARCHIVE_BYTES,
  archiveMarkdownHTML,
  archivePage,
  filterArchiveTimeline,
  parseArchiveJSON,
  parseEventArchive,
  timelineKey,
} from "../src/archive-format.ts";

function fixture() {
  return {
    format: ARCHIVE_FORMAT,
    schema_version: 1,
    archive_id: "synthetic-archive-1",
    exported_at: "2026-11-10T12:30:00.123456Z",
    producer: { name: "Open Tech Quest", plugin_version: "test" },
    data: {
      event: { name: "Practice event", status: "published", snapshot: null },
      results: {
        scoring_version: 2,
        rooms: [{ id: 1, name: "Room", slug: "room", total: 2 }],
        teams: [
          {
            id: 1,
            name: "Shared first",
            rank: 1,
            total: 2,
            points: -1,
            gross_points: 0,
            hint_costs: 1,
            overall_elapsed_ms: 1234,
            room_progress: [
              {
                room_id: 1,
                points: -1,
                solved: 2,
                total: 2,
                rank: 1,
                complete: true,
                milestone_elapsed_ms: 1234,
              },
            ],
          },
          {
            id: 2,
            name: "Also first",
            rank: 1,
            total: 1,
            points: -1,
            gross_points: 0,
            hint_costs: 1,
            overall_elapsed_ms: 1234,
            room_progress: [
              {
                room_id: 1,
                points: -1,
                solved: 1,
                total: 2,
                rank: 2,
                complete: false,
                milestone_elapsed_ms: 1234,
              },
            ],
          },
        ],
      },
      participants: [{ id: 1, name: "Participant" }],
      teams: [{ id: 1, name: "Shared first", members: [1] }],
      rooms: [{ id: 1, name: "Room", steps: [] }],
      challenges: [
        { id: 1, name: "Challenge", description: "<script>bad()</script>" },
      ],
      hints: [],
      starts: [],
      pauses: [],
      attempts: [],
      hint_purchases: [],
      corrections: [],
      exclusions: [],
      audit: [],
      timeline: [
        {
          id: "room.start:1",
          at: "2026-11-10T12:00:00.123456Z",
          type: "room.start",
          team_id: 1,
          room_id: 1,
          summary: "Room started",
        },
      ],
    },
  };
}
function sign(document) {
  // Independent integer-only canonicalizer; Python compatibility is additionally
  // asserted against explicit canonical strings for floats and Unicode ordering.
  const canonical = (value) =>
    Array.isArray(value)
      ? "[" + value.map(canonical).join(",") + "]"
      : value !== null && typeof value === "object"
        ? "{" +
          Object.keys(value)
            .sort()
            .map((key) => JSON.stringify(key) + ":" + canonical(value[key]))
            .join(",") +
          "}"
        : JSON.stringify(value);
  const unsigned = { ...document };
  delete unsigned.integrity;
  return {
    ...unsigned,
    integrity: {
      algorithm: "sha256",
      sha256: createHash("sha256").update(canonical(unsigned)).digest("hex"),
    },
  };
}

test("verified archive retains server ranks, negative points and published snapshot without recomputation", async () => {
  const original = fixture();
  original.data.event.snapshot = { preserved: "verbatim", count_based: true };
  const result = await parseEventArchive(
    JSON.stringify(sign(original), null, 2),
  );
  assert.deepEqual(
    result.data.results.teams.map((row) => row.rank),
    [1, 1],
  );
  assert.deepEqual(
    result.data.results.teams.map((row) => row.points),
    [-1, -1],
  );
  assert.equal(result.data.event.snapshot.preserved, "verbatim");
  assert.equal(result.data.challenges[0].description, "<script>bad()</script>"); // Data, never trusted HTML.
});

test("Python canonical compatibility preserves exact float tokens, exponent spelling and code-point key order", () => {
  const source =
    '{"z":1.0,"😀":1e-07,"\uffff":-0.0,"a":{"b":0.0001},"integrity":{"sha256":"ignored"}}';
  assert.equal(
    parseArchiveJSON(source).canonical,
    '{"a":{"b":0.0001},"z":1.0,"\uffff":-0.0,"😀":1e-07}',
  );
  assert.equal(
    parseArchiveJSON('{"value":1.25e+20,"integrity":{}}').canonical,
    '{"value":1.25e+20}',
  );
  assert.equal(
    parseArchiveJSON('{"nested":{"integrity":"kept"},"integrity":{}}')
      .canonical,
    '{"nested":{"integrity":"kept"}}',
  );
});

test("an official Python float-containing export verifies without reserializing 1.0 into 1", async () => {
  const document = fixture();
  document.data.event.snapshot = { ratio: "FLOAT_LITERAL" };
  let source = JSON.stringify(sign(document)).replace('"FLOAT_LITERAL"', "1.0");
  const canonical = parseArchiveJSON(source).canonical;
  const digest = createHash("sha256").update(canonical).digest("hex");
  source = source.replace(
    /"sha256":"[a-f0-9]{64}"/,
    '"sha256":"' + digest + '"',
  );
  const parsed = await parseEventArchive(source);
  assert.equal(parsed.data.event.snapshot.ratio, 1);
});

test("whitespace, property order and escape spelling do not change a canonical checksum", async () => {
  const original = sign(fixture());
  const reordered = {
    integrity: original.integrity,
    data: original.data,
    producer: original.producer,
    exported_at: original.exported_at,
    archive_id: original.archive_id,
    schema_version: 1,
    format: ARCHIVE_FORMAT,
  };
  const source = JSON.stringify(reordered, null, 4).replace(
    '"Practice event"',
    '"Prac\\u0074ice event"',
  );
  assert.equal(
    (await parseEventArchive(source)).data.event.name,
    "Practice event",
  );
});

test("modified or truncated content, unknown formats and unsupported versions are rejected before rendering", async () => {
  const source = JSON.stringify(sign(fixture()));
  await assert.rejects(
    parseEventArchive(source.replace("Practice event", "Changed event")),
    /checksum does not match/,
  );
  await assert.rejects(parseEventArchive(source.slice(0, -10)), /JSON/);
  await assert.rejects(
    parseEventArchive(
      JSON.stringify(sign({ ...fixture(), schema_version: 2 })),
    ),
    /not supported/,
  );
  await assert.rejects(
    parseEventArchive('{"format":"another-format"}'),
    /not an Open Tech Quest/,
  );
});

test("malformed results and timeline data are rejected even when their checksums match", async () => {
  for (const mutate of [
    (doc) => (doc.data.results.teams[0].room_progress = "wrong"),
    (doc) => (doc.data.results.teams[0].name = { unexpected: "object" }),
    (doc) => (doc.data.results.rooms[0].name = null),
    (doc) => (doc.data.timeline[0].at = "2026-02-30T00:00:00Z"),
    (doc) => (doc.data.timeline[0].team_id = "1"),
    (doc) => (doc.data.teams[0].members = [{ unexpected: "object" }]),
    (doc) => doc.data.participants.push({ id: 1, name: "Duplicate" }),
  ]) {
    const document = fixture();
    mutate(document);
    await assert.rejects(
      parseEventArchive(JSON.stringify(sign(document))),
      /invalid|duplicate/,
    );
  }
});

test("legacy count-based snapshots remain readable without synthesizing points", async () => {
  const document = fixture();
  document.data.results.scoring_version = 1;
  for (const row of document.data.results.teams) {
    delete row.points;
    delete row.gross_points;
    delete row.hint_costs;
    for (const progress of row.room_progress) delete progress.points;
  }
  const parsed = await parseEventArchive(JSON.stringify(sign(document)));
  assert.equal(parsed.data.results.scoring_version, 1);
  assert.equal(parsed.data.results.teams[0].points, undefined);
  assert.deepEqual(
    parsed.data.results.teams.map((row) => row.rank),
    [1, 1],
  );
});

test("untrusted JSON cannot use duplicate keys, non-JSON whitespace, excessive depth or unsafe numbers", () => {
  for (const source of [
    '{"a":1,"a":2}',
    '{"a":1,}',
    "[1,]",
    '{"a":NaN}',
    '{"a":1e999}',
    '{"a":9007199254740993}',
    '{"a":"\\ud800"}',
    "\u00a0{}",
    "[".repeat(42) + "]".repeat(42),
  ]) {
    assert.throws(() => parseArchiveJSON(source));
  }
  const parsed = parseArchiveJSON(
    '{"__proto__":{"polluted":true},"constructor":"safe data"}',
  );
  assert.equal(Object.getPrototypeOf(parsed.value), null);
  assert.equal({}.polluted, undefined);
});

test("oversized imports are refused before parsing or hashing", async () => {
  await assert.rejects(
    parseEventArchive(" ".repeat(MAX_ARCHIVE_BYTES + 1)),
    /64 MiB/,
  );
});

test("timeline order retains microseconds and filters preserve team/room/type isolation", () => {
  assert.ok(
    timelineKey("2026-11-10T12:00:00.1Z") <
      timelineKey("2026-11-10T12:00:00.100001Z"),
  );
  const rows = [
    { team_id: 1, room_id: 2, type: "attempt" },
    { team_id: 2, room_id: 2, type: "attempt" },
    { team_id: 1, room_id: 3, type: "hint.purchase" },
  ];
  assert.deepEqual(
    filterArchiveTimeline(rows, { team: "1", room: "2", type: "attempt" }),
    [rows[0]],
  );
  assert.deepEqual(
    filterArchiveTimeline(rows, { team: "", room: "", type: "hint.purchase" }),
    [rows[2]],
  );
});

test("100,000 records render a bounded page and stale page positions clamp after filtering", () => {
  const rows = Array.from({ length: 100000 }, (_, id) => ({
    id,
    team_id: id % 50,
    type: "attempt",
  }));
  const page = archivePage(rows, 1999);
  assert.equal(page.rows.length, 50);
  assert.equal(page.pages, 2000);
  assert.equal(page.rows[0].id, 99950);
  const filtered = filterArchiveTimeline(rows, {
    team: "1",
    room: "",
    type: "",
  });
  const clamped = archivePage(filtered, 1999);
  assert.equal(clamped.pages, 40);
  assert.equal(clamped.page, 39);
  assert.equal(clamped.rows.length, 50);
  assert.deepEqual(archivePage([], 100), {
    page: 0,
    pages: 1,
    rows: [],
    total: 0,
  });
});

test("archive Markdown cannot create imported links or load media, even from raw HTML", () => {
  const html = archiveMarkdownHTML(
    '# A challenge\n\n[Open lab](https://example.com/lab) ![Diagram](https://example.com/image.png)\n\n<img src="https://example.com/tracker"><script>alert(1)</script><iframe src="https://example.com/frame"></iframe>',
  );
  assert.match(html, /<h1>A challenge<\/h1>/);
  assert.match(html, /Open lab/);
  assert.match(html, /Diagram/);
  assert.doesNotMatch(html, /<(?:a|img|script|iframe)\b/i);
  assert.match(html, /&lt;img/);
});
