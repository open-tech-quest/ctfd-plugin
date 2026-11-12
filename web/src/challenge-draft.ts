import type { EditableChallenge, EditableFlag, QuestEvent } from "./types";

export interface ChallengeDraft {
  name: string;
  value: number | "";
  description: string;
  connection_info: string;
  state: EditableChallenge["state"];
  flags: EditableFlag[];
  adjust_existing_solves: boolean;
  hints: {
    id?: number;
    title: string;
    content: string;
    cost: number | "";
    prerequisites: number[];
    adjust_existing_purchases: boolean;
  }[];
}

export function challengeDraftOf(
  challenge?: EditableChallenge,
): ChallengeDraft {
  return {
    name: challenge?.name || "",
    value: challenge?.value ?? 100,
    description: challenge?.description || "",
    connection_info: challenge?.connection_info || "",
    state: challenge?.state || "hidden",
    flags: challenge?.flags.map((flag) => ({ ...flag })) || [
      { content: "", case_insensitive: false },
    ],
    adjust_existing_solves: false,
    hints:
      challenge?.hints.map((hint) => ({
        ...hint,
        prerequisites: [...hint.prerequisites],
        adjust_existing_purchases: false,
      })) || [],
  };
}

export function challengePricingChanges(
  draft: ChallengeDraft,
  saved: EditableChallenge | undefined,
  phase: QuestEvent["status"],
) {
  const active = !!saved && (phase === "live" || phase === "paused");
  return {
    challengeValue:
      active && (draft.value !== saved.value || draft.adjust_existing_solves),
    hintIds: active
      ? draft.hints
          .filter(
            (hint) =>
              hint.id !== undefined &&
              saved.hints.some(
                (old) =>
                  old.id === hint.id &&
                  (old.cost !== hint.cost || hint.adjust_existing_purchases),
              ),
          )
          .map((hint) => hint.id!)
      : [],
  };
}

export function challengePayloadOf(
  draft: ChallengeDraft,
  saved: EditableChallenge | undefined,
  phase: QuestEvent["status"],
) {
  const { adjust_existing_solves, hints, ...fields } = draft;
  const changes = challengePricingChanges(draft, saved, phase);
  return {
    ...fields,
    hints: hints.map(
      ({ prerequisites: _, adjust_existing_purchases: __, ...hint }) => hint,
    ),
    ...(changes.challengeValue || changes.hintIds.length
      ? {
          pricing: {
            challenge_existing:
              changes.challengeValue && adjust_existing_solves,
            hint_existing_ids: hints
              .filter(
                (hint) =>
                  hint.id !== undefined &&
                  changes.hintIds.includes(hint.id) &&
                  hint.adjust_existing_purchases,
              )
              .map((hint) => hint.id!),
          },
        }
      : {}),
  };
}
