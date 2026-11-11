export interface OrderedHint {
  id: number;
  content?: string;
}
/** Native hint IDs define a stable order independent of costs or redeemed gaps. */
export function redeemedHintIds<T extends OrderedHint>(
  hints: readonly T[],
  costs: Readonly<Record<number, number>>,
  localText: Readonly<Record<number, string>>,
) {
  return new Set(
    hints
      .filter(
        (hint) =>
          Object.hasOwn(costs, hint.id) ||
          typeof hint.content === "string" ||
          typeof localText[hint.id] === "string",
      )
      .map((hint) => hint.id),
  );
}
export function hintProgression<T extends { id: number }>(
  hints: readonly T[],
  redeemed: ReadonlySet<number>,
  inspectAll = false,
) {
  const ordered = [...hints].sort((a, b) => a.id - b.id);
  const next = ordered.find((hint) => !redeemed.has(hint.id));
  return {
    visible: ordered
      .map((hint, index) => ({
        hint,
        number: index + 1,
        redeemed: redeemed.has(hint.id),
      }))
      .filter(
        (item) => inspectAll || item.redeemed || item.hint.id === next?.id,
      ),
    nextId: next?.id ?? null,
    empty: ordered.length === 0,
    complete: ordered.length > 0 && !next,
  };
}
