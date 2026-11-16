import { useEffect, useRef, useState } from "react";
import type { AdminState } from "./types";
import { Confirm, Notice } from "./ui";

type Mutation = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => Promise<void>;
export function ParticipantMove({
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
  const [userId, setUserId] = useState("");
  const [teamId, setTeamId] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const fingerprint = data.onboarding.fingerprint;
  const previous = useRef(fingerprint);
  useEffect(() => {
    if (previous.current !== fingerprint) {
      setUserId("");
      setTeamId("");
      setNotice(
        "Participants, teams or settings changed. Select the participant and destination again to review the current placement.",
      );
      previous.current = fingerprint;
    }
  }, [fingerprint]);
  const person = data.participants.find(
    (person) => person.id === Number(userId) && !person.banned,
  );
  const source = data.teams.find((team) => team.id === person?.team_id);
  const destination = data.teams.find(
    (team) => String(team.id) === teamId && !team.hidden && !team.banned,
  );
  const unassign = teamId === "unassigned";
  const capacity = data.onboarding.team_size;
  const attendanceRequired = data.onboarding.require_attendance !== false;
  const needsAttendance =
    person && !unassign && attendanceRequired && !person.ready_at;
  const destinationValid = unassign
    ? !!person?.team_id
    : !!destination &&
      destination.id !== person?.team_id &&
      destination.user_ids.length < capacity;
  const description = person
    ? `Move ${person.name} from ${source?.name || "Unassigned"} to ${destination?.name || "Unassigned"}? ${source ? `${source.name} will have ${source.user_ids.length - 1}/${capacity} members. ` : ""}${destination ? `${destination.name} will have ${destination.user_ids.length + 1}/${capacity} members. ` : "The account remains registered and can be assigned later. "}Recorded attendance and team names stay unchanged.`
    : "Review the participant and destination.";
  return (
    <section className="admin-panel participant-move">
      <details>
        <summary>Move participant</summary>
        <p>
          Assign someone from the pool, move them between teams, or return a
          no-show to unassigned. Available throughout Draft, including after
          roster finalization.
        </p>
        {notice && <Notice>{notice}</Notice>}
        {error && (
          <Notice tone="error" retry={() => void onRefresh()}>
            {error}
          </Notice>
        )}
        <div className="attendance-settings">
          <label className="field">
            <span>Participant to move</span>
            <select
              value={userId}
              disabled={busy}
              onChange={(event) => {
                setUserId(event.target.value);
                setTeamId("");
                setNotice("");
                setError("");
              }}
            >
              <option value="">Choose a participant</option>
              {data.participants
                .filter((person) => !person.banned)
                .map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name} ·{" "}
                    {data.teams.find((team) => team.id === person.team_id)
                      ?.name || "Unassigned"}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>Destination</span>
            <select
              value={teamId}
              disabled={busy || !person}
              onChange={(event) => setTeamId(event.target.value)}
            >
              <option value="">Choose a destination</option>
              {person?.team_id && (
                <option value="unassigned">Unassigned · retain account</option>
              )}
              {data.teams
                .filter(
                  (team) =>
                    !team.hidden && !team.banned && team.id !== person?.team_id,
                )
                .map((team) => (
                  <option
                    key={team.id}
                    value={team.id}
                    disabled={team.user_ids.length >= capacity}
                  >
                    {team.name} · {team.user_ids.length}/{capacity}
                    {team.user_ids.length >= capacity ? " · Full" : ""}
                  </option>
                ))}
            </select>
          </label>
        </div>
        {person && (
          <p className="meta">
            Current team: {source?.name || "Unassigned"}
            {source && ` · ${source.user_ids.length}/${capacity} members`}
            {destination &&
              ` → ${destination.name}: ${destination.user_ids.length + 1}/${capacity} after move`}
            {unassign &&
              source &&
              ` → Unassigned; ${source.name}: ${source.user_ids.length - 1}/${capacity} after move`}
          </p>
        )}
        {needsAttendance && (
          <Notice>
            This participant must confirm attendance before being assigned.
            Returning an assigned participant to unassigned keeps their check-in
            record.
          </Notice>
        )}
        <label className="field">
          <span>Reason for moving participant</span>
          <textarea
            rows={2}
            value={reason}
            maxLength={1000}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <Confirm
          key={`${fingerprint}-${userId}-${teamId}-${reason}`}
          label="Review participant move"
          description={description}
          disabled={
            busy ||
            !fingerprint ||
            !person ||
            !destinationValid ||
            !!needsAttendance ||
            !reason.trim()
          }
          onConfirm={async () => {
            if (!person) return;
            setError("");
            try {
              await mutate(
                "/admin/onboarding/move",
                "POST",
                {
                  user_id: person.id,
                  team_id: unassign ? null : Number(teamId),
                  fingerprint,
                  reason: reason.trim(),
                },
                "Participant moved; attendance and team names preserved",
              );
              setUserId("");
              setTeamId("");
              setReason("");
              setNotice("");
            } catch (error) {
              setError((error as Error).message);
              await onRefresh();
              throw error;
            }
          }}
        />
      </details>
    </section>
  );
}
