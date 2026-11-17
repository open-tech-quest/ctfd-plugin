import { useEffect, useState } from "react";
import { dateTime, request } from "./api";
import type {
  AdminState,
  AttendancePlan,
  AttendanceState,
  Participant,
  Team,
} from "./types";
import { Confirm, Empty, Notice } from "./ui";
import { AttendanceWindowControls } from "./attendance-window";
import { LateAdmission } from "./late-admission";
import { TeamNamingPolicy } from "./team-naming-policy";
import { ParticipantMove } from "./participant-move";
import { WaitingParticipants } from "./waiting-participants";

type Mutation = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => Promise<void>;
type Props = { data: AdminState; busy: boolean; mutate: Mutation };
const phaseNames = {
  check_in: "Participant pool",
  closed: "Attendance review",
  naming: "Final team review",
  finalized: "Rosters finalized",
};
function eligible(person: Participant) {
  return !person.banned;
}
function teamPeople(data: AdminState, team: Team) {
  return data.participants.filter((person) =>
    team.user_ids.includes(person.id),
  );
}

export function Attendance({
  data,
  busy,
  mutate,
  onRefresh,
}: Props & { onRefresh: () => Promise<void> }) {
  const [query, setQuery] = useState(""),
    [view, setView] = useState<"teams" | "people">(
      data.onboarding?.phase === "check_in" ? "people" : "teams",
    ),
    [filter, setFilter] = useState("all");
  const state = data.onboarding;
  useEffect(() => {
    if (state?.phase === "naming" || state?.phase === "finalized")
      setView("teams");
  }, [state?.phase]);
  useEffect(() => {
    if (
      state?.require_attendance === false &&
      ["ready", "pending", "late"].includes(filter)
    )
      setFilter("all");
  }, [state?.require_attendance, filter]);
  if (!state)
    return (
      <Notice>
        Attendance controls are not available from the server yet. Refresh after
        the update completes.
      </Notice>
    );
  const attendanceRequired = state.require_attendance !== false;
  const teamCount = (team: Team) =>
    attendanceRequired ? team.ready_count : team.user_ids.length;
  const minimum = Math.ceil(state.team_size * 0.8);
  const undersized = data.teams.filter(
    (team) => teamCount(team) > 0 && teamCount(team) < minimum,
  ).length;
  const matches = (value: string) =>
    value.toLocaleLowerCase().includes(query.toLocaleLowerCase());
  const teams = data.teams.filter((team) => {
    const people = teamPeople(data, team);
    return (
      (matches(team.name) ||
        people.some(
          (person) => matches(person.name) || matches(person.email),
        )) &&
      (filter === "all" ||
        (filter === "undersized" &&
          teamCount(team) > 0 &&
          teamCount(team) < minimum) ||
        (filter === "pending" &&
          people.some((person) => !person.ready_at && eligible(person))) ||
        (filter === "ready" &&
          team.ready_count > 0 &&
          team.ready_count === people.filter(eligible).length))
    );
  });
  const people = data.participants.filter(
    (person) =>
      (matches(person.name) ||
        matches(person.email) ||
        matches(
          data.teams.find((team) => team.id === person.team_id)?.name || "",
        )) &&
      (filter === "all" ||
        (filter === "ready" && !!person.ready_at) ||
        (filter === "pending" && !person.ready_at && eligible(person)) ||
        (filter === "unassigned" &&
          !person.team_id &&
          eligible(person) &&
          (!attendanceRequired || !!person.ready_at)) ||
        (filter === "late" && person.late)),
  );
  return (
    <>
      {attendanceRequired && (
        <AttendanceWindowControls
          state={state.attendance}
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
      )}
      <AttendanceLifecycle data={data} busy={busy} mutate={mutate} />
      {data.event.status === "draft" && (
        <ParticipantMove
          data={data}
          busy={busy}
          mutate={mutate}
          onRefresh={onRefresh}
        />
      )}
      <div
        className="attendance-counts"
        role="group"
        aria-label="Attendance totals"
      >
        <div>
          <strong>
            {attendanceRequired ? state.ready_count : state.eligible_count}
          </strong>
          <span>
            {attendanceRequired ? "Checked in" : "Eligible participants"}
          </span>
        </div>
        {attendanceRequired && (
          <div>
            <strong>{state.pending_count}</strong>
            <span>Pending check-in</span>
          </div>
        )}
        <div>
          <strong>
            {attendanceRequired
              ? state.unassigned_ready_count
              : state.unassigned_count}
          </strong>
          <span>
            {attendanceRequired
              ? "Ready, unassigned"
              : "Unassigned participants"}
          </span>
        </div>
        <div>
          <strong>{undersized}</strong>
          <span>Below 80% target</span>
        </div>
        <div>
          <strong>
            {data.teams.filter((team) => team.user_ids.length > 0).length}
          </strong>
          <span>Populated teams</span>
        </div>
      </div>
      {data.event.status === "draft" &&
        (state.phase === "closed" ||
          (!attendanceRequired && state.phase === "check_in")) && (
          <TeamFormation
            key={`${state.attendance?.generation}-${state.attendance?.enabled}-${state.team_size}-${state.allow_team_naming}-${attendanceRequired}-${data.registration.enabled}`}
            data={data}
            busy={busy}
            mutate={mutate}
          />
        )}
      {data.event.status === "draft" && state.phase === "naming" && (
        <LateQueue data={data} busy={busy} mutate={mutate} />
      )}
      {state.phase === "finalized" && (
        <WaitingParticipants data={data} busy={busy} mutate={mutate} />
      )}
      {state.phase === "finalized" && (
        <LateAdmission
          data={data}
          busy={busy}
          mutate={mutate}
          onRefresh={onRefresh}
        />
      )}
      <div className="section-heading">
        <div>
          <h2>Teams and participants</h2>
          <p>
            {data.event.status === "draft"
              ? "Review teams and move participants as needed. Organizers can adjust membership throughout Draft, including after finalization. Team renaming is optional."
              : "Existing memberships and names are fixed after the event starts. Eligible newcomers can use controlled late admission."}
          </p>
        </div>
      </div>
      <div className="attendance-toolbar">
        <div className="attendance-views" role="group" aria-label="Roster view">
          <button
            className={view === "teams" ? "active" : ""}
            aria-pressed={view === "teams"}
            onClick={() => {
              setView("teams");
              setFilter("all");
            }}
          >
            Teams <span>{data.teams.length}</span>
          </button>
          <button
            className={view === "people" ? "active" : ""}
            aria-pressed={view === "people"}
            onClick={() => {
              setView("people");
              setFilter("all");
            }}
          >
            Participants <span>{data.participants.length}</span>
          </button>
        </div>
        <label className="attendance-search">
          <span className="sr-only">Search teams or participants</span>
          <input
            type="search"
            placeholder="Search teams, names or email"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label>
          <span className="sr-only">Attendance filter</span>
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">All {view}</option>
            {attendanceRequired && (
              <>
                <option value="ready">Checked in</option>
                <option value="pending">Pending check-in</option>
              </>
            )}
            {view === "teams" ? (
              <>
                <option value="undersized">Below 80% target</option>
              </>
            ) : (
              <>
                <option value="unassigned">
                  {attendanceRequired ? "Ready, unassigned" : "Unassigned"}
                </option>
                {attendanceRequired && (
                  <option value="late">Late arrivals</option>
                )}
              </>
            )}
          </select>
        </label>
      </div>
      {view === "teams" ? (
        teams.length ? (
          <div className="attendance-team-list">
            {teams.map((team) => (
              <RosterTeam
                key={team.id}
                team={team}
                data={data}
                busy={busy}
                mutate={mutate}
              />
            ))}
          </div>
        ) : (
          <Empty
            title={
              data.teams.length
                ? "No matching teams"
                : "Teams have not been formed yet"
            }
          >
            {data.teams.length
              ? "Try another search or attendance filter."
              : attendanceRequired
                ? "After check-in, move to attendance review and preview the checked-in teams."
                : "Close registration, then preview and apply random balanced teams from registered participants."}
          </Empty>
        )
      ) : (
        <ParticipantList people={people} data={data} />
      )}
    </>
  );
}

