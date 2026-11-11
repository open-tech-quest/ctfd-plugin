import { useState, useSyncExternalStore } from "react";
import { dateTime, request } from "./api";
import { Notice } from "./ui";
import {
  createBackupProgress,
  backupJobActive,
  type BackupProgressState,
} from "./backup-progress";
import { getBackupJob } from "./backup-transport";

export const backupProgress = createBackupProgress({
  active: () => !document.hidden && navigator.onLine !== false,
  watchActivity: (refresh) => {
    window.addEventListener("online", refresh);
    window.addEventListener("offline", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("online", refresh);
      window.removeEventListener("offline", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  },
});
export function useBackupProgress() {
  return useSyncExternalStore(
    backupProgress.subscribe,
    backupProgress.getSnapshot,
    () => null,
  );
}

export function BackupJobProgress({ state }: { state: BackupProgressState }) {
  const [retryError, setRetryError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const operation = state.operation;
  const job = operation?.job;
  async function retry() {
    setRetryError("");
    setRetrying(true);
    try {
      if (state.pendingRestoreId) {
        const found = await getBackupJob(request, state.pendingRestoreId);
        backupProgress.begin(found, state.workerOrigin);
      } else await backupProgress.refresh();
    } catch {
      setRetryError(
        "Status is still unavailable. The operation may be running. Do not submit another restore; contact an operator if the site does not recover.",
      );
    } finally {
      setRetrying(false);
    }
  }
  return (
    <section
      className="admin-panel backup-job"
      aria-label="Backup operation status"
    >
      <h2>
        {job?.status === "failed"
          ? "Backup operation failed"
          : job?.status === "succeeded"
            ? "Backup operation complete"
            : job?.kind === "backup"
              ? "Creating backup"
              : job?.kind === "upload"
                ? "Uploading backup"
                : "Restore status"}
      </h2>
      <p role="status" aria-live="polite">
        {job
          ? `${job.status}: ${job.message}`
          : "Waiting for confirmed restore status"}
      </p>
      {job && (
        <dl className="backup-version-details">
          <div>
            <dt>Stage</dt>
            <dd>{job.stage.replaceAll("_", " ")}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{dateTime(job.updated_at)}</dd>
          </div>
          {job.safety_snapshot_id && (
            <div>
              <dt>Safety backup</dt>
              <dd>{job.safety_snapshot_id}</dd>
            </div>
          )}
        </dl>
      )}
      {(retryError || state.error) && (
        <Notice tone="error">{retryError || state.error}</Notice>
      )}
      {(state.error || (operation && backupJobActive(operation))) && (
        <button
          type="button"
          className="button secondary"
          disabled={retrying}
          onClick={() => void retry()}
        >
          {retrying ? "Checking status…" : "Check status"}
        </button>
      )}
      <details>
        <summary>Operation reference</summary>
        <p className="meta">
          {job?.operation_id ?? job?.id ?? state.pendingRestoreId}
        </p>
      </details>
    </section>
  );
}

export function RestoreProgressScreen({
  state,
}: {
  state: BackupProgressState;
}) {
  const job = state.operation?.job;
  const terminal = job?.status === "succeeded" || job?.status === "failed";
  return (
    <main id="main" className="app-main backup-restore-progress">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Organizer workspace</p>
          <h1>Restoring the instance</h1>
        </div>
      </div>
      <Notice>
        Keep this page open. The website and your current session can become
        unavailable while the worker replaces the database, uploaded files and
        configuration. Status is read separately from the backup service.
      </Notice>
      <BackupJobProgress state={state} />
      {job?.status === "succeeded" && (
        <Notice tone="success">
          Restore completed. Sign in with an account and password from the
          restored backup.
        </Notice>
      )}
      {job?.status === "failed" && (
        <Notice tone="error">
          Restore did not complete successfully. Review the worker message and
          safety backup with an operator before attempting another restore.
        </Notice>
      )}
      {(terminal || state.pendingRestoreId) && (
        <a
          className="button"
          href="/login?next=%2Fquest%2Fadmin%3Fsection%3Dbackups"
          target={state.pendingRestoreId ? "_blank" : undefined}
          rel={state.pendingRestoreId ? "noopener" : undefined}
        >
          {state.pendingRestoreId
            ? "Sign in in another tab"
            : "Sign in to the restored instance"}
        </a>
      )}
      {job?.status === "failed" && !job.requires_login && (
        <button
          type="button"
          className="text-button"
          onClick={() => backupProgress.clear()}
        >
          Return to organizer controls
        </button>
      )}
    </main>
  );
}
