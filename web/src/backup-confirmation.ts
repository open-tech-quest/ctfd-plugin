import type { RestoreReview } from "./backup-types";
import { backupRestoreSupported } from "./backup-presentation.ts";

export function backupReasonValid(reason: string) {
  const length = [...reason.trim()].length;
  return length >= 3 && length <= 1000 && !reason.includes("\0");
}
export function restoreConfirmationValid(
  review: RestoreReview,
  confirmation: string,
  reason: string,
  at = Date.now(),
) {
  const expires = Date.parse(review.expires_at);
  return (
    review.full_replace === true &&
    review.safety_backup_required === true &&
    backupRestoreSupported(review.snapshot) &&
    !!review.confirmation_text &&
    confirmation === review.confirmation_text &&
    backupReasonValid(reason) &&
    Number.isFinite(expires) &&
    at < expires
  );
}
