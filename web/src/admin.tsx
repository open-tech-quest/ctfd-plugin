import { useState, type FormEvent } from "react";
import { dateTime, request } from "./api";
import { useResource } from "./hooks";
import { ProjectorControls } from "./projector-controls";
import { DemoControls } from "./demo";
import { Attendance } from "./attendance";
import { ArchiveWorkspace } from "./event-archive";
import { RegistrationControls } from "./registration";
import { AttendanceWindowControls } from "./attendance-window";
import { RoomManagement } from "./room-management";
import { Users } from "./users";
import type { Viewer } from "./types";
import type {
  AdminState,
  AttendanceState,
  DemoContentState,
  QuestEvent,
  RegistrationState,
} from "./types";
import { Arrow, Confirm, Empty, Markdown, Notice } from "./ui";
const tabs = [
  "Event",
  "Rooms",
  "Teams",
  "Users",
  "Awards",
  "Projector",
  "Archives",
  "Danger zone",
  "Audit",
] as const;
function text(form: FormData, name: string) {
  return String(form.get(name) || "");
}
function localDate(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function utcDate(value: string) {
  return value ? new Date(value).toISOString() : null;
}
export function Admin({ viewer }: { viewer: Viewer }) {
  if (!viewer.admin)
    return (
      <>
        <div className="page-heading">
          <div>
            <p className="eyebrow">Challenge maintainer</p>
            <h1>Prepare the rooms</h1>
            <p className="lede">
              Configure room presentation, challenge paths and accepted answers.
            </p>
          </div>
        </div>
        <nav className="room-tabs" aria-label="Organizer sections">
          <span className="active maintainer-room-tab">Rooms</span>
        </nav>
        <RoomManagement viewer={viewer} />
      </>
    );
  return <FullAdmin viewer={viewer} />;
}
function FullAdmin({ viewer }: { viewer: Viewer }) {
  const resource = useResource<AdminState>("/admin/state", 20000),
    [tab, setTab] = useState<(typeof tabs)[number]>("Event"),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const data = resource.data;
  async function mutate(
    path: string,
    method: string,
    body: unknown,
    success: string,
  ) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await request(path, method, body);
      setMessage(success);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    } finally {
      setBusy(false);
    }
  }
  if (!data)
    return resource.error ? (
      <Notice tone="error" retry={() => void resource.refresh()}>
        {resource.error}
      </Notice>
    ) : (
      <p role="status">Loading organizer controls…</p>
    );
  const execute = (
    path: string,
    method: string,
    body: unknown,
    success: string,
  ) => void mutate(path, method, body, success).catch(() => {});
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Organizer workspace</p>
          <h1>Run the quest</h1>
          <p className="lede">
            Prepare the rooms, bring teams together and publish fair results.
          </p>
        </div>
        <a href="/admin" className="button secondary">
          Native CTFd admin <Arrow />
        </a>
      </div>
      <nav className="room-tabs" aria-label="Organizer sections">
        {tabs
          .filter((t) => t !== "Users" || viewer.can_manage_users)
          .map((t) => (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                setMessage("");
                setError("");
              }}
              className={tab === t ? "active" : ""}
            >
              {t}
            </button>
          ))}
      </nav>
      {resource.error && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          Showing saved configuration. {resource.error}
        </Notice>
      )}
      {message && <Notice tone="success">{message}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="admin-content" aria-busy={busy}>
        {tab === "Event" ? (
          <EventControls
            event={data.event}
            demo={data.demo}
            onboarding={data.onboarding}
            registration={data.registration}
            busy={busy}
            mutate={mutate}
            execute={execute}
          />
        ) : tab === "Rooms" ? (
          <RoomManagement viewer={viewer} />
        ) : tab === "Teams" ? (
          <Attendance
            data={data}
            busy={busy}
            mutate={mutate}
            onRefresh={resource.refresh}
          />
        ) : tab === "Users" ? (
          <Users onChanged={resource.refresh} />
        ) : tab === "Awards" ? (
          <AwardControls data={data} busy={busy} execute={execute} />
        ) : tab === "Projector" ? (
          <ProjectorControls data={data} busy={busy} mutate={mutate} />
        ) : tab === "Archives" ? (
          <ArchiveWorkspace
            key="archives"
            onChanged={resource.refresh}
            pane="archives"
          />
        ) : tab === "Danger zone" ? (
          <ArchiveWorkspace
            key="danger"
            onChanged={resource.refresh}
            pane="danger"
          />
        ) : (
          <AuditControls data={data} />
        )}
      </div>
    </>
  );
}
type Mutation = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => Promise<void>;
type Execute = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => void;
function EventControls({
  event,
  demo,
  onboarding,
  registration,
  busy,
  mutate,
  execute,
}: {
  event: QuestEvent;
  demo: DemoContentState;
  onboarding: AttendanceState;
  registration: RegistrationState;
  busy: boolean;
  mutate: Mutation;
  execute: Execute;
}) {
  const [reason, setReason] = useState("");
  const draft = event.status === "draft";
  const transitions: Record<
    string,
    { action: string; label: string; description: string }[]
  > = {
    draft: [
      {
        action: "start",
        label: "Start event",
        description:
          "Open all configured rooms and record the common event start. Prize policy and challenge order become fixed.",
      },
    ],
    live: [
      {
        action: "pause",
        label: "Pause event",
        description:
          "Pause challenge starts and submissions for everyone. This interval will be excluded from active times.",
      },
      {
        action: "close",
        label: "Close event",
        description:
          "Record the closing cutoff and stop new qualifying solves. Review the results before publishing.",
      },
    ],
    paused: [
      {
        action: "resume",
        label: "Resume event",
        description: "End this recorded pause and allow teams to continue.",
      },
      {
        action: "close",
        label: "Close event",
        description:
          "Record the closing cutoff. Results can be reviewed before publication.",
      },
    ],
    closed: [
      {
        action: "publish",
        label: "Publish final results",
        description:
          "Store the final award snapshot. Later database edits will not silently change these announced results.",
      },
      {
        action: "reopen",
        label: "Reopen event",
        description:
          "Clear the closing cutoff and return the event to paused. Resume explicitly when the teams are ready. Your reason is recorded.",
      },
    ],
    published: [
      {
        action: "unpublish",
        label: "Unpublish results",
        description:
          "Remove the published snapshot and return the event to closed for review. Your reason is recorded.",
      },
    ],
  };
  return (
    <>
      <section className="admin-panel lifecycle">
        <div className="section-heading">
          <h2>Event lifecycle</h2>
          <span className={`phase phase-${event.status}`}>{event.status}</span>
        </div>
        <ol className="phase-path">
          {["draft", "live", "paused", "closed", "published"].map((phase) => (
            <li className={phase === event.status ? "current" : ""} key={phase}>
              {phase}
            </li>
          ))}
        </ol>
        <div className="timing-facts">
          <span>
            Start <strong>{dateTime(event.start_at)}</strong>
          </span>
          <span>
            Cutoff <strong>{dateTime(event.end_at)}</strong>
          </span>
          <span>
            Roster release <strong>{dateTime(event.rosters_release_at)}</strong>
          </span>
        </div>
        {["closed", "published"].includes(event.status) && (
          <label className="field">
            <span>Reason for reopening or unpublishing</span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Explain the organizer correction"
            />
          </label>
        )}
        <div className="actions">
          {transitions[event.status]?.map((action) => (
            <Confirm
              key={action.action}
              label={action.label}
              description={action.description}
              disabled={
                busy ||
                (action.action === "start" &&
                  (demo.active || !onboarding?.can_start)) ||
                (["reopen", "unpublish"].includes(action.action) &&
                  !reason.trim())
              }
              danger={["close", "unpublish"].includes(action.action)}
              onConfirm={() =>
                mutate(
                  "/admin/event/action",
                  "POST",
                  {
                    action: action.action,
                    ...(["reopen", "unpublish"].includes(action.action)
                      ? { reason }
                      : {}),
                  },
                  `${action.label} completed`,
                )
              }
            />
          ))}
        </div>
        {draft && !onboarding?.can_start && (
          <p className="attendance-blocker">
            {onboarding?.start_reason ||
              "Attendance status is not available. Refresh organizer controls before starting."}
          </p>
        )}
      </section>
      <AttendanceWindowControls
        state={onboarding?.attendance}
        busy={busy}
        onChange={(enabled) =>
          mutate(
            "/admin/attendance",
            "PATCH",
            { enabled },
            enabled
              ? "Attendance check-in opened"
              : "Attendance check-in closed",
          )
        }
      />
      <RegistrationControls
        state={registration}
        busy={busy}
        onChange={(enabled) =>
          mutate(
            "/admin/registration",
            "PATCH",
            { enabled },
            enabled
              ? "Public registration opened"
              : "Public registration closed",
          )
        }
      />
      <DemoControls
        state={demo}
        busy={busy}
        onAction={(action) =>
          mutate(
            "/admin/demo",
            "POST",
            { action },
            action === "load"
              ? "Demo room content loaded"
              : "Demo content removed",
          )
        }
      />
      <section className="admin-panel">
        <h2>Event configuration</h2>
        <p className="section-copy">
          Prize clocks are fixed when the event starts. All date and time fields
          use your browser’s local time.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            execute(
              "/admin/event",
              "PATCH",
              {
                name: text(f, "name"),
                ...(draft
                  ? {
                      start_at: utcDate(text(f, "start_at")),
                      end_at: utcDate(text(f, "end_at")),
                      rosters_release_at: utcDate(
                        text(f, "rosters_release_at"),
                      ),
                      room_clock: text(f, "room_clock"),
                      overall_clock: text(f, "overall_clock"),
                    }
                  : {}),
              },
              "Event configuration saved",
            );
          }}
        >
          <label className="field">
            <span>Event name</span>
            <input
              name="name"
              defaultValue={event.name}
              required
              maxLength={128}
            />
          </label>
          <fieldset disabled={!draft || busy} className="form-grid">
            <label className="field">
              <span>Scheduled start</span>
              <input
                type="datetime-local"
                name="start_at"
                defaultValue={localDate(event.start_at)}
              />
            </label>
            <label className="field">
              <span>Scheduled end</span>
              <input
                type="datetime-local"
                name="end_at"
                defaultValue={localDate(event.end_at)}
              />
            </label>
            <label className="field">
              <span>Release team rosters</span>
              <input
                type="datetime-local"
                name="rosters_release_at"
                defaultValue={localDate(event.rosters_release_at)}
              />
              <small>Leave empty to release immediately</small>
            </label>
            <label className="field">
              <span>Room timing origin</span>
              <select name="room_clock" defaultValue={event.room_clock}>
                <option value="explicit">Team starts the room</option>
                <option value="event">Common event start</option>
              </select>
            </label>
            <label className="field">
              <span>Overall timing origin</span>
              <select name="overall_clock" defaultValue={event.overall_clock}>
                <option value="event">Common event start</option>
                <option value="team">Team starts its first room</option>
              </select>
            </label>
          </fieldset>
          {!draft && (
            <p className="meta">
              Timing policy and roster settings are locked after draft
            </p>
          )}
          <button className="button" disabled={busy}>
            Save configuration
          </button>
        </form>
      </section>
    </>
  );
}
function AwardControls({
  data,
  busy,
  execute,
}: {
  data: AdminState;
  busy: boolean;
  execute: Execute;
}) {
  return (
    <>
      <section className="admin-panel">
        <div className="section-heading">
          <h2>Review and export awards</h2>
          <a className="button secondary" href="/api/v1/quest/admin/export">
            Download standings CSV <Arrow />
          </a>
        </div>
        <p>
          Overall: distinct mapped solves, then active time to the last solve.
          Rooms: consecutive solved prefix, then milestone time. Ties share a
          rank. Teams with no qualifying solves do not win a prize.
        </p>
        <div className="admin-award-summary">
          <div>
            <span>Eligible teams</span>
            <strong>
              {data.standings.teams.filter((t) => t.total > 0).length}
            </strong>
          </div>
          <div>
            <span>Room timing</span>
            <strong>
              {data.event.room_clock === "explicit"
                ? "Room start"
                : "Event start"}
            </strong>
          </div>
          <div>
            <span>Overall timing</span>
            <strong>
              {data.event.overall_clock === "event"
                ? "Event start"
                : "First room"}
            </strong>
          </div>
          <div>
            <span>Results</span>
            <strong>
              {data.event.results_published
                ? "Published snapshot"
                : "Provisional"}
            </strong>
          </div>
        </div>
      </section>
      <section className="admin-panel">
        <h2>Record a timing correction</h2>
        <p>
          Original CTFd solve timestamps stay intact. Corrections require a
          reason and are recorded in the audit log. Enter a negative number to
          reduce active time.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            execute(
              "/admin/corrections",
              "POST",
              {
                team_id: Number(text(f, "team_id")),
                room_slug: text(f, "room_slug"),
                adjustment_ms: Number(text(f, "seconds")) * 1000,
                reason: text(f, "reason"),
              },
              "Timing correction recorded",
            );
          }}
        >
          <fieldset
            disabled={busy || data.event.results_published}
            className="form-grid"
          >
            <label className="field">
              <span>Team</span>
              <select name="team_id" required>
                <option value="">Choose a team</option>
                {data.teams.map((t) => (
                  <option value={t.id} key={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Room</span>
              <select name="room_slug" required>
                {data.rooms.map((r) => (
                  <option value={r.slug} key={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Adjustment in seconds</span>
              <input
                type="number"
                name="seconds"
                min={-86400}
                max={86400}
                step={1}
                required
              />
              <small>Maximum magnitude: 24 hours</small>
            </label>
            <label className="field">
              <span>Organizer reason</span>
              <textarea name="reason" required minLength={3} rows={2} />
            </label>
          </fieldset>
          <button
            className="button"
            disabled={busy || data.event.results_published}
          >
            Record correction
          </button>
          {data.event.results_published && (
            <p className="meta">
              Unpublish explicitly before recording corrections
            </p>
          )}
        </form>
      </section>
    </>
  );
}
function AuditControls({ data }: { data: AdminState }) {
  return (
    <section className="admin-panel">
      <h2>Organizer audit</h2>
      <p>
        Lifecycle transitions, configuration changes and corrections retain
        their actor and reason.
      </p>
      {!data.audit.length ? (
        <p className="meta">No organizer changes recorded yet</p>
      ) : (
        <div className="audit-list">
          {data.audit.map((entry) => (
            <article key={entry.id}>
              <div>
                <strong>{entry.action}</strong>
                <time>{dateTime(entry.created_at)}</time>
              </div>
              <span className="meta">Organizer {entry.actor_id}</span>
              <pre>
                {typeof entry.detail === "string"
                  ? entry.detail
                  : JSON.stringify(entry.detail, null, 2)}
              </pre>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
