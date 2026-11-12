import type { Step } from "./types";

export function challengePoints(step: Step, currentValue = step.value) {
  return step.state === "solved" && typeof step.earned_points === "number"
    ? step.earned_points
    : currentValue;
}

export function hintCostAdjustment(
  effectiveCost: number | undefined,
  originalCost: number | undefined,
) {
  if (
    effectiveCost === undefined ||
    originalCost === undefined ||
    effectiveCost === originalCost
  )
    return null;
  return `Original purchase: ${originalCost.toLocaleString()} points. Current deduction after organizer adjustment: ${effectiveCost.toLocaleString()} points.`;
}
