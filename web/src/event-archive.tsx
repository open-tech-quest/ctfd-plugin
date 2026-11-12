import { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { dateTime, request } from "./api";
import { useResource } from "./hooks";
import { ConfirmDialog, Notice } from "./ui";
import { StandingTable } from "./standing-table";
import {
  orderedTeams,
  rankingDuration,
  rankingOrder,
} from "./standings-presentation";
import type { Standings } from "./types";
import {
  MAX_ARCHIVE_BYTES,
  archiveMarkdownHTML,
  archiveNumber,
  archivePage,
  archiveRecords,
  archiveText,
  filterArchiveTimeline,
  isRecord,
  parseEventArchive,
  timelineKey,
  type ArchiveDocument,
  type ArchiveRecord,
  type ArchiveValue,
} from "./archive-format";
import "./event-archive.css";
import {
  archiveHintPurchaseFields,
  archivePriceScope,
  archivePriceTarget,
} from "./archive-pricing";

type ArchiveSummary = {
  id: number;
  archive_id: string;
  name: string;
  event_status: string;
  created_at: string;
  sha256: string;
  counts: Record<string, number>;
};
type ResetMode = "new_round" | "defaults";
type RoundPreview = {
  allowed: boolean;
  reason: string | null;
  revision: string;
  event_name: string;
  counts: Record<string, number>;
  preserved: string[];
  removed: string[];
  archive_policy: string;
};

async function readServerArchive(id: number) {
  const response = await fetch(`/api/v1/quest/admin/archives/${id}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(60000),
  });
  if (!response.ok)
    throw new Error(
      "The archive could not be opened. Refresh the list and try again.",
    );
  const length = Number(response.headers.get("content-length"));
  if (length > MAX_ARCHIVE_BYTES)
    throw new Error("This archive exceeds the 64 MiB viewer limit.");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The server returned an empty archive.");
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const parts: string[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > MAX_ARCHIVE_BYTES)
        throw new Error("This archive exceeds the 64 MiB viewer limit.");
      parts.push(decoder.decode(chunk.value, { stream: true }));
    }
    parts.push(decoder.decode());
    return parts.join("");
  } finally {
    await reader.cancel().catch(() => {});
  }
}
function downloadArchive(source: string, document: ArchiveDocument) {
  const url = URL.createObjectURL(
    new Blob([source], { type: "application/json" }),
  );
  const link = window.document.createElement("a");
  link.href = url;
  link.download = `quest-archive-${document.archive_id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ArchiveWorkspace({
  onChanged,
  pane = "archives",
}: {
  onChanged: () => Promise<void>;
  pane?: "archives" | "danger";
}) {
  const list = useResource<{ archives: ArchiveSummary[] }>(
    pane === "archives" ? "/admin/archives" : null,
    30000,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [warning, setWarning] = useState(""),
    [preview, setPreview] = useState<RoundPreview | null>(null);
  const [source, setSource] = useState<string | undefined>(),
    [readerKey, setReaderKey] = useState(0);
  const [page, setPage] = useState(0);
  const [mode, setMode] = useState<ResetMode>("new_round");
  useEffect(() => {
    setPreview(null);
    setError("");
    setMessage("");
    setWarning("");
  }, [pane]);
  const archives = archivePage(list.data?.archives ?? [], page, 10);
  async function view(id: number, download = false) {
    setBusy(true);
    setError("");
    try {
      const text = await readServerArchive(id);
      if (download) downloadArchive(text, await parseEventArchive(text));
      else {
        setSource(text);
        setReaderKey((value) => value + 1);
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await request<{ id: number }>(
        "/admin/archives",
        "POST",
        {},
      );
      const text = await readServerArchive(result.id);
      const document = await parseEventArchive(text);
      downloadArchive(text, document);
      setMessage(
        "Archive saved on the server and downloaded. Keep the file private.",
      );
      await list.refresh();
    } catch (error) {
      setError((error as Error).message);
      await list.refresh();
    } finally {
      setBusy(false);
    }
  }
  async function prepare(selectedMode: ResetMode) {
    setBusy(true);
    setMode(selectedMode);
    setPreview(null);
    setError("");
    setWarning("");
    setMessage("");
    try {
      setPreview(
        await request<RoundPreview>(
          `/admin/round/preview?mode=${selectedMode}`,
        ),
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function refreshArchiveList() {
    if (pane === "archives") await list.refresh();
    else await request("/admin/archives");
  }
  return (
    <div className="archive-workspace">
      {pane === "archives" && (
        <section
          className="admin-panel archive-server-controls"
          aria-busy={busy}
        >
          <div className="section-heading">
            <div>
              <h2>Event archives</h2>
              <p>Keep an event record before moving on to the next round.</p>
            </div>
            <button
              className="button"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? "Working…" : "Save and download archive"}
            </button>
          </div>
          <p>
            Archives contain participant names, authored content and organizer
            notes. They exclude credentials, submitted answers and flag values.
            Store and share them as private event records.
          </p>
          {message && <Notice tone="success">{message}</Notice>}
          {warning && <Notice>{warning}</Notice>}
          {(error || list.error) && (
            <Notice tone="error" retry={() => void list.refresh()}>
              {error || list.error}
            </Notice>
          )}
          {!list.data ? (
            <p role="status">Loading saved archives…</p>
          ) : !archives.total ? (
            <p className="meta">No archives have been saved yet.</p>
          ) : (
            <>
              <div className="archive-table-scroll">
                <table className="archive-table">
                  <thead>
                    <tr>
                      <th>Event</th>
                      <th>Saved</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {archives.rows.map((item) => (
                      <tr key={item.id}>
                        <th scope="row">{item.name}</th>
                        <td>{dateTime(item.created_at)}</td>
                        <td>{item.event_status}</td>
                        <td className="archive-row-actions">
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => void view(item.id)}
                          >
                            View
                          </button>
                          <button
                            className="text-button"
                            disabled={busy}
                            onClick={() => void view(item.id, true)}
                          >
                            Download JSON
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager
                page={archives.page}
                pages={archives.pages}
                total={archives.total}
                onChange={setPage}
              />
            </>
          )}
        </section>
      )}
      {pane === "danger" && (
        <>
          <section className="admin-panel archive-danger-heading">
            <h2>Danger zone</h2>
            <p>
              Both actions save an immutable event archive first. Only the
              signed-in administrator performing the action is retained; every
              other account, including other administrators and maintainers, is
              removed.
            </p>
            {message && <Notice tone="success">{message}</Notice>}
            {warning && <Notice>{warning}</Notice>}
            {error && <Notice tone="error">{error}</Notice>}
          </section>
          <section className="admin-panel archive-next-round">
            <div>
              <h2>Start another round</h2>
              <p>
                Remove all other accounts, teams and competition history. Keep
                the rooms, challenge order, flags, hints and uploaded files for
                a new draft round.
              </p>
            </div>
            <button
              className="button danger"
              disabled={busy}
              onClick={() => void prepare("new_round")}
            >
              Review another round
            </button>
          </section>
          <section className="admin-panel archive-next-round">
            <div>
              <h2>Restore defaults</h2>
              <p>
                Also remove all challenges and custom rooms, then restore five
                empty default rooms. Only the administrator performing this
                action and saved archives are retained.
              </p>
            </div>
            <button
              className="button danger"
              disabled={busy}
              onClick={() => void prepare("defaults")}
            >
              Review restore defaults
            </button>
          </section>
        </>
      )}
      {pane === "danger" &&
        preview &&
        (preview.allowed ? (
          <NewRoundDialog
            key={`${mode}-${preview.revision}`}
            mode={mode}
            preview={preview}
            onClose={() => setPreview(null)}
            onConfirm={async (name, reason) => {
              try {
                const result = await request<{
                  archive_id: number;
                  cache_warning: string | null;
                }>("/admin/round/reset", "POST", {
                  revision: preview.revision,
                  mode,
                  confirm: true,
                  event_name: name,
                  reason,
                });
                setMessage(
                  mode === "defaults"
                    ? "The event was archived and defaults restored. The event is ready in draft."
                    : "The event was archived. The next round is ready in draft.",
                );
                setWarning(result.cache_warning ?? "");
                await Promise.all([refreshArchiveList(), onChanged()]);
              } catch (error) {
                // The transaction may have committed before its response was lost.
                // Reconcile all live state; never retry a destructive request here.
                const checked = await Promise.allSettled([
                  refreshArchiveList(),
                  onChanged(),
                  request<RoundPreview>(`/admin/round/preview?mode=${mode}`),
                ]);
                const fresh = checked[2];
                setPreview(fresh.status === "fulfilled" ? fresh.value : null);
                setError(
                  (error as Error).message +
                    " Review the current event state before trying another action.",
                );
                throw error;
              }
            }}
          />
        ) : (
          <Notice>
            {preview.reason || "This event is not ready for a new round."}
          </Notice>
        ))}
      {pane === "archives" && <ArchiveViewer key={readerKey} source={source} />}
    </div>
  );
}

function NewRoundDialog({
  mode,
  preview,
  onClose,
  onConfirm,
}: {
  mode: ResetMode;
  preview: RoundPreview;
  onClose: () => void;
  onConfirm: (name: string, reason: string) => Promise<void>;
}) {
  const [name, setName] = useState(""),
    [reason, setReason] = useState("");
  return (
    <ConfirmDialog
      label={mode === "defaults" ? "Restore defaults" : "Start another round"}
      description={
        mode === "defaults"
          ? "Archive this event, remove all other accounts, all challenges and custom rooms, and restore five empty default rooms. Only your signed-in administrator account is retained."
          : "Archive this event, remove all other accounts, teams and competition history, and keep the authored rooms and challenges. Only your signed-in administrator account is retained."
      }
      onClose={onClose}
      disabled={
        name !== preview.event_name ||
        reason.trim().length < 3 ||
        reason.trim().length > 1000
      }
      onConfirm={() => onConfirm(name, reason.trim())}
    >
      <div className="archive-reset-summary">
        <div>
          <h3>Kept</h3>
          <ul>
            {preview.preserved.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Cleared</h3>
          <ul>
            {preview.removed.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </div>
      <p>{preview.archive_policy}</p>
      <details>
        <summary>Round record counts</summary>
        <RecordFields record={preview.counts} />
      </details>
      <label className="field">
        <span>
          Type the current event name: <strong>{preview.event_name}</strong>
        </span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoComplete="off"
        />
      </label>
      <label className="field">
        <span>Reason for the new round</span>
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          minLength={3}
          maxLength={1000}
          rows={3}
        />
      </label>
      <p className="meta">
        Both actions return to draft without starting a competition clock. If
        the event changes while this review is open, close it and request a
        fresh review.
      </p>
    </ConfirmDialog>
  );
}

/** Local files are read in this browser. This component has no write APIs. */
export function ArchiveViewer({ source }: { source?: string } = {}) {
  const [document, setDocument] = useState<ArchiveDocument | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const generation = useRef(0);
  async function open(text: () => Promise<string>) {
    const sequence = ++generation.current;
    setDocument(null);
    setError("");
    setBusy(true);
    try {
      const parsed = await parseEventArchive(await text());
      if (generation.current === sequence) setDocument(parsed);
    } catch (error) {
      if (generation.current === sequence) setError((error as Error).message);
    } finally {
      if (generation.current === sequence) setBusy(false);
    }
  }
  useEffect(() => {
    if (source !== undefined) void open(() => Promise.resolve(source));
    return () => {
      ++generation.current;
    };
  }, [source]);
  return (
    <section className="archive-reader" aria-label="Read an event archive">
      <div className="archive-import">
        <div>
          <h2>Read an archive</h2>
          <p>
            Open a JSON event archive locally. The file stays in your browser
            and is never uploaded.
          </p>
        </div>
        <label className="archive-file-picker">
          <span>Open JSON file</span>
          <input
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              if (file.size > MAX_ARCHIVE_BYTES) {
                ++generation.current;
                setDocument(null);
                setBusy(false);
                setError("This archive exceeds the 64 MiB file limit.");
                return;
              }
              void open(() => file.text());
            }}
          />
        </label>
      </div>
      <p className="meta">
        Schema and SHA-256 checksum are checked before opening. A checksum
        detects changed content; it does not prove who created the file.
        External media and links are disabled.
      </p>
      {busy && <p role="status">Verifying archive…</p>}
      {error && <Notice tone="error">{error}</Notice>}
      {document && (
        <ArchiveContents key={document.archive_id} document={document} />
      )}
    </section>
  );
}

function Pager({
  page,
  pages,
  total,
  onChange,
}: {
  page: number;
  pages: number;
  total: number;
  onChange: (page: number) => void;
}) {
  return (
    <nav className="archive-pager" aria-label="Archive record pages">
      <span>
        {total.toLocaleString()} records · Page {page + 1} of {pages}
      </span>
      <button
        className="button secondary"
        disabled={page === 0}
        onClick={() => onChange(page - 1)}
      >
        Previous
      </button>
      <button
        className="button secondary"
        disabled={page >= pages - 1}
        onClick={() => onChange(page + 1)}
      >
        Next
      </button>
    </nav>
  );
}
function RecordFields({
  record,
}: {
  record: ArchiveRecord | Record<string, number>;
}) {
  return (
    <dl className="archive-fields">
      {Object.entries(record).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>
            {isRecord(value) || Array.isArray(value) ? (
              <pre>{JSON.stringify(value, null, 2)}</pre>
            ) : (
              String(value ?? "—")
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
function Evidence({
  rows,
  title,
  present,
}: {
  rows: ArchiveRecord[];
  title: string;
  present?: (row: ArchiveRecord) => ArchiveRecord;
}) {
  const [open, setOpen] = useState(false),
    [page, setPage] = useState(0);
  const current = archivePage(rows, page, 10);
  return (
    <details
      className="archive-evidence"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {title} ({rows.length.toLocaleString()})
      </summary>
      {open && (
        <>
          {current.rows.length ? (
            current.rows.map((row, index) => (
              <RecordFields key={index} record={present ? present(row) : row} />
            ))
          ) : (
            <p>No records.</p>
          )}
          <Pager
            page={current.page}
            pages={current.pages}
            total={current.total}
            onChange={setPage}
          />
        </>
      )}
    </details>
  );
}
function PriceChangeEvidence({
  rows,
  challenges,
  hints,
}: {
  rows: ArchiveRecord[];
  challenges: Map<ArchiveValue | undefined, ArchiveRecord>;
  hints: Map<ArchiveValue | undefined, ArchiveRecord>;
}) {
  const [open, setOpen] = useState(false),
    [page, setPage] = useState(0);
  const current = archivePage(rows, page, 10);
  return (
    <details
      className="archive-evidence"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        Point and hint price history ({rows.length.toLocaleString()})
      </summary>
      {open && (
        <>
          <p className="meta">
            Recorded price changes and their scope. Earlier cohorts and original
            hint receipts are retained; results above stay as recorded by the
            server.
          </p>
          {current.rows.length ? (
            <div
              className="archive-table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Point and hint price history"
            >
              <table className="archive-table">
                <thead>
                  <tr>
                    <th>Changed (UTC)</th>
                    <th>Price</th>
                    <th>Before → After</th>
                    <th>Applies to</th>
                  </tr>
                </thead>
                <tbody>
                  {current.rows.map((row) => (
                    <tr key={archiveNumber(row.id)}>
                      <td>
                        <time dateTime={archiveText(row.changed_at)}>
                          {archiveText(row.changed_at)}
                        </time>
                      </td>
                      <th>
                        {archivePriceTarget(
                          row,
                          challenges.get(row.challenge_id),
                          hints.get(row.hint_id),
                        )}
                      </th>
                      <td>
                        {archiveNumber(row.before_value).toLocaleString()} →{" "}
                        {archiveNumber(row.after_value).toLocaleString()} points
                      </td>
                      <td>{archivePriceScope(row)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p>No price changes.</p>
          )}
          <Pager
            page={current.page}
            pages={current.pages}
            total={current.total}
            onChange={setPage}
          />
        </>
      )}
    </details>
  );
}
function ArchiveMarkdown({ source }: { source: string }) {
  const html = useMemo(
    () =>
      DOMPurify.sanitize(archiveMarkdownHTML(source.slice(0, 65536)), {
        FORBID_TAGS: [
          "a",
          "img",
          "picture",
          "video",
          "audio",
          "source",
          "iframe",
          "object",
          "embed",
          "form",
          "input",
          "button",
          "style",
          "link",
          "meta",
          "svg",
          "math",
        ],
        FORBID_ATTR: ["style", "src", "srcset", "href", "poster", "background"],
      }),
    [source],
  );
  return (
    <>
      <div
        className="prose archive-prose"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {source.length > 65536 && (
        <details>
          <summary>Read the complete text without formatting</summary>
          <pre>{source}</pre>
        </details>
      )}
    </>
  );
}
function ArchiveContents({ document }: { document: ArchiveDocument }) {
  const { data } = document;
  const [tab, setTab] = useState("Results"),
    [team, setTeam] = useState(""),
    [room, setRoom] = useState(""),
    [type, setType] = useState("");
  const [page, setPage] = useState(0),
    [resultRoom, setResultRoom] = useState("");
  const [selectedTeam, setSelectedTeam] = useState(
    String(data.teams[0]?.id ?? ""),
  );
  const [selectedChallenge, setSelectedChallenge] = useState(
    String(data.challenges[0]?.id ?? ""),
  );
  const teams = useMemo(
    () => new Map(data.teams.map((row) => [row.id, row])),
    [data],
  );
  const rooms = useMemo(
    () => new Map(data.rooms.map((row) => [row.id, row])),
    [data],
  );
  const challenges = useMemo(
    () => new Map(data.challenges.map((row) => [row.id, row])),
    [data],
  );
  const people = useMemo(
    () => new Map(data.participants.map((row) => [row.id, row])),
    [data],
  );
  const hints = useMemo(
    () => new Map(data.hints.map((row) => [row.id, row])),
    [data],
  );
  const timeline = useMemo(
    () =>
      [...data.timeline].sort((a, b) =>
        timelineKey(archiveText(b.at)).localeCompare(
          timelineKey(archiveText(a.at)),
        ),
      ),
    [data],
  );
  const timelineTypes = useMemo(
    () =>
      [...new Set(data.timeline.map((row) => archiveText(row.type)))]
        .sort()
        .map((value) => ({ id: value, name: value })),
    [data],
  );
  const filtered = useMemo(
    () => filterArchiveTimeline(timeline, { team, room, type }),
    [timeline, team, room, type],
  );
  const records = archivePage(filtered, page);
  const results = data.results as unknown as Standings;
  const comparison = results.rooms.find(
    (item) => String(item.id) === resultRoom,
  );
  const rankings = archivePage(orderedTeams(results.teams, comparison), page);
  function changeTab(value: string) {
    setTab(value);
    setPage(0);
  }
  function filter(setter: (value: string) => void, value: string) {
    setter(value);
    setPage(0);
  }
  function label(
    map: Map<ArchiveValue | undefined, ArchiveRecord>,
    id: ArchiveValue | undefined,
    kind: string,
  ) {
    return archiveText(map.get(id)?.name, id == null ? "—" : `${kind} ${id}`);
  }
  return (
    <div className="archive-document">
      <header className="archive-event-heading">
        <h2>{archiveText(data.event.name)}</h2>
        <p>
          {archiveText(data.event.status)} event · Exported{" "}
          {dateTime(document.exported_at)}
        </p>
        <dl className="archive-counts">
          {[
            ["Teams", data.teams.length],
            ["Participants", data.participants.length],
            ["Challenges", data.challenges.length],
            ["Attempts", archiveRecords(data.attempts).length],
          ].map(([label, count]) => (
            <div key={String(label)}>
              <dt>{label}</dt>
              <dd>{Number(count).toLocaleString()}</dd>
            </div>
          ))}
        </dl>
        <details>
          <summary>Verified archive information</summary>
          <RecordFields
            record={{
              archive_id: document.archive_id,
              exported_at: document.exported_at,
              checksum: document.integrity.sha256,
              producer: document.producer,
            }}
          />
        </details>
      </header>
      <nav className="room-tabs archive-tabs" aria-label="Archive sections">
        {["Results", "Timeline", "Teams", "Challenges", "Event records"].map(
          (value) => (
            <button
              key={value}
              aria-pressed={tab === value}
              className={tab === value ? "active" : ""}
              onClick={() => changeTab(value)}
            >
              {value}
            </button>
          ),
        )}
      </nav>
      {tab === "Results" ? (
        <>
          <Choice
            label="Results category"
            value={resultRoom}
            onChange={(value) => filter(setResultRoom, value)}
            rows={results.rooms as unknown as ArchiveRecord[]}
            all="Overall"
          />
          <p>
            {rankingOrder(results, !!comparison)} Ranks, points and times are
            the recorded server results; they are not recalculated from this
            file.
          </p>
          <div className="archive-table-scroll">
            <StandingTable
              data={results}
              teams={rankings.rows}
              room={comparison}
              overall
            />
          </div>
          <Pager
            page={rankings.page}
            pages={rankings.pages}
            total={rankings.total}
            onChange={setPage}
          />
        </>
      ) : tab === "Timeline" ? (
        <>
          <div className="archive-filters">
            <Choice
              label="Team"
              value={team}
              onChange={(value) => filter(setTeam, value)}
              rows={data.teams}
              all="All teams"
            />
            <Choice
              label="Room"
              value={room}
              onChange={(value) => filter(setRoom, value)}
              rows={data.rooms}
              all="All rooms"
            />
            <Choice
              label="Activity"
              value={type}
              onChange={(value) => filter(setType, value)}
              rows={timelineTypes}
              all="All activity"
            />
          </div>
          <p className="meta">
            Newest first. Recorded UTC times retain microsecond precision.
          </p>
          <ol className="archive-timeline">
            {records.rows.map((row, index) => (
              <li key={`${row.id}-${index}`}>
                <time dateTime={archiveText(row.at)}>
                  {archiveText(row.at).slice(0, 10)}
                  <span>{archiveText(row.at).slice(11, -1)} UTC</span>
                  <small>{archiveText(row.type)}</small>
                </time>
                <div>
                  <h3>{archiveText(row.summary, archiveText(row.type))}</h3>
                  <p>
                    {[
                      row.team_id != null
                        ? label(teams, row.team_id, "Team")
                        : "",
                      row.room_id != null
                        ? label(rooms, row.room_id, "Room")
                        : "",
                      row.challenge_id != null
                        ? label(challenges, row.challenge_id, "Challenge")
                        : "",
                      row.user_id != null
                        ? label(people, row.user_id, "Participant")
                        : "",
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Event activity"}
                  </p>
                  <Evidence rows={[row]} title="Recorded details" />
                </div>
              </li>
            ))}
          </ol>
          {!records.total && <p>No activity matches these filters.</p>}
          <Pager
            page={records.page}
            pages={records.pages}
            total={records.total}
            onChange={setPage}
          />
        </>
      ) : tab === "Teams" ? (
        <>
          <Choice
            label="Team record"
            value={selectedTeam}
            onChange={setSelectedTeam}
            rows={data.teams}
          />
          {data.teams.find((row) => String(row.id) === selectedTeam) ? (
            (() => {
              const row = data.teams.find(
                (row) => String(row.id) === selectedTeam,
              )!;
              const result = results.teams.find(
                (item) => String(item.id) === selectedTeam,
              );
              return (
                <article className="archive-detail">
                  <h3>{archiveText(row.name)}</h3>
                  <p>
                    {result
                      ? `${result.total} solved${results.scoring_version === 2 ? ` · ${result.points} net points` : ""} · ${rankingDuration(result.overall_elapsed_ms)} active time`
                      : "No recorded prize result for this team."}
                  </p>
                  <h4>Members at export</h4>
                  <MemberList
                    ids={Array.isArray(row.members) ? row.members : []}
                    people={people}
                  />
                  <RecordFields record={row} />
                  <Evidence
                    rows={archiveRecords(data.starts).filter(
                      (item) => String(item.team_id) === selectedTeam,
                    )}
                    title="Room starts"
                  />
                  <Evidence
                    rows={archiveRecords(data.hint_purchases).filter(
                      (item) => String(item.team_id) === selectedTeam,
                    )}
                    title="Hint purchases"
                    present={archiveHintPurchaseFields}
                  />
                  <Evidence
                    rows={archiveRecords(data.corrections).filter(
                      (item) => String(item.team_id) === selectedTeam,
                    )}
                    title="Timing corrections"
                  />
                  <Evidence
                    rows={archiveRecords(data.attempts).filter(
                      (item) => String(item.team_id) === selectedTeam,
                    )}
                    title="Native attempts and recovery (live credit at export)"
                  />
                </article>
              );
            })()
          ) : (
            <p>No team records.</p>
          )}
        </>
      ) : tab === "Challenges" ? (
        <>
          <Choice
            label="Challenge record"
            value={selectedChallenge}
            onChange={setSelectedChallenge}
            rows={data.challenges}
          />
          {data.challenges.find(
            (row) => String(row.id) === selectedChallenge,
          ) ? (
            (() => {
              const row = data.challenges.find(
                (row) => String(row.id) === selectedChallenge,
              )!;
              return (
                <article className="archive-detail">
                  <h3>{archiveText(row.name)}</h3>
                  <p>
                    {label(rooms, row.room_id, "Room")} ·{" "}
                    {archiveNumber(row.value)} points · {archiveText(row.state)}
                  </p>
                  <ArchiveMarkdown source={archiveText(row.description, "")} />
                  <Evidence
                    rows={data.hints.filter(
                      (hint) => String(hint.challenge_id) === selectedChallenge,
                    )}
                    title="Authored hints and costs"
                  />
                  {data.price_changes !== undefined && (
                    <PriceChangeEvidence
                      key={selectedChallenge}
                      rows={archiveRecords(data.price_changes).filter(
                        (item) =>
                          String(item.challenge_id) === selectedChallenge,
                      )}
                      challenges={challenges}
                      hints={hints}
                    />
                  )}
                  <Evidence
                    rows={archiveRecords(data.attachments).filter(
                      (item) => String(item.challenge_id) === selectedChallenge,
                    )}
                    title="Attachment metadata (files not embedded)"
                  />
                  <Evidence
                    rows={archiveRecords(data.attempts).filter(
                      (item) => String(item.challenge_id) === selectedChallenge,
                    )}
                    title="Native attempts"
                  />
                  <details>
                    <summary>Authoring metadata</summary>
                    <RecordFields record={row} />
                  </details>
                </article>
              );
            })()
          ) : (
            <p>No challenge records.</p>
          )}
        </>
      ) : (
        <>
          <h3>Event settings and retained records</h3>
          <RecordFields record={data.event} />
          {Object.entries(data)
            .filter(
              ([key, value]) =>
                Array.isArray(value) &&
                !["teams", "challenges", "timeline"].includes(key),
            )
            .map(([key, value]) =>
              key === "price_changes" ? (
                <PriceChangeEvidence
                  key={key}
                  rows={archiveRecords(value)}
                  challenges={challenges}
                  hints={hints}
                />
              ) : (
                <Evidence
                  key={key}
                  title={key.replaceAll("_", " ")}
                  rows={archiveRecords(value)}
                  present={
                    key === "hint_purchases"
                      ? archiveHintPurchaseFields
                      : undefined
                  }
                />
              ),
            )}
          <p className="meta">
            Results retain the server's published snapshot where one existed,
            including legacy scoring rules. Attempt credit and scoring-credit
            records describe the live projection at export; they do not replace
            those saved results. This event report cannot restore credentials,
            flags or attachment files.
          </p>
        </>
      )}
    </div>
  );
}
function Choice({
  label,
  value,
  onChange,
  rows,
  all,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows: ArchiveRecord[];
  all?: string;
}) {
  const [search, setSearch] = useState("");
  const matches = useMemo(
    () =>
      rows.filter(
        (row) =>
          !search ||
          `${archiveText(row.name)} ${row.id}`
            .toLocaleLowerCase()
            .includes(search.toLocaleLowerCase()),
      ),
    [rows, search],
  );
  const visible = matches.slice(0, 200);
  const selected = rows.find((row) => String(row.id) === value);
  if (selected && !visible.includes(selected)) visible.unshift(selected);
  return (
    <div className="archive-select">
      {rows.length > 200 && (
        <label className="field">
          <span>Find {label.toLocaleLowerCase()}</span>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter names or IDs"
          />
        </label>
      )}
      <label className="field">
        <span>{label}</span>
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          {all && <option value="">{all}</option>}
          {visible.map((row) => (
            <option key={String(row.id)} value={String(row.id)}>
              {archiveText(row.name, `Record ${row.id}`)}
            </option>
          ))}
        </select>
      </label>
      {matches.length > 200 && (
        <small className="meta">
          First 200 matches shown. Refine the search to find another record.
        </small>
      )}
    </div>
  );
}
function MemberList({
  ids,
  people,
}: {
  ids: ArchiveValue[];
  people: Map<ArchiveValue | undefined, ArchiveRecord>;
}) {
  const [page, setPage] = useState(0);
  const current = archivePage(ids, page);
  return (
    <>
      <ul>
        {current.rows.map((id) => (
          <li key={String(id)}>
            {archiveText(people.get(id)?.name, `Participant ${id}`)}
          </li>
        ))}
      </ul>
      {current.pages > 1 && (
        <Pager
          page={current.page}
          pages={current.pages}
          total={current.total}
          onChange={setPage}
        />
      )}
    </>
  );
}
