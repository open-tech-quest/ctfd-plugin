import type { request } from "./api";
import type { EditableChallenge } from "./types";
import type { MaintenanceApproval, MaintenanceReview } from "./maintenance";

export interface ChallengeTarget {
  room: string;
  id?: number;
  revision?: string;
}
function pathOf(target: ChallengeTarget) {
  return `/manage/rooms/${target.room}/challenges${target.id === undefined ? "" : `/${target.id}`}`;
}
export function reviewChallengeWrite(
  send: typeof request,
  target: ChallengeTarget,
  changes: object,
) {
  return send<MaintenanceReview>(`${pathOf(target)}/review`, "POST", {
    changes,
    revision: target.revision,
  });
}
export function saveChallengeWrite(
  send: typeof request,
  target: ChallengeTarget,
  changes: object,
  approval?: MaintenanceApproval,
) {
  return send<EditableChallenge>(
    pathOf(target),
    target.id === undefined ? "POST" : "PATCH",
    {
      ...changes,
      ...(approval ? { maintenance: approval } : {}),
    },
    undefined,
    target.revision
      ? { "X-Quest-Content-Revision": target.revision }
      : undefined,
  );
}
