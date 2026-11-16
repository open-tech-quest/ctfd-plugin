import { useId } from "react";
import type { AttendanceState, QuestEvent } from "./types";

export function TeamNamingPolicy({
  state,
  event,
  busy,
  onChange,
}: {
  state: AttendanceState;
  event: QuestEvent;
  busy: boolean;
  onChange: (enabled: boolean) => Promise<void>;
}) {
  const helpId = useId();
  const editable = event.status === "draft";
  return (
    <div className="team-naming-policy">
      <label className="checkbox-field">
        <input
          type="checkbox"
          checked={!!state.allow_team_naming}
          disabled={busy || !editable}
          aria-describedby={helpId}
          onChange={(event) => {
            const enabled = event.currentTarget.checked;
            void onChange(enabled).catch(() => {});
          }}
        />
        Let participants choose team names
      </label>
      <p id={helpId} className="meta">
        {state.allow_team_naming
          ? "Current teammates can change their team's unique name throughout Draft, including after roster finalization. Names freeze when the event goes Live."
          : "Teams keep their generated or chosen names. Names such as Team 1 and Team 2 are valid; renaming is always optional."}
      </p>
      <p className="meta">
        {editable
          ? "Turning this off stops participant renaming and keeps current names. Team names never block roster finalization or event start."
          : "Participant naming is available only while the event is in Draft."}
      </p>
    </div>
  );
}
