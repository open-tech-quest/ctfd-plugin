import { useEffect, useRef, useState } from "react";
import { ApiError, dateTime, request } from "./api";
import { useResource } from "./hooks";
import { ConfirmDialog, Empty, Notice } from "./ui";
import type {
  BackupIndex,
  BackupSnapshot,
  BackupVersions,
  RestoreReview,
} from "./backup-types";
import {
  backupDownloadPath,
  createBackup,
  restoreBackup,
  reviewBackupRestore,
  uploadBackup,
} from "./backup-transport";
import { backupJobActive } from "./backup-progress";
import {
  backupChecksumLabel,
  backupCountsKnown,
  backupOriginLabel,
  backupRestoreSupported,
  backupSizeLabel,
  backupVersionLabel,
} from "./backup-presentation";
import {
  backupReasonValid,
  restoreConfirmationValid,
} from "./backup-confirmation";
import {
  BackupJobProgress,
  backupProgress,
  useBackupProgress,
} from "./backup-runtime";

export function BackupRestoreIntroduction() {
  return (
    <div className="section-heading">
      <div>
        <h2>Backup &amp; Restore</h2>
        <p>
          Full signed backups contain the database, uploaded files and instance
          configuration, including accounts and challenge answers. Keep
          downloaded backups private. Event JSON reports are separate and cannot
          restore the instance.
        </p>
      </div>
    </div>
  );
}

export function RestoreReplacementNotice() {
  return (
    <Notice>
      Restore replaces all current data, challenge answers, uploaded files and
      event settings. Accounts, roles, passwords and API tokens return to their
      backup state. A safety backup must complete before replacement starts. The
      site will be temporarily unavailable; afterward, sign in using an account
      and password from the restored backup.
    </Notice>
  );
}