function AttendanceLifecycle({ data, busy, mutate }: Props) {
  const state = data.onboarding;
  const [size, setSize] = useState(state.team_size);
  useEffect(() => setSize(state.team_size), [state.team_size]);
  const draft = data.event.status === "draft";
  const attendanceRequired = state.require_attendance !== false;
  const settingsEditable =
    draft && ["check_in", "closed"].includes(state.phase);
  const phases = attendanceRequired
    ? phaseNames
    : {
        check_in: "Participant pool",
        closed: "Team review",
        naming: "Final team review",
        finalized: "Rosters finalized",
      };
  return (
    <section className="admin-panel attendance-lifecycle">
      <div className="section-heading">
        <h2>Prepare teams</h2>
        <span className="phase">{phases[state.phase]}</span>
      </div>
      <ol className="phase-path">
        {Object.entries(phases).map(([phase, name]) => (
          <li key={phase} className={phase === state.phase ? "current" : ""}>
            {name}
          </li>
        ))}
      </ol>
      {state.phase !== "finalized" && (
        <div className="event-day-guidance">
          <h3>Preparation sequence</h3>
          <p>
            {attendanceRequired
              ? "Collect registrations, open attendance check-in, then move to attendance review. Preview and apply random balanced teams from checked-in participants."
              : "Close public registration, then preview and apply random balanced teams from all eligible registered participants. No attendance check-in is needed. No-shows are included; use Move participant to adjust teams or return them to unassigned."}
          </p>
          <p>
            Review the groups, finalize rosters, then start the event from the
            Event tab. Team renaming is optional and remains available during
            Draft, Live and Paused play.
          </p>
        </div>
      )}
      <div className="team-naming-policy">
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={attendanceRequired}
            disabled={busy || !settingsEditable || state.attendance.enabled}
            onChange={(event) =>
              void mutate(
                "/admin/onboarding",
                "PATCH",
                { require_attendance: event.currentTarget.checked },
                "Team formation policy saved",
              ).catch(() => {})
            }
          />
          Require attendance before forming teams
        </label>
        <p className="meta">
          {attendanceRequired
            ? "Only checked-in eligible participants enter random team formation."
            : "All eligible registered participants enter formation, including no-shows. Registration must be closed before previewing and applying teams."}
        </p>
        <p className="meta">
          {state.attendance.enabled
            ? "Close attendance capture before changing this policy."
            : settingsEditable
              ? "Choose the policy before applying teams. Changing it invalidates older previews."
              : "The formation policy is fixed after teams have been applied."}
        </p>
      </div>
      <div className="attendance-settings">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void mutate(
              "/admin/onboarding",
              "PATCH",
              { team_size: size },
              "Team capacity saved",
            ).catch(() => {});
          }}
        >
          <label className="field">
            <span>Maximum people per team</span>
            <input
              type="number"
              min={1}
              max={50}
              value={size}
              disabled={!settingsEditable || busy}
              onChange={(event) => setSize(Number(event.target.value))}
            />
          </label>
          {settingsEditable && (
            <button
              className="button secondary"
              disabled={
                busy ||
                size === state.team_size ||
                !Number.isInteger(size) ||
                size < 1 ||
                size > 50
              }
            >
              Save capacity
            </button>
          )}
        </form>
        <p>
          Maximum size is a hard limit. The 80% target is a guideline; the
          preview explains smaller groups. A move preserves the participant's
          recorded attendance.
        </p>
      </div>
      <TeamNamingPolicy
        state={state}
        event={data.event}
        busy={busy}
        onChange={(enabled) =>
          mutate(
            "/admin/onboarding",
            "PATCH",
            { allow_team_naming: enabled },
            enabled
              ? "Participant team renaming enabled"
              : "Participant renaming disabled; current names retained",
          )
        }
      />
      <div className="actions">
        {draft && attendanceRequired && state.phase === "check_in" && (
          <Confirm
            key={`review-${state.attendance.generation}-${state.attendance.enabled}`}
            label="Move to attendance review"
            description="Close attendance capture and review the checked-in participant pool. Next, preview and apply the random balanced groups. Existing readiness stays saved and no competition clock starts."
            disabled={busy}
            onConfirm={() =>
              mutate(
                "/admin/onboarding/action",
                "POST",
                { action: "close_checkin" },
                "Checked-in participant pool moved to attendance review",
              )
            }
          />
        )}
        {draft && state.phase === "closed" && (
          <Confirm
            label="Return to participant pool"
            description="Return to the participant pool before reviewed teams have been applied. Recorded attendance is retained; capture stays unchanged."
            disabled={busy}
            onConfirm={() =>
              mutate(
                "/admin/onboarding/action",
                "POST",
                { action: "reopen_checkin" },
                "Returned to participant pool",
              )
            }
          />
        )}
        {draft && state.phase === "naming" && (
          <Confirm
            key={`finalize-${state.fingerprint}`}
            label="Finalize rosters"
            description="Confirm the reviewed rosters are ready for launch. Organizers can still move participants throughout Draft. Enabled teammates can rename during Draft, Live and Paused play. Generated Team N names are valid. Start the event separately."
            disabled={busy || !!state.finalization_reason}
            onConfirm={() =>
              mutate(
                "/admin/onboarding/action",
                "POST",
                { action: "finalize" },
                "Team rosters finalized",
              )
            }
          />
        )}
      </div>
      {state.phase === "naming" && state.finalization_reason && (
        <p className="attendance-blocker">{state.finalization_reason}</p>
      )}
      {state.phase === "finalized" && (
        <p className="meta">
          {draft
            ? "Rosters are ready for launch. You can still adjust teams and names during Draft; launch checks the current rosters again."
            : "Existing memberships and names are fixed. Eligible newcomers can be admitted separately while existing progress and clocks remain unchanged."}
        </p>
      )}
    </section>
  );
}

