import { useState } from "react";
import { dateTime } from "./api";
import { useParticipantSession } from "./participant-session";
import type { Viewer } from "./types";
import { Check, Confirm, Notice } from "./ui";
import { TeamNumber } from "./team-number";

export function ParticipantOnboarding({ viewer }: { viewer: Viewer }) {
  if (!viewer.authenticated || viewer.role !== "participant") return null;
  return <PersonalOnboarding key={viewer.id} viewer={viewer} />;
}
function PersonalOnboarding({ viewer }: { viewer: Viewer }) {
  const session = useParticipantSession();
  const [name, setName] = useState("");
  if (!session) return null;
  const { state, busy, error, update, openPrompt, refresh } = session;
  if (!state)
    return error ? (
      <Notice tone="error" retry={() => void refresh()}>
        {error}
      </Notice>
    ) : (
      <div className="onboarding-loading" role="status">
        Checking your team and attendance…
      </div>
    );
  const team = state.roster_released ? state.team : null;
  return (
    <section
      className="onboarding-panel"
      id="your-team"
      aria-label="Your attendance and team"
    >
      <div className="personal-readiness">
        <div>
          <p className="eyebrow">Your place in the quest</p>
          <h2>
            {state.ready_at ? "You're checked in" : "Ready to join the quest?"}
          </h2>
          <p>
            {state.ready_at
              ? `${viewer.name || "Your attendance"} · Confirmed ${dateTime(state.ready_at)}`
              : state.can_ready
                ? "Confirm that you're here on your own device. Your organizers will bring the attending participants into teams."
                : "Your account is ready. Organizers open attendance check-in separately from registration and team preparation."}
          </p>
          <p className="meta">
            {state.allow_team_naming
              ? "Checking in and choosing a team name never start a competition clock"
              : "Checking in never starts a competition clock"}
          </p>
        </div>
        {state.ready_at ? (
          <span className="readiness-confirmed">
            <Check /> {state.late ? "Late arrival recorded" : "Ready to quest"}
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
      {error && (
        <Notice tone="error" retry={() => void refresh()}>
          {error}
        </Notice>
      )}
      {state.late && (
        <div className="onboarding-note">
          Your attendance is recorded as a late arrival.
          {!team && (
            <>
              {" "}
              Please see an organizer for placement
              {state.phase === "finalized"
                ? "; organizers can review controlled late admission when the event permits it"
                : " in a team"}
              .
            </>
          )}
        </div>
      )}
      {!state.roster_released ? (
        <div className="onboarding-note">
          <h3>Team details stay private until roster release</h3>
          <p>
            {state.ready_at
              ? "Your check-in is saved. "
              : state.can_ready
                ? "Confirm your attendance above when you're ready. "
                : "Attendance confirmation is available when organizers open check-in. "}
            Teams are formed from checked-in participants after attendance
            review. Your team appears after that plan is applied and the rosters
            are released.
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
            {state.phase === "check_in"
              ? state.ready_at
                ? "Your attendance is confirmed. Organizers will form teams from checked-in participants after capture closes."
                : "You're registered in the participant pool. Confirm attendance when check-in opens; team formation happens afterwards."
              : state.phase === "closed"
                ? state.ready_at
                  ? "Organizers are reviewing random balanced teams from checked-in participants. Your team appears when the reviewed plan is applied."
                  : "Only checked-in participants enter the reviewed teams. Confirm attendance when capture is available, or ask an organizer about the next check-in window."
                : state.phase === "naming"
                  ? state.ready_at
                    ? "Your readiness is saved. An organizer can place an attending late arrival before rosters are finalized."
                    : state.can_ready
                      ? "Confirm attendance above, then see an organizer about placement before rosters are finalized."
                      : "Ask an organizer about the next late-arrival check-in window and team placement."
                  : state.ready_at
                    ? "Your attendance is saved. Ask an organizer about controlled late admission into an existing team with space or an agreed named new team. You can play after placement when the event permits it."
                    : state.can_ready
                      ? "Confirm attendance above, then an organizer reviews your team placement before you can play."
                      : state.attendance.reason ||
                        "Ask an organizer about opening late check-in and arranging your team placement."}
          </p>
        </div>
      ) : (
        <div className="own-roster">
          <div className="own-team-heading">
            <div>
              <span className="meta">
                {state.phase === "check_in" || state.phase === "closed"
                  ? "Provisional team"
                  : state.phase === "finalized"
                    ? "Final team"
                    : team.name_locked
                      ? "Fixed team name"
                      : "Your final group"}
              </span>
              <h3>{team.name}</h3>
              <TeamNumber number={team.id} />
            </div>
            <span className="meta">
              {team.members.filter((member) => member.ready_at).length} of{" "}
              {team.members.length} checked in
            </span>
          </div>
          <ul className="teammate-list">
            {team.members.map((member) => (
              <li key={member.id}>
                <span>
                  {member.name}
                  {member.id === viewer.id && <small> You</small>}
                </span>
                <span className={member.ready_at ? "member-ready" : "meta"}>
                  {member.ready_at ? (
                    <>
                      <Check /> Ready
                    </>
                  ) : (
                    "Not checked in"
                  )}
                </span>
              </li>
            ))}
          </ul>
          {(state.phase === "check_in" || state.phase === "closed") && (
            <p className="meta roster-explanation">
              Any earlier assignment is provisional. Organizers form the
              reviewed teams from checked-in participants before finalization.
            </p>
          )}
          {state.allow_team_naming && state.can_name && !team.name_locked ? (
            <div className="team-name-claim">
              <div>
                <h3>Give your team its final name</h3>
                <p>
                  Agree with your teammates first. Any checked-in member can
                  confirm the name once; the first confirmation fixes it for
                  your group.
                </p>
              </div>
              <label className="field">
                <span>Team name</span>
                <input
                  maxLength={128}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  disabled={busy}
                  autoComplete="off"
                  placeholder="The name your team agreed on"
                />
              </label>
              <Confirm
                key={`${team.id}-${state.allow_team_naming}-${state.can_name}-${team.name_locked}-${state.phase}`}
                label="Confirm team name"
                description={`Use “${name.trim()}” as your team's final name? This can be confirmed only once. An organizer correction requires a reason.`}
                disabled={busy || !name.trim()}
                onConfirm={() =>
                  update("/onboarding/name", {
                    name: name.trim(),
                    confirm: true,
                  })
                }
              />
            </div>
          ) : state.phase === "naming" &&
            state.allow_team_naming &&
            !team.name_locked ? (
            <p className="onboarding-note">
              {state.ready_at
                ? "A checked-in teammate can confirm your team's name once naming is available."
                : "Check in before confirming your team's name."}
            </p>
          ) : team.name_locked || !state.allow_team_naming ? (
            <p className="meta roster-explanation">
              {state.phase === "finalized"
                ? "Your roster and team name are finalized. Your team shares progress across devices."
                : "Your team name is fixed. Organizers will review and finalize the roster before starting the event."}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
