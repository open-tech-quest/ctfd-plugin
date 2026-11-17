import { useEffect, useState } from "react";
import { dateTime } from "./api";
import { useParticipantSession } from "./participant-session";
import type { Phase, Viewer } from "./types";
import { Check, Confirm, Notice } from "./ui";
import { TeamNumber } from "./team-number";

export function ParticipantOnboarding({
  viewer,
  phase,
}: {
  viewer: Viewer;
  phase: Phase;
}) {
  if (!viewer.authenticated || viewer.role !== "participant") return null;
  return <PersonalOnboarding key={viewer.id} viewer={viewer} phase={phase} />;
}
function PersonalOnboarding({
  viewer,
  phase,
}: {
  viewer: Viewer;
  phase: Phase;
}) {
  const session = useParticipantSession();
  const team = session?.state?.roster_released ? session.state.team : null;
  const [draft, setDraft] = useState({
    teamId: team?.id,
    base: team?.name || "",
    value: team?.name || "",
  });
  useEffect(() => {
    setDraft((current) =>
      current.teamId !== team?.id ||
      current.value === current.base ||
      current.value === team?.name
        ? { teamId: team?.id, base: team?.name || "", value: team?.name || "" }
        : current,
    );
  }, [team?.id, team?.name]);
  const name = draft.value;
  const nameConflict =
    !!team && (draft.teamId !== team.id || draft.base !== team.name);
  if (!session) return null;
  const { state, busy, error, update, openPrompt, refresh } = session;
  if (!state)
    return error ? (
      <Notice tone="error" retry={() => void refresh()}>
        {error}
      </Notice>
    ) : (
      <div className="onboarding-loading" role="status">
        Loading your team…
      </div>
    );
  const attendanceRequired = state.require_attendance !== false;
  const nameEditor =
    ["draft", "live", "paused"].includes(phase) &&
    team &&
    state.allow_team_naming &&
    state.can_name ? (
      <div className="team-name-claim">
        <div>
          <h3>Change your team name</h3>
          <p>
            Agree with your teammates first. You can change the name during
            Draft, Live and Paused play while organizers allow it. Your team
            number, location and progress stay the same.
          </p>
        </div>
        <label className="field">
          <span>Team name</span>
          <input
            maxLength={128}
            value={name}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                value: event.target.value,
              }))
            }
            disabled={busy}
            autoComplete="off"
          />
        </label>
        {nameConflict && (
          <Notice>
            The team name changed to “{team.name}”. Your unsaved name is
            retained.{" "}
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                setDraft((current) => ({
                  ...current,
                  teamId: team.id,
                  base: team.name,
                }))
              }
            >
              Review latest name and keep my edit
            </button>
          </Notice>
        )}
        <Confirm
          key={`${team.id}-${team.name}-${name}-${state.can_name}`}
          label="Save team name"
          description={`Use “${name.trim()}” as your team's name? Your team number, location and progress stay the same.`}
          disabled={
            busy || nameConflict || !name.trim() || name.trim() === team.name
          }
          onConfirm={() =>
            update("/onboarding/name", {
              name: name.trim(),
              confirm: true,
              expected_name: draft.base,
              team_id: team.id,
            })
          }
        />
      </div>
    ) : null;
  if (phase !== "draft" && team)
    return (
      <>
        {error && (
          <Notice tone="error" retry={() => void refresh()}>
            {error}
          </Notice>
        )}
        <details className="team-roster" id="your-team">
          <summary>
            {nameEditor ? "Team members & name" : "Team members"} (
            {team.members.length})
          </summary>
          <ul className="teammate-list">
            {team.members.map((member) => (
              <li key={member.id}>
                <span>
                  {member.name}
                  {member.id === viewer.id && <small> You</small>}
                </span>
              </li>
            ))}
          </ul>
          {nameEditor}
        </details>
      </>
    );
  if (phase === "closed" || phase === "published") return null;
  return (
    <section className="onboarding-panel" id="your-team" aria-label="Your team">
      {attendanceRequired ? (
        <div className="personal-readiness">
          <div>
            <p className="eyebrow">Your place in the quest</p>
            <h2>
              {state.ready_at
                ? "You're checked in"
                : "Ready to join the quest?"}
            </h2>
            <p>
              {state.ready_at
                ? `${viewer.display_name || viewer.name || "Your attendance"} · Confirmed ${dateTime(state.ready_at)}`
                : state.can_ready
                  ? "Confirm that you're here on your own device. Organizers will bring attending participants into teams."
                  : "Your account is ready. Organizers open attendance check-in separately from registration and team preparation."}
            </p>
            <p className="meta">
              Checking in and changing your team name never start a competition
              clock
            </p>
          </div>
          {state.ready_at ? (
            <span className="readiness-confirmed">
              <Check />{" "}
              {state.late ? "Late arrival recorded" : "Ready to quest"}
            </span>
          ) : state.can_ready ? (
            <button className="button" disabled={busy} onClick={openPrompt}>
              Confirm your attendance
            </button>
          ) : (
            <span className="meta">
              {state.attendance?.enabled
                ? "Attendance confirmation is not available for this account"
                : "Attendance check-in is closed"}
            </span>
          )}
        </div>
      ) : (
        <div className="personal-readiness">
          <div>
            <p className="eyebrow">Your place in the quest</p>
            <h2>{team ? "Your team" : "You're registered"}</h2>
            <p>
              Organizers form teams from registered participants. No attendance
              check-in is needed.
            </p>
          </div>
        </div>
      )}
      {error && (
        <Notice tone="error" retry={() => void refresh()}>
          {error}
        </Notice>
      )}
      {attendanceRequired && state.late && (
        <div className="onboarding-note">
          Your attendance is recorded as a late arrival.
          {!team && " Please see an organizer for team placement."}
        </div>
      )}
      {!state.roster_released ? (
        <div className="onboarding-note">
          <h3>Team details stay private until roster release</h3>
          <p>
            {attendanceRequired
              ? "Your team appears after organizers apply the checked-in grouping plan and release rosters."
              : "Your team appears after organizers close registration, apply the grouping plan and release rosters."}
          </p>
        </div>
      ) : !team ? (
        <div className="onboarding-note">
          <h3>
            {state.phase === "finalized"
              ? "See an organizer about your team"
              : "Your team is being arranged"}
          </h3>
          <p>
            {!attendanceRequired
              ? "Organizers will assign your team. If teams are already formed, ask an organizer about placement."
              : state.ready_at
                ? "Your attendance is saved. An organizer can place you in a team before you can play."
                : state.can_ready
                  ? "Confirm your attendance above, then ask an organizer about team placement."
                  : "Confirm attendance when check-in opens, or ask an organizer about the next check-in window and team placement."}
          </p>
        </div>
      ) : (
        <div className="own-roster">
          <div className="own-team-heading">
            <div>
              <span className="meta">
                {state.phase === "check_in" || state.phase === "closed"
                  ? "Provisional team"
                  : "Your team"}
              </span>
              <h3>{team.name}</h3>
              <TeamNumber number={team.id} location={team.location} />
            </div>
            {attendanceRequired && (
              <span className="meta">
                {team.members.filter((member) => member.ready_at).length} of{" "}
                {team.members.length} checked in
              </span>
            )}
          </div>
          <ul className="teammate-list">
            {team.members.map((member) => (
              <li key={member.id}>
                <span>
                  {member.name}
                  {member.id === viewer.id && <small> You</small>}
                </span>
                {attendanceRequired && (
                  <span className={member.ready_at ? "member-ready" : "meta"}>
                    {member.ready_at ? (
                      <>
                        <Check /> Ready
                      </>
                    ) : (
                      "Not checked in"
                    )}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {(state.phase === "check_in" || state.phase === "closed") && (
            <p className="meta roster-explanation">
              This assignment is provisional until organizers apply the reviewed
              teams.
            </p>
          )}
          {nameEditor ?? (
            <p className="meta roster-explanation">
              {state.allow_team_naming
                ? "An eligible teammate can change the name while participant naming is enabled."
                : "Organizers manage team names. Your current name is valid and does not need confirmation."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
