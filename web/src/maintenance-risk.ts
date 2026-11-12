export interface MaintenanceImpact {
  counted_teams: number;
  solve_records: number;
  hint_purchase_teams: number;
  hint_receipts: number;
  completed_teams: number;
  teams_with_score_changes?: number;
  repriced_solve_records?: number;
  repriced_hint_receipts?: number;
  negative_overall_teams?: number;
  negative_room_teams?: number;
  new_negative_overall_teams?: number;
  new_negative_room_teams?: number;
  team_scores?: {
    team_id: number;
    team_name?: string;
    before_points: number;
    after_points: number;
    before_room_points: number;
    after_room_points: number;
    before_solved: number;
    after_solved: number;
  }[];
}

export interface MaintenanceReview {
  revision: string;
  competitive_fields: string[];
  requires_confirmation: boolean;
  impact: MaintenanceImpact;
  warnings: string[];
}

export interface MaintenanceApproval {
  revision: string;
  reason: string;
  confirm: true;
  acknowledge_negative?: boolean;
}

export function maintenanceRisk(impact: MaintenanceImpact) {
  const rows = impact.team_scores ?? [];
  const newlyNegativeOverall =
    impact.new_negative_overall_teams ??
    rows.filter((team) => team.before_points >= 0 && team.after_points < 0)
      .length;
  const newlyNegativeRoom =
    impact.new_negative_room_teams ??
    rows.filter(
      (team) => team.before_room_points >= 0 && team.after_room_points < 0,
    ).length;
  return {
    affectedTeams: impact.teams_with_score_changes ?? rows.length,
    negativeOverall:
      impact.negative_overall_teams ??
      rows.filter((team) => team.after_points < 0).length,
    negativeRoom:
      impact.negative_room_teams ??
      rows.filter((team) => team.after_room_points < 0).length,
    newlyNegativeOverall,
    newlyNegativeRoom,
    needsAcknowledgment: newlyNegativeOverall > 0 || newlyNegativeRoom > 0,
  };
}

export function canConfirmMaintenance(
  review: MaintenanceReview,
  reason: string,
  acknowledgeNegative: boolean,
  stale = false,
) {
  return (
    !stale &&
    reason.trim().length >= 3 &&
    reason.trim().length <= 1000 &&
    !reason.includes("\0") &&
    (!maintenanceRisk(review.impact).needsAcknowledgment || acknowledgeNegative)
  );
}

export function maintenanceApproval(
  review: MaintenanceReview,
  reason: string,
  acknowledgeNegative: boolean,
  stale = false,
): MaintenanceApproval {
  if (!canConfirmMaintenance(review, reason, acknowledgeNegative, stale)) {
    throw new Error(
      "Review the impact, provide a reason and acknowledge any new negative balances before applying.",
    );
  }
  return {
    revision: review.revision,
    reason: reason.trim(),
    confirm: true,
    ...(acknowledgeNegative ? { acknowledge_negative: true } : {}),
  };
}