function VersionDetails({ version }: { version: BackupVersions }) {
  return (
    <dl className="backup-version-details">
      <div>
        <dt>CTFd</dt>
        <dd>{backupVersionLabel(version.ctfd_version)}</dd>
      </div>
      <div>
        <dt>Quest</dt>
        <dd>{backupVersionLabel(version.plugin_version)}</dd>
      </div>
      <div>
        <dt>CTFd schema</dt>
        <dd>{backupVersionLabel(version.ctfd_schema_revision)}</dd>
      </div>
      <div>
        <dt>Quest schema</dt>
        <dd>{backupVersionLabel(version.schema_revision)}</dd>
      </div>
      <div>
        <dt>Image</dt>
        <dd>{version.image_digest || "Not recorded"}</dd>
      </div>
    </dl>
  );
}
function RestoreComparison({ review }: { review: RestoreReview }) {
  const counts = [
    ...new Set([
      ...Object.keys(review.comparison.current_counts),
      ...Object.keys(review.comparison.snapshot_counts),
    ]),
  ].sort();
  return (
    <>
      <RestoreReplacementNotice />
      <p>
        Restore the backup from {dateTime(review.snapshot.created_at)} to{" "}
        <strong>{review.target.name}</strong>.
      </p>
      <div
        className="backup-comparison"
        role="region"
        aria-label="Restore comparison"
        tabIndex={0}
      >
        <table>
          <thead>
            <tr>
              <th>Data</th>
              <th>Current</th>
              <th>Backup</th>
            </tr>
          </thead>
          <tbody>
            {counts.map((key) => (
              <tr key={key}>
                <th scope="row">{key.replaceAll("_", " ")}</th>
                <td>
                  {review.comparison.current_counts[key]?.toLocaleString() ??
                    "Not recorded"}
                </td>
                <td>
                  {review.comparison.snapshot_counts[key]?.toLocaleString() ??
                    "Not recorded"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>Software and database versions</summary>
        <h3>Current instance</h3>
        <VersionDetails version={review.target.current} />
        <h3>Backup</h3>
        <VersionDetails version={review.snapshot.source} />
      </details>
      {review.warnings.map((warning, index) => (
        <Notice key={index}>{warning}</Notice>
      ))}
    </>
  );
}
function ReasonField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>Reason</span>
      <textarea
        rows={3}
        minLength={3}
        maxLength={1000}
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <small>
        Use 3–1,000 characters. Keep passwords, answers and other secrets out of
        the reason.
      </small>
    </label>
  );
}

export function BackupRestore() {
  const resource = useResource<BackupIndex>("/admin/backups", 15000);
  const progress = useBackupProgress();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [action, setAction] = useState<{
    kind: "backup" | "upload";
    operationId: string;
  } | null>(null);
  const [reason, setReason] = useState("");
  const [review, setReview] = useState<{
    data: RestoreReview;
    operationId: string;
  } | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const reviewOrigin = useRef<HTMLButtonElement | null>(null);
  const data = resource.data;
  const active =
    !!progress?.pendingRestoreId ||
    !!(progress?.operation && backupJobActive(progress.operation));
  useEffect(() => {
    if (data?.active_job && data.worker_status_origin)
      backupProgress.recover(data.active_job, data.worker_status_origin);
  }, [data]);
  useEffect(() => {
    if (data && !data.available) setFile(null);
  }, [data?.available]);
  const terminal = progress?.operation?.job.status;
  useEffect(() => {
    if (terminal === "succeeded" || terminal === "failed")
      void resource.refresh();
  }, [terminal]);
  async function inspect(snapshot: BackupSnapshot) {
    if (!backupRestoreSupported(snapshot)) {
      setError(
        snapshot.compatibility.reason ||
          "This legacy or unsupported backup needs operator verification before restore.",
      );
      return;
    }
    setBusy(true);
    setError("");
    setReason("");
    setConfirmation("");
    try {
      const result = await reviewBackupRestore(request, snapshot.id);
      if (
        !backupRestoreSupported(result.snapshot) ||
        result.safety_backup_required !== true ||
        result.full_replace !== true ||
        !result.confirmation_text
      )
        throw new Error(
          result.snapshot.compatibility.reason ||
            "This backup needs operator review before it can be restored.",
        );
      setReview({ data: result, operationId: crypto.randomUUID() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const reasonValid = backupReasonValid(reason);
  if (!data)
    return (
      <section className="admin-panel">
        <BackupRestoreIntroduction />
        {resource.error ? (
          <Notice tone="error" retry={() => void resource.refresh()}>
            {resource.error}
          </Notice>
        ) : (
          <p role="status">Loading backup service…</p>
        )}
        {progress && <BackupJobProgress state={progress} />}
      </section>
    );
  if (
    !data.available ||
    !data.target ||
    !data.schedule ||
    !data.worker_status_origin
  )
    return (
      <section className="admin-panel">
        <BackupRestoreIntroduction />
        <Notice>
          {data.unavailable_reason ||
            "Backup & Restore is not configured. Ask an operator to connect the backup service."}
        </Notice>
        {progress && <BackupJobProgress state={progress} />}
        <button
          type="button"
          className="button secondary"
          onClick={() => void resource.refresh()}
        >
          Check availability
        </button>
      </section>
    );
  const origin = data.worker_status_origin;
  const disabled = busy || active;
  return (
    <div className="backup-workspace">
      <section className="admin-panel">
        <BackupRestoreIntroduction />
        <p>
          Instance: <strong>{data.target.name}</strong>{" "}
          <span className="meta">({data.target.id})</span>
        </p>
        {(error || resource.error) && (
          <Notice tone="error">{error || resource.error}</Notice>
        )}
        <dl className="backup-version-details">
          <div>
            <dt>Scheduled backups</dt>
            <dd>
              {data.schedule.enabled
                ? `Every ${data.schedule.interval_seconds.toLocaleString()} seconds`
                : "Disabled"}
            </dd>
          </div>
          <div>
            <dt>Last successful backup</dt>
            <dd>{dateTime(data.schedule.last_success_at)}</dd>
          </div>
          <div>
            <dt>Next scheduled backup</dt>
            <dd>
              {data.schedule.enabled
                ? dateTime(data.schedule.next_run_at)
                : "Not scheduled"}
            </dd>
          </div>
          {data.schedule.last_job && (
            <div>
              <dt>Last scheduled attempt</dt>
              <dd>
                {data.schedule.last_job.status}:{" "}
                {data.schedule.last_job.message}
              </dd>
            </div>
          )}
        </dl>
        <div className="actions">
          <button
            type="button"
            className="button"
            disabled={disabled}
            onClick={() => {
              setReason("");
              setAction({ kind: "backup", operationId: crypto.randomUUID() });
            }}
          >
            Create backup
          </button>
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => void resource.refresh()}
          >
            Refresh backup status
          </button>
        </div>
      </section>
      {progress && <BackupJobProgress state={progress} />}
      <section className="admin-panel">
        <h2>Upload a signed backup</h2>
        <p>
          Upload the original full backup file. The service verifies its
          signature and compatibility before offering restore. Event JSON
          reports cannot be restored here.
        </p>
        <label className="field">
          <span>Full backup file</span>
          <input
            ref={fileInput}
            type="file"
            disabled={disabled}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button
          type="button"
          className="button secondary"
          disabled={disabled || !file}
          onClick={() => {
            setReason("");
            setAction({ kind: "upload", operationId: crypto.randomUUID() });
          }}
        >
          Upload backup
        </button>
      </section>
      <section className="admin-panel">
        <h2>Saved backups</h2>
        {!data.snapshots.length ? (
          <Empty title="No saved backups">
            Create a backup or upload a full signed backup to review it here.
          </Empty>
        ) : (
          <ul className="backup-snapshots">
            {data.snapshots.map((snapshot) => (
              <li key={snapshot.id}>
                <div>
                  <h3>{dateTime(snapshot.created_at)}</h3>
                  <p className="meta">
                    {backupOriginLabel(snapshot.origin)} ·{" "}
                    {backupSizeLabel(snapshot)}
                  </p>
                  <p className="meta">
                    CTFd {backupVersionLabel(snapshot.source.ctfd_version)} ·
                    Quest {backupVersionLabel(snapshot.source.plugin_version)} ·
                    Schema {backupVersionLabel(snapshot.source.schema_revision)}
                  </p>
                  <details>
                    <summary>Versions and recorded metadata</summary>
                    <VersionDetails version={snapshot.source} />
                    <p className="backup-digest">
                      {backupChecksumLabel(snapshot.sha256)}
                    </p>
                    {backupCountsKnown(snapshot) ? (
                      <dl className="backup-version-details">
                        {Object.entries(snapshot.counts).map(([key, value]) => (
                          <div key={key}>
                            <dt>{key.replaceAll("_", " ")}</dt>
                            <dd>{value.toLocaleString()}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <p>
                        {snapshot.origin === "legacy"
                          ? "Event counts unknown; inspect the downloaded capture with an operator."
                          : "Event counts not recorded."}
                      </p>
                    )}
                  </details>
                  <p>
                    {backupRestoreSupported(snapshot)
                      ? "Compatible with this instance"
                      : `Operator-only restore: ${snapshot.compatibility.reason || "Unsupported source version or backup format."}`}
                  </p>
                </div>
                <div className="actions">
                  <a
                    className="button secondary"
                    href={backupDownloadPath(snapshot.id)}
                    download
                  >
                    {snapshot.origin === "legacy"
                      ? "Download for inspection"
                      : "Download backup"}
                  </a>
                  <button
                    type="button"
                    className="button secondary"
                    disabled={disabled || !backupRestoreSupported(snapshot)}
                    onClick={(event) => {
                      reviewOrigin.current = event.currentTarget;
                      void inspect(snapshot);
                    }}
                  >
                    Review restore
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      {action && (
        <ConfirmDialog
          label={action.kind === "backup" ? "Create backup" : "Upload backup"}
          description={
            action.kind === "backup"
              ? "Create a full signed backup of this instance's database, uploaded files and configuration."
              : `Upload “${file?.name ?? "the selected backup"}” for server-side verification. This does not restore or change current event data.`
          }
          disabled={!reasonValid || active}
          onClose={() => setAction(null)}
          onConfirm={async () => {
            setError("");
            try {
              const result =
                action.kind === "backup"
                  ? await createBackup(request, reason, action.operationId)
                  : await uploadBackup(
                      file!,
                      reason,
                      action.operationId,
                      window.QUEST_INIT?.csrfNonce ?? "",
                    );
              backupProgress.begin(result, origin);
              setAction(null);
              setFile(null);
              if (fileInput.current) fileInput.current.value = "";
              void resource.refresh();
            } catch (e) {
              setError((e as Error).message);
              void resource.refresh();
              throw e;
            }
          }}
        >
          <ReasonField value={reason} onChange={setReason} />
        </ConfirmDialog>
      )}
      {review && (
        <ConfirmDialog
          label="Restore full instance"
          description="This replaces the entire instance. Review the comparison and confirm the exact target below."
          disabled={
            !restoreConfirmationValid(review.data, confirmation, reason) ||
            active
          }
          onClose={() => setReview(null)}
          focusFallback={() => {
            const origin = reviewOrigin.current;
            return origin?.isConnected && !origin.disabled ? origin : null;
          }}
          onConfirm={async () => {
            if (!restoreConfirmationValid(review.data, confirmation, reason))
              throw new Error(
                "This restore review expired. Close it and review the backup again before confirming.",
              );
            setError("");
            try {
              const result = await restoreBackup(
                request,
                review.data.snapshot.id,
                review.data.review_token,
                reason,
                confirmation,
                review.operationId,
              );
              backupProgress.begin(result, origin);
            } catch (e) {
              const status = e instanceof ApiError ? e.status : 0;
              if (!(status >= 400 && status < 500 && status !== 408))
                backupProgress.unconfirmedRestore(review.operationId, origin);
              else setError((e as Error).message);
              throw e;
            }
          }}
        >
          <RestoreComparison review={review.data} />
          <p className="meta">
            Review expires {dateTime(review.data.expires_at)}. Keep this page
            open while restore is running.
          </p>
          <label className="field">
            <span>Type this exact confirmation</span>
            <code className="backup-confirmation">
              {review.data.confirmation_text}
            </code>
            <textarea
              rows={2}
              autoComplete="off"
              spellCheck={false}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <ReasonField value={reason} onChange={setReason} />
        </ConfirmDialog>
      )}
    </div>
  );
}
