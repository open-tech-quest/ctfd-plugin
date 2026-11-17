import { useRef, useState } from "react";
import { dateTime, request } from "./api";
import type { AdminState } from "./types";
import { ConfirmDialog, Notice } from "./ui";
import {
  applyWaitingPlacementBody,
  placementIsStale,
  previewWaitingPlacement,
  type WaitingPlacement,
} from "./waiting-placement";

export function WaitingParticipants({
  data,
  busy,
  mutate,
}: {
  data: AdminState;
  busy: boolean;
  mutate: (
    path: string,
    method: string,
    body: unknown,
    success: string,
  ) => Promise<void>;
}) {
  const previewButton = useRef<HTMLButtonElement>(null);
  const [plan, setPlan] = useState<WaitingPlacement | null>(null),
    [seen, setSeen] = useState<number[]>([]),
    [loading, setLoading] = useState(false),
    [invalid, setInvalid] = useState(false),
    [reason, setReason] = useState(""),
    [error, setError] = useState(""),
    [confirm, setConfirm] = useState(false),
    [completed, setCompleted] = useState(false);
  const required = data.onboarding.require_attendance !== false;
  const queue = data.participants.filter(
    (p) => !p.banned && p.team_id === null && (!required || p.ready_at),
  );
  const allowed = !!data.onboarding.late_admission?.allowed;
  const disabled = busy || loading || !allowed;
  const stale = !!plan && (invalid || placementIsStale(plan, data));
  const additional = plan
    ? queue.filter(
        (p) =>
          !seen.includes(p.id) &&
          !plan.participants.some((selected) => selected.id === p.id),
      ).length
    : 0;
  const newTeams = plan?.groups.filter((g) => g.team_id === null).length ?? 0;
  const existingTeams = (plan?.groups.length ?? 0) - newTeams;
  async function preview() {
    setLoading(true);
    setInvalid(true);
    setError("");
    setCompleted(false);
    const observed = queue.map((p) => p.id);
    try {
      const result = await previewWaitingPlacement(request);
      setPlan(result);
      setSeen(observed);
      setInvalid(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <section className="admin-panel waiting-placement">
      <div className="section-heading">
        <h2>Place waiting participants</h2>
        <span className="meta">{queue.length} unassigned</span>
      </div>
      <p>
        Fill spaces in existing competing teams, then create balanced new teams.
        Existing members, team names, numbers, locations and progress stay in
        place. Registration can remain open.
      </p>
      <p className="meta">
        Only eligible, unassigned participants without competition history are
        included{required ? "; attendance must be confirmed first" : ""}.
        Hidden, banned and award-excluded teams are skipped.
      </p>
      {!allowed && <Notice>{data.onboarding.late_admission?.reason}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {completed && (
        <Notice tone="success">
          Participants placed. Use location distribution above to seat teams
          without a location. Leave “Reassign existing locations” unchecked to
          keep current tables.
        </Notice>
      )}
      <button
        ref={previewButton}
        className="button secondary"
        type="button"
        disabled={disabled || !queue.length}
        onClick={() => void preview()}
      >
        {loading
          ? "Building preview…"
          : plan
            ? "Build a new preview"
            : "Preview placement"}
      </button>
      {plan && (
        <div className="waiting-placement-review">
          {plan.participants.length ? (
            <>
              <div
                className="waiting-placement-counts"
                role="group"
                aria-label="Placement totals"
              >
                <p>
                  <strong>{plan.participants.length}</strong> participants
                </p>
                <p>
                  <strong>{existingTeams}</strong> existing teams receiving
                  people
                </p>
                <p>
                  <strong>{newTeams}</strong> new teams
                </p>
              </div>
              <p className="meta">
                Reviewed batch only · Maximum {plan.capacity} per team · Preview
                valid until {dateTime(plan.expires_at)}
                {plan.participants.length === plan.max_batch &&
                  ` · Up to ${plan.max_batch} people per batch; preview again for any remainder`}
              </p>
              {!!additional && (
                <Notice>
                  {additional} more unassigned{" "}
                  {additional === 1 ? "participant has" : "participants have"}{" "}
                  arrived. They stay queued for the next preview; this batch is
                  unchanged.
                </Notice>
              )}
              {stale && (
                <Notice tone="error">
                  This placement needs a fresh preview. It expired, a reviewed
                  participant, destination or event setting changed, or the
                  previous request could not be confirmed. Build a new preview
                  to continue.
                </Notice>
              )}
              <div
                className="waiting-placement-scroll"
                role="region"
                aria-label="Placement preview"
                tabIndex={0}
              >
                <ol className="waiting-placement-groups">
                  {plan.groups.map((group, index) => (
                    <li key={group.team_id ?? `new-${index}`}>
                      <div className="section-heading">
                        <h3>{group.name}</h3>
                        <span className="meta">
                          {group.team_id === null
                            ? "New team · "
                            : `Team number ${group.team_id} · `}
                          {group.existing_user_ids.length +
                            group.user_ids.length}
                          /{plan.capacity}
                        </span>
                      </div>
                      <p className="meta">
                        {group.location?.label ??
                          (group.team_id === null
                            ? "Needs a location after placement"
                            : "No location assigned")}
                      </p>
                      <p>
                        <strong>
                          {group.team_id === null ? "Members: " : "Add: "}
                        </strong>
                        {group.user_ids
                          .map(
                            (id) =>
                              plan.participants.find((p) => p.id === id)?.name,
                          )
                          .join(", ")}
                      </p>
                    </li>
                  ))}
                </ol>
              </div>
              <p className="meta">
                New teams receive their native team numbers when created and
                need locations afterward. Joining an existing team shares its
                current progress and room clocks.
              </p>
              <label className="field">
                <span>Placement reason</span>
                <input
                  value={reason}
                  maxLength={512}
                  disabled={disabled}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="button"
                disabled={disabled || stale || !reason.trim()}
                onClick={() => setConfirm(true)}
              >
                Apply placement
              </button>
            </>
          ) : (
            <p className="meta">
              No eligible participants are waiting for placement.
            </p>
          )}
        </div>
      )}
      {confirm && plan && (
        <ConfirmDialog
          focusFallback={() => previewButton.current}
          label={`Place ${plan.participants.length} participants`}
          description={`Add people to ${existingTeams} existing teams and create ${newTeams} new teams. Existing members, names, numbers, tables, progress and clocks are preserved. New registrations stay queued for the next batch.`}
          disabled={disabled || stale || !reason.trim()}
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            try {
              await mutate(
                "/admin/onboarding/waiting",
                "POST",
                applyWaitingPlacementBody(plan.token, reason),
                `Placed ${plan.participants.length} participants; created ${newTeams} teams`,
              );
              setPlan(null);
              setReason("");
              setCompleted(true);
            } catch (e) {
              setInvalid(true);
              throw e;
            }
          }}
        />
      )}
    </section>
  );
}
