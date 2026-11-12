import { archiveText, type ArchiveRecord } from "./archive-format.ts";

export function archivePriceScope(row: ArchiveRecord) {
  return row.apply_existing === true
    ? row.kind === "hint"
      ? "Existing and new purchases"
      : "Existing and new solves"
    : row.kind === "hint"
      ? "New purchases only"
      : "New solves only";
}

export function archivePriceTarget(
  row: ArchiveRecord,
  challenge?: ArchiveRecord,
  hint?: ArchiveRecord,
) {
  const name = archiveText(challenge?.name, `Challenge ${row.challenge_id}`);
  return row.kind === "hint"
    ? `${name} · ${archiveText(hint?.title, `Hint ${row.hint_id}`) || `Hint ${row.hint_id}`} cost`
    : `${name} points`;
}

export function archiveHintPurchaseFields(row: ArchiveRecord): ArchiveRecord {
  const { cost, effective_cost, ...history } = row;
  return {
    ...history,
    original_purchase_cost: cost ?? null,
    current_effective_deduction: effective_cost ?? cost ?? null,
  };
}
