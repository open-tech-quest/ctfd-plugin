import type { RegistrationState } from "./types";
import { Confirm } from "./ui";

export function RegistrationControls({
  state,
  busy,
  onChange,
}: {
  state?: RegistrationState;
  busy: boolean;
  onChange: (enabled: boolean) => Promise<void>;
}) {
  if (!state)
    return (
      <section className="admin-panel">
        <h2>Public registration</h2>
        <p className="section-copy">
          Registration status is not available. Refresh organizer controls
          before changing it.
        </p>
      </section>
    );
  return (
    <section className="admin-panel">
      <div className="section-heading">
        <h2>Public registration</h2>
        <span className="meta">{state.enabled ? "Open" : "Closed"}</span>
      </div>
      <p className="section-copy">
        {state.enabled
          ? "New participants can create their own account. Close registration when you no longer want new public sign-ups."
          : "New public sign-ups are closed. Existing participants can still sign in; organizers manage attendance and team placement separately."}
      </p>
      <Confirm
        key={state.enabled ? "open" : "closed"}
        label={state.enabled ? "Close registration" : "Open registration"}
        description={
          state.enabled
            ? "Stop new public account registrations, including forms that are already open. Existing accounts, sign-in, attendance and team progress stay available."
            : "Allow new participants to create a personal account using the public registration form. This does not change attendance, team allocation or the event phase."
        }
        danger={state.enabled}
        disabled={busy}
        onConfirm={() => onChange(!state.enabled)}
      />
    </section>
  );
}
