import { useEffect, useState } from "react";
import { ApiError, dateTime, request } from "./api";
import type { Phase, Room } from "./types";
import { ConfirmDialog, Notice } from "./ui";
import { changeRoomPause, roomPauseFailure } from "./room-pause";

export function RoomPauseControls({
  room,
  phase,
  disabled,
  updatedAt,
  onChanged,
}: {
  room: Room;
  phase: Phase;
  disabled: boolean;
  updatedAt: number | null;
  onChanged: () => Promise<void>;
}) {
  const [pending, setPending] = useState<{
    action: "pause" | "resume";
    revision: string;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [blockedAt, setBlockedAt] = useState<number | null | undefined>();
  const needsRefresh = blockedAt !== undefined;
  useEffect(() => {
    if (needsRefresh && updatedAt !== blockedAt) setBlockedAt(undefined);
  }, [updatedAt, blockedAt, needsRefresh]);
  const paused = room.pause?.paused === true;
  const action = paused ? "resume" : "pause";
  const available = ["live", "paused"].includes(phase);
  const stale = !!pending && pending.revision !== room.pause?.revision;
  const label = action === "pause" ? "Pause room" : "Resume room";
  return (
    <section className="admin-panel room-pause-controls">
      <h3>{paused ? "Room paused" : "Room availability"}</h3>
      <p>
        Pause this room for every team during an interruption. Its room clocks
        stop and new starts, answers and hint purchases wait. Other rooms remain
        available; overall time and the event closing deadline stay unchanged.
      </p>
      {paused && (
        <Notice>
          Paused {dateTime(room.pause?.started_at)}. {room.pause?.reason}
          {phase === "paused" && (
            <> Resuming the event will leave this room paused.</>
          )}
        </Notice>
      )}
      {message && <Notice tone="success">{message}</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="actions">
        <button
          type="button"
          className="button secondary"
          disabled={
            disabled || !available || needsRefresh || !room.pause?.revision
          }
          onClick={() => {
            setReason("");
            setError("");
            setMessage("");
            setPending({ action, revision: room.pause!.revision });
          }}
        >
          {label}
        </button>
        {needsRefresh && (
          <button
            type="button"
            className="text-button"
            onClick={() => void onChanged()}
          >
            Refresh room status
          </button>
        )}
      </div>
      {!available && (
        <p className="meta">
          Room pauses are available during live or paused events.
        </p>
      )}
      {pending && (
        <ConfirmDialog
          label={pending.action === "pause" ? "Pause room" : "Resume room"}
          description={
            pending.action === "pause"
              ? `Pause “${room.name}” for every team? Recorded progress stays saved. Only this room’s time stops; overall time and the event closing deadline stay unchanged.`
              : `Resume “${room.name}” for every team? ${phase === "paused" ? "The event is still paused, so play will wait until the event also resumes." : "Teams can continue this room from their saved progress."}`
          }
          disabled={
            disabled || !available || needsRefresh || stale || !reason.trim()
          }
          onClose={() => setPending(null)}
          onConfirm={async () => {
            if (stale || needsRefresh) return;
            try {
              await changeRoomPause(
                request,
                room.slug,
                pending.action,
                reason,
                pending.revision,
              );
              setMessage(
                pending.action === "pause"
                  ? "Room paused for all teams"
                  : "Room resumed",
              );
              await onChanged();
            } catch (failure) {
              const message = roomPauseFailure(
                (failure as Error).message,
                failure instanceof ApiError ? failure.status : undefined,
              );
              setError(message);
              setBlockedAt(updatedAt);
              throw new Error(message);
            }
          }}
        >
          <label className="field">
            <span>Reason</span>
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={1000}
              required
            />
          </label>
          <p className="meta">
            Pause reasons are shown to participants. Keep answers and
            credentials out of this message.
          </p>
          {stale && (
            <Notice>
              Room status changed while you were reviewing. Cancel and review
              the current action.
            </Notice>
          )}
          {needsRefresh && (
            <Notice>
              Close this dialog and refresh room status before trying again.
            </Notice>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}
