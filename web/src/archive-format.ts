/** Local, read-only event archive parsing. No server or DOM dependencies. */
import { Marked } from "marked";

export const ARCHIVE_FORMAT = "open-tech-quest/event-archive";
export const MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
export const ARCHIVE_PAGE_SIZE = 50;
export type ArchiveValue =
  null | boolean | number | string | ArchiveValue[] | ArchiveRecord;
export interface ArchiveRecord {
  [key: string]: ArchiveValue;
}
export interface ArchiveDocument {
  format: typeof ARCHIVE_FORMAT;
  schema_version: 1;
  archive_id: string;
  exported_at: string;
  producer: ArchiveRecord;
  data: ArchiveRecord & {
    event: ArchiveRecord;
    results: ArchiveRecord;
    participants: ArchiveRecord[];
    teams: ArchiveRecord[];
    rooms: ArchiveRecord[];
    challenges: ArchiveRecord[];
    hints: ArchiveRecord[];
    timeline: ArchiveRecord[];
  };
  integrity: { algorithm: "sha256"; sha256: string };
}

export function archiveText(value: ArchiveValue | undefined, fallback = "—") {
  return typeof value === "string" ? value : fallback;
}
export function archiveNumber(value: ArchiveValue | undefined, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
export function archiveRecords(
  value: ArchiveValue | undefined,
): ArchiveRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/** Raw HTML is shown as text; links/images never become live URL attributes.
 * Consumers must still sanitize the resulting Markdown HTML before insertion.
 */
export function archiveMarkdownHTML(source: string) {
  const escape = (value: string) =>
    value
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  const renderer = new Marked({
    async: false,
    renderer: {
      html: ({ text }) => escape(text),
      image: ({ text }) => escape(text || "[Image omitted]"),
      link({ tokens }) {
        return this.parser.parseInline(tokens);
      },
    },
  });
  return renderer.parse(source, { async: false }) as string;
}
export function isRecord(value: unknown): value is ArchiveRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function invalid(message: string): never {
  throw new Error(message);
}
function codePointOrder(a: string, b: string) {
  const x = Array.from(a),
    y = Array.from(b);
  for (let index = 0; index < Math.min(x.length, y.length); ++index) {
    const difference = x[index].codePointAt(0)! - y[index].codePointAt(0)!;
    if (difference) return difference;
  }
  return x.length - y.length;
}

/** Retain numeric tokens: JSON.parse/stringify would turn Python's 1.0 into 1.
 * Official exports use the same Python number spelling in the document and its
 * canonical checksum. Whitespace and object order are normalized, not numbers.
 */
export function parseArchiveJSON(source: string) {
  let offset = 0,
    nodes = 0;
  function space() {
    while (offset < source.length && " \r\n\t".includes(source[offset]))
      ++offset;
  }
  function string() {
    const start = offset++;
    while (offset < source.length) {
      if (source[offset] === "\\") offset += 2;
      else if (source[offset++] === '"') {
        const value = JSON.parse(source.slice(start, offset)) as string;
        if (value.length > 1024 * 1024)
          invalid("An archive text field exceeds the 1 MiB limit.");
        for (const character of value) {
          const point = character.codePointAt(0)!;
          if (point >= 0xd800 && point <= 0xdfff)
            invalid("The archive contains invalid Unicode text.");
        }
        return value;
      }
    }
    return invalid("The archive contains an unfinished JSON string.");
  }
  function value(depth: number): { value: ArchiveValue; canonical: string } {
    if (++nodes > 4000000 || depth > 40)
      invalid("The archive is too complex to open safely.");
    space();
    const character = source[offset];
    if (character === '"') {
      const result = string();
      return { value: result, canonical: JSON.stringify(result) };
    }
    if (character === "{") {
      ++offset;
      space();
      const result: ArchiveRecord = Object.create(null);
      const canonical = new Map<string, string>();
      while (source[offset] !== "}") {
        if (source[offset] !== '"') invalid("The archive is not valid JSON.");
        const key = string();
        space();
        if (Object.hasOwn(result, key))
          invalid("The archive contains duplicate JSON keys.");
        if (source[offset++] !== ":") invalid("The archive is not valid JSON.");
        const child = value(depth + 1);
        result[key] = child.value;
        if (!(depth === 0 && key === "integrity"))
          canonical.set(key, child.canonical);
        space();
        if (source[offset] === "}") break;
        if (source[offset++] !== ",") invalid("The archive is not valid JSON.");
        space();
        if (source[offset] === "}") invalid("The archive is not valid JSON.");
      }
      ++offset;
      return {
        value: result,
        canonical:
          "{" +
          [...canonical.keys()]
            .sort(codePointOrder)
            .map((key) => JSON.stringify(key) + ":" + canonical.get(key))
            .join(",") +
          "}",
      };
    }
    if (character === "[") {
      ++offset;
      space();
      const result: ArchiveValue[] = [],
        canonical: string[] = [];
      while (source[offset] !== "]") {
        if (result.length >= 200000)
          invalid("An archive collection exceeds 200,000 records.");
        const child = value(depth + 1);
        result.push(child.value);
        canonical.push(child.canonical);
        space();
        if (source[offset] === "]") break;
        if (source[offset++] !== ",") invalid("The archive is not valid JSON.");
        space();
        if (source[offset] === "]") invalid("The archive is not valid JSON.");
      }
      ++offset;
      return { value: result, canonical: "[" + canonical.join(",") + "]" };
    }
    for (const [token, result] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ] as const) {
      if (source.startsWith(token, offset)) {
        offset += token.length;
        return { value: result, canonical: token };
      }
    }
    const token = source
      .slice(offset)
      .match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/)?.[0];
    if (!token) invalid("The archive is not valid JSON.");
    offset += token.length;
    const number = Number(token);
    if (
      !Number.isFinite(number) ||
      (!/[.eE]/.test(token) && !Number.isSafeInteger(number))
    )
      invalid("The archive contains an unsupported numeric value.");
    return { value: number, canonical: token };
  }
  try {
    const result = value(0);
    space();
    if (offset !== source.length) invalid("The archive is not valid JSON.");
    return result;
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof RangeError)
      invalid("The archive is not valid JSON.");
    throw error;
  }
}

