import { useState } from "react";
import { ConfirmDialog, Notice } from "./ui";

import {
  canConfirmMaintenance,
  maintenanceApproval,
  maintenanceRisk,
} from "./maintenance-risk";
import type {
  MaintenanceApproval,
  MaintenanceReview,
} from "./maintenance-risk";
export type {
  MaintenanceApproval,
  MaintenanceReview,
} from "./maintenance-risk";

export function MaintenanceConfirmation({
  review,
  label,
  description,
  onConfirm,
  onClose,
  stale = false,
  focusFallback,
}: {
  review: MaintenanceReview;
  label: string;
  description: string;
  onConfirm: (approval: MaintenanceApproval) => Promise<void>;
  onClose: () => void;
  stale?: boolean;
  focusFallback?: () => HTMLElement | null;
}) {
  const [reason, setReason] = useState("");
  const [acknowledgeNegative, setAcknowledgeNegative] = useState(false);
  const risk = maintenanceRisk(review.impact);
  return (
    <ConfirmDialog
      label={label}
      description={description}
      disabled={
        !canConfirmMaintenance(review, reason, acknowledgeNegative, stale)
      }
      onClose={onClose}
      focusFallback={focusFallback}
      onConfirm={() =>
        onConfirm(
          maintenanceApproval(review, reason, acknowledgeNegative, stale),
        )
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
        Counts describe this review. Live activity may change them; the server
        recalculates the final impact when you apply the change.
      </p>
      <dl className="hint-purchase-summary maintenance-score-summary">
        <div>
          <dt>Teams whose scores change</dt>
          <dd>{risk.affectedTeams.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Existing solve records to reprice</dt>
          <dd>
            {(review.impact.repriced_solve_records ?? 0).toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>Existing hint purchases to reprice</dt>
          <dd>
            {(review.impact.repriced_hint_receipts ?? 0).toLocaleString()}
          </dd>
        </div>
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
      {(risk.negativeOverall > 0 || risk.negativeRoom > 0) && (
        <div className="maintenance-negative-summary" role="note">
          <h3>Negative points after this change</h3>
          <p>
            <strong>{risk.negativeOverall.toLocaleString()}</strong> teams with
            negative overall points
            {" · "}
            <strong>{risk.negativeRoom.toLocaleString()}</strong> teams with
            negative room points.
          </p>
          <p>
            Newly below zero: {risk.newlyNegativeOverall.toLocaleString()}{" "}
            overall, {risk.newlyNegativeRoom.toLocaleString()} in this room.
            These counts can include the same team.
          </p>
        </div>
      )}
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
                    <th>
                      {team.team_name
                        ? `${team.team_name} (#${team.team_id})`
                        : `#${team.team_id}`}
                    </th>
                    <td
                      className={
                        team.after_points < 0
                          ? "maintenance-negative-points"
                          : undefined
                      }
                    >
                      {team.before_points.toLocaleString()} →{" "}
                      {team.after_points.toLocaleString()}
                      {team.after_points < 0 && (
                        <small>
                          {team.before_points >= 0
                            ? "Newly below zero"
                            : "Below zero"}
                        </small>
                      )}
                    </td>
                    <td
                      className={
                        team.after_room_points < 0
                          ? "maintenance-negative-points"
                          : undefined
                      }
                    >
                      {team.before_room_points.toLocaleString()} →{" "}
                      {team.after_room_points.toLocaleString()}
                      {team.after_room_points < 0 && (
                        <small>
                          {team.before_room_points >= 0
                            ? "Newly below zero"
                            : "Below zero"}
                        </small>
                      )}
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
      {risk.needsAcknowledgment && (
        <label className="checkbox-field maintenance-negative-acknowledgment">
          <input
            type="checkbox"
            checked={acknowledgeNegative}
            onChange={(event) => setAcknowledgeNegative(event.target.checked)}
          />
          <span>
            I understand this change puts teams below zero points in this room
            or overall.
          </span>
        </label>
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
