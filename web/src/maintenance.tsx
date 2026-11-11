import { useState } from "react";
import { ConfirmDialog, Notice } from "./ui";

export interface MaintenanceReview {
  revision: string;
  competitive_fields: string[];
  requires_confirmation: boolean;
  impact: {
    counted_teams: number;
    solve_records: number;
    hint_purchase_teams: number;
    hint_receipts: number;
    completed_teams: number;
    teams_with_score_changes?: number;
    team_scores?: {
      team_id: number;
      before_points: number;
      after_points: number;
      before_room_points: number;
      after_room_points: number;
      before_solved: number;
      after_solved: number;
    }[];
  };
  warnings: string[];
}
export interface MaintenanceApproval {
  revision: string;
  reason: string;
  confirm: true;
}

export function MaintenanceConfirmation({
  review,
  label,
  description,
  onConfirm,
  onClose,
  stale = false,
}: {
  review: MaintenanceReview;
  label: string;
  description: string;
  onConfirm: (approval: MaintenanceApproval) => Promise<void>;
  onClose: () => void;
  stale?: boolean;
}) {
  const [reason, setReason] = useState("");
  return (
    <ConfirmDialog
      label={label}
      description={description}
      disabled={
        stale || reason.trim().length < 3 || reason.trim().length > 1000
      }
      onClose={onClose}
      onConfirm={() =>
        onConfirm({
          revision: review.revision,
          reason: reason.trim(),
          confirm: true,
        })
      }
    >
      <p>
        Changes:{" "}
        {review.competitive_fields
          .map((field) => field.replaceAll("_", " "))
          .join(", ")}
        .
      </p>
      <p>
        Live progress may change while you review; the change applies when
        saved.
      </p>
      <dl className="hint-purchase-summary">
        <div>
          <dt>Teams with counted solves</dt>
          <dd>{review.impact.counted_teams.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Recorded solves</dt>
          <dd>{review.impact.solve_records.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Teams with hint purchases</dt>
          <dd>{review.impact.hint_purchase_teams.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Recorded hint purchases</dt>
          <dd>{review.impact.hint_receipts.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Teams that completed this room</dt>
          <dd>{review.impact.completed_teams.toLocaleString()}</dd>
        </div>
      </dl>
      {!!review.impact.team_scores?.length && (
        <details className="maintenance-impact">
          <summary>
            Team score changes (
            {review.impact.teams_with_score_changes ??
              review.impact.team_scores.length}
            )
          </summary>
          <div
            className="maintenance-impact-table"
            tabIndex={0}
            role="region"
            aria-label="Team score impact"
          >
            <table>
              <thead>
                <tr>
                  <th>Team</th>
                  <th>Overall points</th>
                  <th>Room points</th>
                  <th>Solved</th>
                </tr>
              </thead>
              <tbody>
                {review.impact.team_scores.map((team) => (
                  <tr key={team.team_id}>
                    <th>#{team.team_id}</th>
                    <td>
                      {team.before_points.toLocaleString()} →{" "}
                      {team.after_points.toLocaleString()}
                    </td>
                    <td>
                      {team.before_room_points.toLocaleString()} →{" "}
                      {team.after_room_points.toLocaleString()}
                    </td>
                    <td>
                      {team.before_solved} → {team.after_solved}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {review.warnings.map((warning, index) => (
        <Notice key={index}>{warning}</Notice>
      ))}
      {stale && (
        <Notice>
          The saved content or event state changed during this review. Cancel
          and review the latest saved version before applying changes.
        </Notice>
      )}
      <label className="field">
        <span>Reason for this change</span>
        <textarea
          rows={3}
          minLength={3}
          maxLength={1000}
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <small>
          Use 3–1,000 characters. Visible in the organizer audit. Do not include
          accepted answers or other secrets.
        </small>
      </label>
    </ConfirmDialog>
  );
}