export function utcArchiveTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString().slice(0, 19) === value.slice(0, 19)
  );
}
function validateRecordArray(data: ArchiveRecord, key: string) {
  const value = data[key];
  if (!Array.isArray(value) || value.some((row) => !isRecord(row)))
    invalid(`The archive has an invalid ${key} collection.`);
  return value as ArchiveRecord[];
}
function integer(
  value: ArchiveValue | undefined,
  label: string,
  nullable = false,
) {
  if (nullable && (value === null || value === undefined)) return;
  if (typeof value !== "number" || !Number.isSafeInteger(value))
    invalid(`The archive has an invalid ${label}.`);
}
function boundedInteger(
  value: ArchiveValue | undefined,
  label: string,
  minimum = 0,
) {
  integer(value, label);
  if ((value as number) < minimum || (value as number) > 2147483647)
    invalid(`The archive has an invalid ${label}.`);
}
function validateDocument(
  value: ArchiveValue,
): asserts value is ArchiveValue & ArchiveDocument {
  if (!isRecord(value) || value.format !== ARCHIVE_FORMAT)
    invalid("This file is not an Open Tech Quest event archive.");
  if (value.schema_version !== 1)
    invalid(
      "This archive version is not supported. Open it with a matching viewer.",
    );
  if (
    typeof value.archive_id !== "string" ||
    !/^[\w.:-]{1,128}$/.test(value.archive_id)
  )
    invalid("The archive identifier is invalid.");
  if (!utcArchiveTime(value.exported_at) || !isRecord(value.producer))
    invalid("The archive export information is invalid.");
  if (
    !isRecord(value.integrity) ||
    value.integrity.algorithm !== "sha256" ||
    typeof value.integrity.sha256 !== "string" ||
    !/^[a-f\d]{64}$/i.test(value.integrity.sha256)
  )
    invalid("The archive checksum information is invalid.");
  const data = value.data;
  if (
    !isRecord(data) ||
    !isRecord(data.event) ||
    typeof data.event.name !== "string" ||
    !["draft", "live", "paused", "closed", "published"].includes(
      String(data.event.status),
    )
  )
    invalid("The archive event information is invalid.");
  for (const key of [
    "participants",
    "teams",
    "rooms",
    "challenges",
    "hints",
    "starts",
    "pauses",
    "attempts",
    "hint_purchases",
    "corrections",
    "exclusions",
    "audit",
    "timeline",
  ])
    validateRecordArray(data, key);
  if (data.room_pauses !== undefined) {
    const ids = new Set<number>();
    for (const row of validateRecordArray(data, "room_pauses")) {
      integer(row.id, "room pause identifier");
      integer(row.room_id, "paused room identifier");
      if (ids.has(row.id as number))
        invalid("The archive contains duplicate room pause identifiers.");
      ids.add(row.id as number);
      if (
        !utcArchiveTime(row.started_at) ||
        (row.ended_at !== null && !utcArchiveTime(row.ended_at)) ||
        typeof row.reason !== "string" ||
        row.reason.length > 1000 ||
        (typeof row.ended_at === "string" &&
          timelineKey(row.ended_at) < timelineKey(row.started_at as string))
      )
        invalid("The archive room pause history is invalid.");
    }
  }
  if (data.price_changes !== undefined) {
    const ids = new Set<number>();
    for (const row of validateRecordArray(data, "price_changes")) {
      boundedInteger(row.id, "price change identifier", 1);
      boundedInteger(row.challenge_id, "price change challenge identifier", 1);
      boundedInteger(row.before_value, "previous price");
      boundedInteger(row.after_value, "new price");
      boundedInteger(row.existing_through_id, "price cohort boundary");
      if (ids.has(row.id as number))
        invalid("The archive contains duplicate price change identifiers.");
      ids.add(row.id as number);
      if (row.kind === "hint")
        boundedInteger(row.hint_id, "repriced hint identifier", 1);
      else if (row.kind !== "challenge" || row.hint_id !== null)
        invalid("The archive price change target is invalid.");
      if (
        typeof row.apply_existing !== "boolean" ||
        !utcArchiveTime(row.changed_at)
      )
        invalid("The archive price change history is invalid.");
    }
  }
  for (const row of validateRecordArray(data, "hint_purchases")) {
    if (row.effective_cost !== undefined) {
      boundedInteger(row.cost, "original hint purchase cost");
      boundedInteger(row.effective_cost, "effective hint deduction");
    }
  }
  if (data.scoring_credits !== undefined) {
    for (const row of validateRecordArray(data, "scoring_credits")) {
      if (row.earned_points !== undefined)
        boundedInteger(row.earned_points, "earned challenge points");
    }
  }
  for (const key of ["participants", "teams", "rooms", "challenges", "hints"]) {
    const seen = new Set<number>();
    for (const row of validateRecordArray(data, key)) {
      integer(row.id, `${key} identifier`);
      if (seen.has(row.id as number))
        invalid(`The archive contains duplicate ${key} identifiers.`);
      seen.add(row.id as number);
    }
  }
  for (const team of validateRecordArray(data, "teams")) {
    if (!Array.isArray(team.members))
      invalid("The archive team membership is invalid.");
    for (const id of team.members) integer(id, "team member identifier");
  }
  if (!isRecord(data.results)) invalid("The archive results are invalid.");
  if (
    data.results.scoring_version !== undefined &&
    data.results.scoring_version !== 1 &&
    data.results.scoring_version !== 2
  )
    invalid("This archive uses unsupported scoring rules.");
  validateRecordArray(data.results, "rooms");
  for (const row of validateRecordArray(data.results, "teams")) {
    if (typeof row.name !== "string")
      invalid("The archive result team name is invalid.");
    integer(row.id, "result team identifier");
    integer(row.rank, "result rank", true);
    integer(row.total, "solve count");
    if (archiveNumber(row.total) < 0)
      invalid("The archive solve count is invalid.");
    integer(row.overall_elapsed_ms, "ranking time", true);
    for (const field of ["points", "gross_points", "hint_costs"])
      integer(row[field], field, data.results.scoring_version !== 2);
    for (const progress of validateRecordArray(row, "room_progress")) {
      integer(progress.room_id, "result room identifier");
      integer(progress.solved, "room solve count");
      integer(progress.total, "room total");
      if (
        archiveNumber(progress.solved) < 0 ||
        archiveNumber(progress.total) < 0
      )
        invalid("The archive room counts are invalid.");
      if (data.results.scoring_version === 2)
        integer(progress.points, "room net points");
      integer(progress.rank, "room rank", true);
      integer(progress.milestone_elapsed_ms, "room ranking time", true);
    }
  }
  for (const row of validateRecordArray(data.results, "rooms")) {
    integer(row.id, "result room identifier");
    integer(row.total, "result room total");
    if (typeof row.name !== "string" || typeof row.slug !== "string")
      invalid("The archive result room information is invalid.");
  }
  for (const row of validateRecordArray(data, "timeline")) {
    if (
      !utcArchiveTime(row.at) ||
      typeof row.type !== "string" ||
      typeof row.id !== "string"
    )
      invalid("The archive timeline is invalid.");
    for (const key of ["team_id", "user_id", "room_id", "challenge_id"])
      integer(row[key], `timeline ${key}`, true);
  }
}

