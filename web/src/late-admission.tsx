import { useEffect, useRef, useState } from "react";
import type { AdminState } from "./types";
import { Confirm, Notice } from "./ui";

type Mutation = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => Promise<void>;
export function LateAdmission({
  data,
  busy,
  mutate,
  onRefresh,
}: {
  data: AdminState;
  busy: boolean;
  mutate: Mutation;
  onRefresh: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<number[]>([]),
    [mode, setMode] = useState<"existing" | "new">("existing"),
    [teamId, setTeamId] = useState(""),
    [name, setName] = useState(""),
    [reason, setReason] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const admission = data.onboarding.late_admission;
  const previousFingerprint = useRef(admission?.fingerprint);
  useEffect(() => {
    if (previousFingerprint.current !== admission?.fingerprint) {
      setSelected([]);
      setNotice(
        "Participants or team availability changed. Review the current queue and select newcomers again.",
      );
      previousFingerprint.current = admission?.fingerprint;
    }
  }, [admission?.fingerprint]);
  if (!admission)
    return (
      <section className="admin-panel">
        <h2>Late admission</h2>
        <p>
          Late-admission status is not available. Refresh organizer controls
          before placing newcomers.
        </p>
      </section>
    );
  const attendanceRequired = data.onboarding.require_attendance !== false;
  const capacity = data.onboarding.team_size;
  const queue = data.participants.filter(
    (person) =>
      !person.banned &&
      (!attendanceRequired || person.ready_at) &&
      !person.team_id,
  );
  const teams = data.teams.filter(
    (team) => !team.hidden && !team.banned && team.user_ids.length < capacity,
  );
  const target = teams.find((team) => team.id === Number(teamId));
  const names = queue
    .filter((person) => selected.includes(person.id))
    .map((person) => person.name);
  const selectedCurrent =
    selected.length > 0 &&
    names.length === selected.length &&
    selected.length <= capacity;
  const nameValid = !!name.trim();
  const nameTaken = data.teams.some(
    (team) =>
      team.name.trim().toLocaleLowerCase() === name.trim().toLocaleLowerCase(),
  );
  const targetValid =
    mode === "existing"
      ? !!target && target.user_ids.length + selected.length <= capacity
      : nameValid && !nameTaken;
  const enabled = admission.allowed && !!admission.fingerprint && !busy;
  const finalName =
    mode === "existing" ? target?.name || "the selected team" : name.trim();
  const confirmation = `Admit ${names.join(", ")} to “${finalName}”? ${mode === "new" ? "This creates a team with this name. It begins without progress or starts and must explicitly start each room." : "They share this team’s existing progress and room starts."} Existing players, team names, recorded solves and competition clocks remain unchanged. This is not a rebalance.`;
  return (
    <section className="admin-panel late-admission">
      <div className="section-heading">
        <h2>Late admission</h2>
        <button
          type="button"
          className="text-button"
          disabled={busy}
          onClick={() => void onRefresh()}
        >
          Refresh queue
        </button>
      </div>
      <p>
        Admit eligible unassigned newcomers to an existing team with space, or
        create an additional team.{" "}
        {attendanceRequired
          ? "Newcomers must confirm attendance first. "
          : "Attendance confirmation is not required. "}
        Existing players are not moved by late admission.
      </p>
      {!admission.allowed && (
        <Notice>
          {admission.reason || "Late admission is not currently available"}
        </Notice>
      )}
      {notice && queue.length > 0 && <Notice>{notice}</Notice>}
      {error && (
        <Notice tone="error" retry={() => void onRefresh()}>
          {error}
        </Notice>
      )}
      {!queue.length ? (
        <p className="meta">No eligible newcomers are waiting for placement.</p>
      ) : (
        <>
          <fieldset disabled={!enabled}>
            <legend>
              Choose up to {capacity}{" "}
              {attendanceRequired ? "checked-in" : "eligible"} newcomers
            </legend>
            <div className="late-team-choices">
              {queue.map((person) => (
                <label key={person.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(person.id)}
                    disabled={
                      !selected.includes(person.id) &&
                      selected.length >= capacity
                    }
                    onChange={(event) => {
                      setSelected(
                        event.target.checked
                          ? [...selected, person.id]
                          : selected.filter((id) => id !== person.id),
                      );
                      setNotice("");
                      setError("");
                    }}
                  />
                  <span>
                    {person.name}
                    <small>{person.email}</small>
                  </span>
                </label>
              ))}
            </div>
            <div
              className="admission-mode"
              role="group"
              aria-label="Late team placement"
            >
              <label className="checkbox-field">
                <input
                  type="radio"
                  name="admission-mode"
                  value="existing"
                  checked={mode === "existing"}
                  onChange={() => setMode("existing")}
                />
                Join an existing team
              </label>
              <label className="checkbox-field">
                <input
                  type="radio"
                  name="admission-mode"
                  value="new"
                  checked={mode === "new"}
                  onChange={() => setMode("new")}
                />
                Create an agreed named team
              </label>
            </div>
            {mode === "existing" ? (
              <label className="field">
                <span>Existing team and capacity</span>
                <select
                  value={teamId}
                  onChange={(event) => setTeamId(event.target.value)}
                >
                  <option value="">
                    {teams.length
                      ? "Choose a team with space"
                      : "No eligible team has space"}
                  </option>
                  {teams.map((team) => (
                    <option
                      key={team.id}
                      value={team.id}
                      disabled={
                        team.user_ids.length + selected.length > capacity
                      }
                    >
                      {team.name} · {team.user_ids.length}/{capacity} members ·{" "}
                      {capacity - team.user_ids.length} spaces
                      {team.excluded ? " · Excluded from awards" : ""}
                    </option>
                  ))}
                </select>
                <small>
                  Only visible, non-banned teams are offered. All existing
                  members count toward capacity.
                </small>
                {target &&
                  target.user_ids.length + selected.length > capacity && (
                    <small>
                      Choose a team with enough space for every selected
                      newcomer
                    </small>
                  )}
              </label>
            ) : (
              <label className="field">
                <span>New team name</span>
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={128}
                  autoComplete="off"
                />
                <small>
                  Choose a unique name or an unused Team N name. Enabled
                  teammates can rename it while the event remains in Draft.
                </small>
                {name.trim() && (!nameValid || nameTaken) && (
                  <small>
                    {nameTaken
                      ? "A team already uses this name"
                      : "Choose a non-empty final team name"}
                  </small>
                )}
              </label>
            )}
            <label className="field">
              <span>Organizer reason</span>
              <textarea
                rows={2}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                maxLength={2000}
              />
            </label>
          </fieldset>
          <p className="meta">
            Admission changes no readiness timestamp, past solve, event origin,
            pause or existing room start. A new team explicitly starts its rooms
            while the event is live.
          </p>
          <Confirm
            key={`${admission.fingerprint}-${selected.join(",")}-${mode}-${teamId}-${name}`}
            label={
              mode === "new"
                ? "Create team and admit newcomers"
                : "Admit to selected team"
            }
            description={confirmation}
            disabled={
              !enabled || !selectedCurrent || !targetValid || !reason.trim()
            }
            onConfirm={async () => {
              if (!admission.fingerprint) return;
              setError("");
              try {
                await mutate(
                  "/admin/onboarding/admit",
                  "POST",
                  {
                    user_ids: selected,
                    ...(mode === "existing"
                      ? { team_id: Number(teamId) }
                      : { new_team_name: name.trim() }),
                    reason: reason.trim(),
                    fingerprint: admission.fingerprint,
                  },
                  "Late newcomers admitted; existing players and clocks preserved",
                );
                setSelected([]);
                setName("");
                setReason("");
                setNotice("");
              } catch (error) {
                setError((error as Error).message);
                await onRefresh();
                throw error;
              }
            }}
          />
        </>
      )}
    </section>
  );
}
