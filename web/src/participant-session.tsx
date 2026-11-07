import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { request } from "./api";
import type { Onboarding, Viewer } from "./types";
import { Notice } from "./ui";

type ParticipantState = {
  viewer: Viewer;
  state: Onboarding | null;
  loading: boolean;
  error: string;
  busy: boolean;
  refresh: () => Promise<void>;
  update: (path: string, body: unknown) => Promise<void>;
  openPrompt: () => void;
};
const ParticipantContext = createContext<ParticipantState | null>(null);
export function useParticipantSession() {
  return useContext(ParticipantContext);
}

export function ParticipantSession({
  viewer,
  onboarding,
  loading,
  syncError,
  onChanged,
  children,
}: {
  viewer?: Viewer;
  onboarding: Onboarding | null;
  loading: boolean;
  syncError: string;
  onChanged: () => Promise<void>;
  children: ReactNode;
}) {
  if (!viewer?.authenticated || viewer.role !== "participant" || !viewer.id)
    return (
      <ParticipantContext.Provider value={null}>
        {children}
      </ParticipantContext.Provider>
    );
  return (
    <AuthenticatedParticipantSession
      key={viewer.id}
      viewer={viewer}
      state={onboarding}
      loading={loading}
      syncError={syncError}
      onChanged={onChanged}
    >
      {children}
    </AuthenticatedParticipantSession>
  );
}

function AuthenticatedParticipantSession({
  viewer,
  state,
  loading,
  syncError,
  onChanged,
  children,
}: {
  viewer: Viewer;
  state: Onboarding | null;
  loading: boolean;
  syncError: string;
  onChanged: () => Promise<void>;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [dismissedGeneration, setDismissedGeneration] = useState<number | null>(
      null,
    );
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function update(path: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      await request<Onboarding>(path, "POST", body);
      if (!mounted.current) return;
      await onChanged();
    } catch (error) {
      if (!mounted.current) return;
      setError((error as Error).message);
      await onChanged();
      throw error;
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  useEffect(() => setError(""), [state?.attendance?.generation]);
  const eligible =
    !!state?.can_ready && !!state.attendance?.enabled && !state.ready_at;
  const showPrompt =
    eligible && dismissedGeneration !== state?.attendance.generation;
  function dismiss() {
    if (state) setDismissedGeneration(state.attendance.generation);
  }
  function openPrompt() {
    if (eligible) {
      setError("");
      setDismissedGeneration(null);
    }
  }
  return (
    <ParticipantContext.Provider
      value={{
        viewer,
        state,
        loading,
        error: error || syncError,
        busy,
        refresh: onChanged,
        update,
        openPrompt,
      }}
    >
      {children}
      {eligible && (
        <aside
          className="participant-attendance-bar"
          aria-label="Attendance check-in"
        >
          <span>
            {state?.attendance.late_only
              ? "Late attendance check-in is open"
              : "Attendance check-in is open"}
          </span>
          <button className="text-button" onClick={openPrompt}>
            Confirm your attendance
          </button>
        </aside>
      )}
      {error && !showPrompt && (
        <div className="participant-attendance-feedback">
          <Notice tone="error">
            {error}{" "}
            <button className="text-button" onClick={() => setError("")}>
              Dismiss notice
            </button>
          </Notice>
        </div>
      )}
      {showPrompt && (
        <AttendancePrompt
          key={`${state.attendance.generation}-${state.attendance.late_only}`}
          lateOnly={state.attendance.late_only}
          busy={busy}
          error={error || syncError}
          onDismiss={dismiss}
          onConfirm={() => update("/onboarding/ready", {})}
        />
      )}
    </ParticipantContext.Provider>
  );
}

function AttendancePrompt({
  lateOnly,
  busy,
  error,
  onDismiss,
  onConfirm,
}: {
  lateOnly: boolean;
  busy: boolean;
  error: string;
  onDismiss: () => void;
  onConfirm: () => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    titleId = useId(),
    descriptionId = useId();
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.showModal();
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog attendance-prompt"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onDismiss();
      }}
    >
      <p className="eyebrow">
        {lateOnly ? "Late attendance check-in" : "Attendance check-in"}
      </p>
      <h2 id={titleId}>Are you here and ready to quest?</h2>
      <p id={descriptionId}>
        {lateOnly
          ? "Confirm that you're at the event and taking part. An organizer must review your team placement before you can play."
          : "Confirm that you're at the event and taking part. Your organizers will use attendance to prepare the teams."}
      </p>
      <p className="meta">This confirmation starts no competition clock</p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="actions">
        <button
          className="button secondary"
          autoFocus
          disabled={busy}
          onClick={onDismiss}
        >
          Not yet
        </button>
        <button
          className="button"
          disabled={busy}
          onClick={() => void onConfirm().catch(() => {})}
        >
          {busy ? "Confirming…" : "I'm ready to quest"}
        </button>
      </div>
    </dialog>
  );
}