export async function parseEventArchive(
  source: string,
): Promise<ArchiveDocument> {
  if (source.length > MAX_ARCHIVE_BYTES)
    invalid("This archive exceeds the 64 MiB file limit.");
  const bytes = new TextEncoder().encode(source);
  if (bytes.byteLength > MAX_ARCHIVE_BYTES)
    invalid("This archive exceeds the 64 MiB file limit.");
  const parsed = parseArchiveJSON(source);
  validateDocument(parsed.value);
  if (!globalThis.crypto?.subtle)
    invalid("Archive verification requires an HTTPS or localhost page.");
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(parsed.canonical),
  );
  const actual = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== parsed.value.integrity.sha256.toLowerCase())
    invalid(
      "The archive checksum does not match. The file may be incomplete or changed.",
    );
  return parsed.value as unknown as ArchiveDocument;
}

export function timelineKey(value: string) {
  const [whole, fraction = ""] = value.slice(0, -1).split(".");
  return whole + "." + fraction.padEnd(6, "0");
}
export function filterArchiveTimeline(
  rows: ArchiveRecord[],
  filter: { team: string; room: string; type: string },
) {
  return rows.filter(
    (row) =>
      (!filter.team || String(row.team_id) === filter.team) &&
      (!filter.room || String(row.room_id) === filter.room) &&
      (!filter.type || row.type === filter.type),
  );
}
export function archivePage<T>(
  rows: readonly T[],
  requested: number,
  size = ARCHIVE_PAGE_SIZE,
) {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.max(0, Math.min(Math.floor(requested), pages - 1));
  return {
    page,
    pages,
    rows: rows.slice(page * size, (page + 1) * size),
    total: rows.length,
  };
}