function TeamFormation({ data, busy, mutate }: Props) {
  const [plan, setPlan] = useState<AttendancePlan | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const attendanceRequired = data.onboarding.require_attendance !== false;
  const blocked =
    data.onboarding.attendance.enabled ||
    (!attendanceRequired && data.registration.enabled);
  const name = (id: number) =>
    data.participants.find((person) => person.id === id)?.name ||
    `Participant ${id}`;
  return (
    <section className="admin-panel optimize-panel">
      <h2>
        {attendanceRequired
          ? "Form teams from checked-in participants"
          : "Form teams from registered participants"}
      </h2>
      <p>
        {attendanceRequired
          ? "Preview random, balanced groups from checked-in eligible participants. Unconfirmed accounts stay outside the teams."
          : "Preview random, balanced groups from every eligible registered participant. No attendance confirmation is required; no-shows are included."}{" "}
        Unchanged accounts and settings return the same proposal. Review and
        explicitly apply it; no competition clock starts.
      </p>
      <button
        className="button secondary"
        disabled={busy || loading || blocked}
        onClick={async () => {
          setLoading(true);
          setPlan(null);
          setError("");
          try {
            setPlan(
              await request<AttendancePlan>(
                "/admin/onboarding/optimize",
                "POST",
                { preview: true },
              ),
            );
          } catch (error) {
            setError((error as Error).message);
          } finally {
            setLoading(false);
          }
        }}
      >
        {loading
          ? "Forming preview…"
          : plan
            ? "Refresh team preview"
            : attendanceRequired
              ? "Preview checked-in teams"
              : "Preview registered teams"}
      </button>
      {!attendanceRequired && data.registration.enabled && (
        <p className="attendance-blocker">
          Close public registration before previewing or applying teams.
        </p>
      )}
      {data.onboarding.attendance?.enabled && (
        <p className="attendance-blocker">
          Close attendance check-in before previewing or applying the final
          groups
        </p>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {plan && (
        <div className="optimization-review">
          <div className="optimization-totals">
            <span>
              <strong>{plan.groups.length}</strong> reviewed teams
            </span>
            <span>
              <strong>{plan.moves.length}</strong> proposed placements
            </span>
            <span>
              <strong>{plan.unassigned_user_ids.length}</strong> outside final
              groups
            </span>
          </div>
          {plan.warnings.length > 0 && (
            <div className="optimization-warnings" role="status">
              <h3>Review these warnings</h3>
              <ul>
                {plan.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="optimized-groups">
            {plan.groups.map((group, index) => (
              <section key={`${group.team_id}-${index}`}>
                <div>
                  <h3>{group.name}</h3>
                  <span>
                    {group.user_ids.length} / {data.onboarding.team_size} people
                  </span>
                </div>
                <p>{group.user_ids.map(name).join(", ")}</p>
              </section>
            ))}
          </div>
          <details className="optimization-details">
            <summary>
              Review participant placements ({plan.moves.length})
            </summary>
            {plan.moves.length ? (
              <ul>
                {plan.moves.map((move) => (
                  <li key={move.user_id}>
                    <strong>{name(move.user_id)}</strong>
                    <span>
                      {data.teams.find((team) => team.id === move.from_team_id)
                        ?.name || "Unassigned"}{" "}
                      → {plan.groups[move.to_group]?.name || "Final group"}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p>Existing safe placements remain unchanged</p>
            )}
          </details>
          {plan.unassigned_user_ids.length > 0 && (
            <details className="optimization-details">
              <summary>
                Accounts outside final groups ({plan.unassigned_user_ids.length}
                )
              </summary>
              <p>{plan.unassigned_user_ids.map(name).join(", ")}</p>
              <p className="meta">
                These accounts remain registered. Organizers can review later
                placement separately.
              </p>
            </details>
          )}
          <p className="meta">
            This plan is tied to current accounts, registration, attendance,
            capacity, memberships and naming policy. If anything changes, the
            server rejects it and you must preview again.
          </p>
          <Confirm
            key={`${plan.fingerprint}-${data.onboarding.allow_team_naming}`}
            label="Apply reviewed teams"
            description={`Create exactly the reviewed teams from ${attendanceRequired ? "checked-in" : "registered"} eligible participants? Teams receive generated names such as Team 1 and Team 2. Renaming is optional and enabled members can change names during Draft, Live and Paused play. Review and finalize the rosters before starting the event; no clock starts now.`}
            disabled={busy || loading || blocked || !plan.groups.length}
            onConfirm={async () => {
              await mutate(
                "/admin/onboarding/optimize",
                "POST",
                { preview: false, fingerprint: plan.fingerprint },
                "Teams formed; review and finalize the rosters",
              );
              setPlan(null);
            }}
          />
        </div>
      )}
    </section>
  );
}

function LateQueue({ data, busy, mutate }: Props) {
  const [selected, setSelected] = useState<number[]>([]),
    [reason, setReason] = useState("");
  const queue = data.participants.filter(
    (person) =>
      eligible(person) &&
      (data.onboarding.require_attendance === false || person.ready_at) &&
      !person.team_id,
  );
  const queueKey = queue.map((person) => person.id).join(",");
  useEffect(
    () =>
      setSelected((current) =>
        current.filter((id) => queue.some((person) => person.id === id)),
      ),
    [queueKey],
  );
  if (!queue.length) return null;
  return (
    <section className="admin-panel late-arrival-queue">
      <h2>Participants awaiting placement</h2>
      <p>
        Place late arrivals in an existing final group with space. Every
        placement needs a reason. In attendance mode, resolve checked-in
        participants before finalizing.
      </p>
      {queue.map((person) => (
        <LatePlacement
          key={person.id}
          person={person}
          data={data}
          busy={busy}
          mutate={mutate}
        />
      ))}
      <details className="additional-team">
        <summary>Create an additional team for late arrivals</summary>
        <p>
          When the existing groups are full, create another group without moving
          their members or changing their names.{" "}
          {data.onboarding.allow_team_naming
            ? "Members can change the name during Draft, Live and Paused play."
            : "The new group receives a generated Team N name."}
        </p>
        <fieldset disabled={busy}>
          <legend>
            Select up to {data.onboarding.team_size} eligible participants
          </legend>
          <div className="late-team-choices">
            {queue.map((person) => (
              <label key={person.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(person.id)}
                  disabled={
                    !selected.includes(person.id) &&
                    selected.length >= data.onboarding.team_size
                  }
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, person.id]
                        : selected.filter((id) => id !== person.id),
                    )
                  }
                />
                <span>
                  {person.name}
                  <small>{person.email}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field">
          <span>Reason for creating an additional team</span>
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            disabled={busy}
            required
          />
        </label>
        <Confirm
          key={`additional-${data.onboarding.allow_team_naming}`}
          label="Create additional team"
          description={`Create an additional team for ${selected.length} selected eligible participant${selected.length === 1 ? "" : "s"}? Existing groups stay intact. ${data.onboarding.allow_team_naming ? "Members can rename it during Draft, Live and Paused play." : "Its generated name is valid; renaming is optional."}`}
          disabled={
            busy ||
            !selected.length ||
            selected.length > data.onboarding.team_size ||
            !reason.trim()
          }
          onConfirm={async () => {
            await mutate(
              "/admin/onboarding/action",
              "POST",
              { action: "create_late_team", user_ids: selected, reason },
              "Additional late-arrival team created",
            );
            setSelected([]);
            setReason("");
          }}
        />
      </details>
    </section>
  );
}
function LatePlacement({
  person,
  data,
  busy,
  mutate,
}: Props & { person: Participant }) {
  const [teamId, setTeamId] = useState(""),
    [reason, setReason] = useState("");
  const available = data.teams.filter(
    (team) =>
      !team.hidden &&
      !team.banned &&
      team.user_ids.length < data.onboarding.team_size,
  );
  return (
    <form
      className="late-placement"
      onSubmit={(event) => {
        event.preventDefault();
        const team = available.find((team) => team.id === Number(teamId));
        if (!team) return;
        void mutate(
          "/admin/onboarding/move",
          "POST",
          {
            user_id: person.id,
            team_id: team.id,
            fingerprint: data.onboarding.fingerprint,
            reason,
          },
          `${person.name} placed in ${team.name}`,
        ).catch(() => {});
      }}
    >
      <div>
        <strong>{person.name}</strong>
        <span>
          {person.email}
          {data.onboarding.require_attendance !== false &&
            ` · ${person.late ? "Late arrival" : "Checked in"}`}
        </span>
      </div>
      <label>
        <span className="sr-only">Team for {person.name}</span>
        <select
          value={teamId}
          onChange={(event) => setTeamId(event.target.value)}
          required
          disabled={busy}
        >
          <option value="">
            {available.length
              ? "Choose a team with space"
              : "All teams are at capacity"}
          </option>
          {available.map((team) => (
            <option key={team.id} value={team.id}>
              {team.name} · {team.user_ids.length}/{data.onboarding.team_size}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="sr-only">Placement reason for {person.name}</span>
        <input
          placeholder="Organizer reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          required
          disabled={busy}
        />
      </label>
      <button
        className="button secondary"
        disabled={busy || !teamId || !reason.trim()}
      >
        Place participant
      </button>
    </form>
  );
}

function RosterTeam({ team, data, busy, mutate }: Props & { team: Team }) {
  const [identity, setIdentity] = useState({
      base: team.name,
      value: team.name,
    }),
    [members, setMembers] = useState(team.user_ids.map(String)),
    [excluded, setExcluded] = useState(team.excluded),
    [reason, setReason] = useState("");
  const membershipKey = team.user_ids.join(",");
  useEffect(() => {
    setIdentity((current) =>
      current.value === current.base || current.value === team.name
        ? { base: team.name, value: team.name }
        : current,
    );
  }, [team.name]);
  useEffect(() => setMembers(team.user_ids.map(String)), [membershipKey]);
  useEffect(() => setExcluded(team.excluded), [team.excluded]);
  const phase = data.onboarding.phase;
  const draft = data.event.status === "draft";
  const provisional = draft && (phase === "check_in" || phase === "closed");
  const identityEditable = ["draft", "live", "paused"].includes(
    data.event.status,
  );
  const name = identity.value;
  const nameConflict = identity.base !== team.name;
  const published = data.event.status === "published";
  const people = teamPeople(data, team);
  const membersChanged =
    members.length !== team.user_ids.length ||
    members.some((id) => !team.user_ids.includes(Number(id)));
  const changed =
    (identityEditable && name !== team.name) ||
    (provisional && membersChanged) ||
    excluded !== team.excluded;
  const reasonRequired =
    (name !== team.name && (team.name_locked || !provisional)) ||
    excluded !== team.excluded;
  return (
    <details className="attendance-team">
      <summary>
        <span className="attendance-team-name">
          {team.name}
          <small>{team.location?.label || "Location not assigned yet"}</small>
          <small>
            {team.excluded
              ? "Excluded from awards"
              : phase === "check_in" || phase === "closed"
                ? "Provisional"
                : draft
                  ? "Reviewed team"
                  : "Final team"}
          </small>
        </span>
        <span className="attendance-team-size">
          {team.user_ids.length}
          <small>/{data.onboarding.team_size} people</small>
        </span>
        {data.onboarding.require_attendance !== false && (
          <span
            className={
              team.ready_count === team.user_ids.length && team.ready_count > 0
                ? "member-ready"
                : "meta"
            }
          >
            {team.ready_count} ready
            {team.user_ids.length > team.ready_count
              ? ` · ${team.user_ids.length - team.ready_count} pending`
              : ""}
          </span>
        )}
        <span className="meta">View team</span>
      </summary>
      <div className="attendance-team-body">
        <ul className="organizer-member-list">
          {people.map((person) => (
            <li key={person.id}>
              <div>
                <strong>{person.name}</strong>
                <span>{person.email}</span>
              </div>
              <span>
                {person.banned
                  ? "Ineligible"
                  : data.onboarding.require_attendance === false
                    ? "Eligible participant"
                    : person.ready_at
                      ? person.late
                        ? "Ready · Late arrival"
                        : "Ready"
                      : "Pending check-in"}
              </span>
            </li>
          ))}
        </ul>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (nameConflict) return;
            void mutate(
              `/admin/teams/${team.id}`,
              "PATCH",
              {
                ...(identityEditable && name !== team.name
                  ? { name, expected_name: identity.base }
                  : {}),
                ...(provisional && membersChanged
                  ? { user_ids: members.map(Number) }
                  : {}),
                ...(excluded !== team.excluded ? { excluded } : {}),
                ...(reason.trim() ? { reason } : {}),
              },
              "Team updated",
            ).catch(() => {});
          }}
        >
          <div className="form-grid">
            <label className="field">
              <span>
                {provisional
                  ? "Provisional team name"
                  : "Organizer name correction"}
              </span>
              <input
                value={name}
                maxLength={128}
                required
                disabled={!identityEditable || busy}
                onChange={(event) =>
                  setIdentity((current) => ({
                    ...current,
                    value: event.target.value,
                  }))
                }
              />
              <small>
                {!identityEditable
                  ? "Team names are read-only after the event closes"
                  : provisional
                    ? "Participant name choice follows the saved naming policy after teams are formed"
                    : "A name correction requires a reason. Team number, location, progress and clocks stay unchanged."}
              </small>
            </label>
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={excluded}
                disabled={published || busy}
                onChange={(event) => setExcluded(event.target.checked)}
              />
              Exclude from awards and public standings
            </label>
          </div>
          {nameConflict && (
            <Notice>
              The team name changed to “{team.name}”. Your unsaved name is
              retained.{" "}
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() =>
                  setIdentity((current) => ({ ...current, base: team.name }))
                }
              >
                Review latest name and keep my edit
              </button>
            </Notice>
          )}
          {provisional && (
            <label className="field">
              <span>Provisional members</span>
              <select
                multiple
                value={members}
                size={Math.min(8, Math.max(3, data.participants.length))}
                disabled={busy}
                onChange={(event) =>
                  setMembers(
                    Array.from(
                      event.target.selectedOptions,
                      (option) => option.value,
                    ),
                  )
                }
              >
                {data.participants
                  .filter(
                    (person) =>
                      eligible(person) &&
                      (!person.team_id || person.team_id === team.id),
                  )
                  .map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.name}
                      {data.onboarding.require_attendance !== false &&
                        ` · ${person.ready_at ? "Ready" : "Pending"}`}
                    </option>
                  ))}
              </select>
              <small>
                Maximum {data.onboarding.team_size} members. Use Command or
                Control to select several people.
              </small>
            </label>
          )}
          <label className="field">
            <span>Organizer reason{reasonRequired ? " (required)" : ""}</span>
            <input
              value={reason}
              required={reasonRequired}
              disabled={published || busy}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Explain a final name or prize eligibility correction"
            />
          </label>
          <button
            className="button secondary"
            disabled={
              busy ||
              published ||
              nameConflict ||
              !changed ||
              (provisional &&
                membersChanged &&
                members.length > data.onboarding.team_size) ||
              (reasonRequired && !reason.trim())
            }
          >
            Save team changes
          </button>
        </form>
      </div>
    </details>
  );
}

function ParticipantList({
  people,
  data,
}: {
  people: Participant[];
  data: AdminState;
}) {
  if (!people.length)
    return (
      <Empty title="No matching participants">
        Try another search or attendance filter.
      </Empty>
    );
  return (
    <div className="attendance-people">
      <table>
        <thead>
          <tr>
            <th scope="col">Participant</th>
            <th scope="col">Team</th>
            <th scope="col">
              {data.onboarding.require_attendance === false
                ? "Status"
                : "Attendance"}
            </th>
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <tr key={person.id}>
              <th scope="row">
                {person.name}
                <small>{person.email}</small>
              </th>
              <td>
                {data.teams.find((team) => team.id === person.team_id)?.name ||
                  "Unassigned"}
              </td>
              <td>
                {person.banned ? (
                  "Ineligible"
                ) : data.onboarding.require_attendance === false ? (
                  "Eligible participant"
                ) : person.ready_at ? (
                  <>
                    <span className="member-ready">
                      Ready{person.late ? " · Late" : ""}
                    </span>
                    <small>{dateTime(person.ready_at)}</small>
                  </>
                ) : (
                  "Pending check-in"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
