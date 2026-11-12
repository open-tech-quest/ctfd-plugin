import { useEffect, useRef, useState } from "react";
import { dateTime, request } from "./api";
import { Empty, Notice } from "./ui";
import type {
  BackupOverview,
  BackupSnapshot,
  BackupCurrentVersions,
} from "./backup-types";
import { backupDownloadPath, getBackupDay } from "./backup-transport";
import { createBackupDayLoader, type BackupDayState } from "./backup-catalog";
import { VersionDetails } from "./backup-details";
import {
  backupChecksumLabel,
  backupCountsKnown,
  backupOriginLabel,
  backupRestoreSupported,
  backupReviewAvailable,
  backupSizeLabel,
  backupDayLabel,
  backupTimeLabel,
  backupIntervalLabel,
  backupSchemaComparison,
  backupVersionLabel,
} from "./backup-presentation";

export function BackupSchedule({ data }: { data: BackupOverview }) {
  const schedule = data.schedule!;
  return (
    <div className="backup-protection">
      <div className="backup-protection-heading">
        <h3>{schedule.enabled ? "Automatic backups" : "On-demand backups"}</h3>
        <span className="meta">
          {schedule.enabled
            ? backupIntervalLabel(schedule.interval_seconds)
            : "Create a backup when needed"}
        </span>
      </div>
      <p className="meta">
        Your hosting operator configures the schedule and backup storage outside
        Organize. This page reports their status and lets you create an extra
        manual backup.{" "}
        <a href="https://github.com/open-tech-quest/ctfd-plugin/blob/main/docs/backups.md#hosting-the-worker">
          Backup setup guide
        </a>
      </p>
      {schedule.enabled && (
        <dl className="backup-schedule-times">
          <div>
            <dt>Last successful automatic backup</dt>
            <dd>
              {schedule.last_success_at
                ? dateTime(schedule.last_success_at)
                : "No successful backup recorded"}
            </dd>
          </div>
          {schedule.enabled && (
            <div>
              <dt>Next scheduled backup</dt>
              <dd>
                {schedule.next_run_at
                  ? dateTime(schedule.next_run_at)
                  : "Not reported by the operator"}
              </dd>
            </div>
          )}
        </dl>
      )}
      {schedule.last_job?.status === "failed" && (
        <Notice tone="error">
          The last automatic backup failed. {schedule.last_job.message} Contact
          your hosting operator to check the schedule.
        </Notice>
      )}
      <details className="backup-service-details">
        <summary>Schedule and instance details</summary>
        <dl className="backup-version-details">
          <div>
            <dt>Instance</dt>
            <dd>{data.target!.name}</dd>
          </div>
          <div>
            <dt>Instance ID</dt>
            <dd>{data.target!.id}</dd>
          </div>
          {schedule.last_job && (
            <>
              <div>
                <dt>Last scheduled attempt</dt>
                <dd>
                  {schedule.last_job.status}: {schedule.last_job.message}
                </dd>
              </div>
              <div>
                <dt>Updated</dt>
                <dd>{dateTime(schedule.last_job.updated_at)}</dd>
              </div>
            </>
          )}
        </dl>
      </details>
    </div>
  );
}

function SnapshotRow({
  snapshot,
  disabled,
  inspect,
  current,
}: {
  snapshot: BackupSnapshot;
  current: BackupCurrentVersions | null | undefined;
  disabled: boolean;
  inspect: (snapshot: BackupSnapshot, origin: HTMLButtonElement) => void;
}) {
  const comparison = backupSchemaComparison(snapshot.source, current);
  return (
    <li>
      <div>
        <h5>{backupTimeLabel(snapshot.created_at)}</h5>
        <p className="meta">
          {backupOriginLabel(snapshot.origin)} · {backupSizeLabel(snapshot)}
        </p>
        <div
          className="backup-schema-pairs"
          aria-label="Current and backup schemas"
        >
          <span>Schema</span>
          <span>Current</span>
          <span>Backup</span>
          <strong>Quest</strong>
          <span>{backupVersionLabel(current?.schema_revision ?? "")}</span>
          <span>{backupVersionLabel(snapshot.source.schema_revision)}</span>
          <strong>CTFd</strong>
          <span>{backupVersionLabel(current?.ctfd_schema_revision ?? "")}</span>
          <span>
            {backupVersionLabel(snapshot.source.ctfd_schema_revision)}
          </span>
        </div>
        <p
          className={`backup-schema-status meta${comparison === "mismatch" ? " mismatch" : ""}`}
        >
          {comparison === "match"
            ? "Schemas match; full compatibility is checked on review."
            : comparison === "mismatch"
              ? "Schema mismatch; operator review is required."
              : "Schema unknown; compatibility is checked on review."}
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
        <p className="backup-compatibility meta">
          {snapshot.compatibility.checked === false &&
          snapshot.origin !== "legacy"
            ? "Compatibility is checked during restore review."
            : backupRestoreSupported(snapshot)
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
          disabled={disabled || !backupReviewAvailable(snapshot)}
          onClick={(event) => inspect(snapshot, event.currentTarget)}
        >
          Review restore
        </button>
      </div>
    </li>
  );
}

export function BackupHistory({
  data,
  disabled,
  inspect,
  refreshSequence,
}: {
  data: BackupOverview;
  disabled: boolean;
  inspect: (snapshot: BackupSnapshot, origin: HTMLButtonElement) => void;
  refreshSequence: number;
}) {
  const [month, setMonth] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [state, setState] = useState<BackupDayState>({
    day: null,
    page: null,
    loading: false,
    error: "",
    notice: "",
  });
  const initialized = useRef(false);
  const loader = useRef<ReturnType<typeof createBackupDayLoader> | null>(null);
  if (!loader.current)
    loader.current = createBackupDayLoader(
      (selected, before, signal) =>
        getBackupDay(
          request,
          selected,
          before,
          AbortSignal.any([signal, AbortSignal.timeout(30000)]),
        ),
      setState,
    );
  const previousSelection = useRef({
    day: null as string | null,
    revision: "",
    refresh: refreshSequence,
  });
  const pageStatus = useRef<HTMLParagraphElement>(null);
  const focusAfterMore = useRef(false);
  const groups = data.groups;
  useEffect(() => {
    if (!initialized.current && groups.length) {
      initialized.current = true;
      setMonth(groups[0].key);
      setDay(groups[0].days[0]?.key ?? null);
    }
  }, [groups]);
  const selected =
    groups
      .find((group) => group.key === month)
      ?.days.find((item) => item.key === day) ?? null;
  useEffect(() => {
    const key = selected?.key ?? null;
    const revision = selected ? `${selected.key}:${selected.count}` : "";
    const previous = previousSelection.current;
    loader.current!.open(key, revision);
    if (
      previous.day === key &&
      previous.revision === revision &&
      previous.refresh !== refreshSequence
    )
      loader.current!.refresh();
    previousSelection.current = {
      day: key,
      revision,
      refresh: refreshSequence,
    };
  }, [selected?.key, selected?.count, refreshSequence]);
  useEffect(() => {
    if (!state.loading && focusAfterMore.current) {
      focusAfterMore.current = false;
      if (
        !state.error &&
        state.page &&
        !state.page.next_cursor &&
        state.day === selected?.key
      )
        pageStatus.current?.focus();
    }
  }, [state]);
  useEffect(() => () => loader.current!.close(), []);
  return (
    <section className="admin-panel backup-history">
      <div className="section-heading">
        <div>
          <h2>Saved backups</h2>
          <p className="meta">
            Grouped by month and day in UTC. Open a day to browse its backups.
          </p>
          <p className="meta backup-current-schemas">
            Current schemas: Quest{" "}
            {backupVersionLabel(data.current_versions?.schema_revision ?? "")} ·
            CTFd{" "}
            {backupVersionLabel(
              data.current_versions?.ctfd_schema_revision ?? "",
            )}
          </p>
        </div>
        {!!groups.length && (
          <span className="meta">
            {groups
              .reduce((count, group) => count + group.count, 0)
              .toLocaleString()}{" "}
            backups
          </span>
        )}
      </div>
      {!groups.length ? (
        <Empty title="No saved backups">
          Create a backup or upload a full signed backup to review it here.
        </Empty>
      ) : (
        <div className="backup-months">
          {groups.map((group) => (
            <div className="backup-month" key={group.key}>
              <h3>
                <button
                  type="button"
                  className="backup-group-toggle"
                  aria-expanded={month === group.key}
                  aria-controls={`backup-month-${group.key}`}
                  onClick={() => {
                    focusAfterMore.current = false;
                    setMonth(month === group.key ? null : group.key);
                    setDay(null);
                  }}
                >
                  <span className="backup-group-chevron" aria-hidden="true">
                    {month === group.key ? "−" : "+"}
                  </span>
                  <span>{group.label}</span>
                  <span className="backup-group-count">
                    {group.count.toLocaleString()} backups
                  </span>
                </button>
              </h3>
              <div
                id={`backup-month-${group.key}`}
                className="backup-days"
                hidden={month !== group.key}
              >
                {month === group.key &&
                  group.days.map((item) => (
                    <div className="backup-day" key={item.key}>
                      <h4>
                        <button
                          type="button"
                          className="backup-group-toggle"
                          aria-expanded={selected?.key === item.key}
                          aria-controls={`backup-day-${item.key}`}
                          onClick={() => {
                            focusAfterMore.current = false;
                            setDay(day === item.key ? null : item.key);
                          }}
                        >
                          <span
                            className="backup-group-chevron"
                            aria-hidden="true"
                          >
                            {selected?.key === item.key ? "−" : "+"}
                          </span>
                          <span>{backupDayLabel(item.key)}</span>
                          <span className="backup-group-count">
                            {item.count.toLocaleString()} backups
                          </span>
                        </button>
                      </h4>
                      <div
                        id={`backup-day-${item.key}`}
                        className="backup-day-content"
                        hidden={selected?.key !== item.key}
                        aria-busy={selected?.key === item.key && state.loading}
                      >
                        {selected?.key === item.key && (
                          <>
                            {state.notice && (
                              <p role="status" className="meta">
                                {state.notice}
                              </p>
                            )}
                            {state.error && (
                              <Notice
                                tone="error"
                                retry={() => void loader.current!.retry()}
                              >
                                {state.error}
                              </Notice>
                            )}
                            {state.page && state.day === selected.key && (
                              <>
                                {!state.page.snapshots.length && (
                                  <p>
                                    No backups remain for this day. Refresh
                                    backup status to update the history.
                                  </p>
                                )}
                                <ul className="backup-snapshots">
                                  {state.page.snapshots.map((snapshot) => (
                                    <SnapshotRow
                                      key={snapshot.id}
                                      snapshot={snapshot}
                                      current={data.current_versions}
                                      disabled={disabled}
                                      inspect={inspect}
                                    />
                                  ))}
                                </ul>
                                <div className="backup-page-controls">
                                  <p
                                    className="meta"
                                    ref={pageStatus}
                                    tabIndex={-1}
                                  >
                                    Showing{" "}
                                    {state.page.snapshots.length.toLocaleString()}{" "}
                                    of {state.page.total.toLocaleString()}{" "}
                                    backups for this day
                                  </p>
                                  {state.page.next_cursor && (
                                    <button
                                      type="button"
                                      className="button secondary"
                                      disabled={state.loading}
                                      onClick={() => {
                                        focusAfterMore.current = true;
                                        void loader.current!.more();
                                      }}
                                    >
                                      Load more backups
                                    </button>
                                  )}
                                </div>
                              </>
                            )}
                            {state.loading && (
                              <p role="status">Loading backups…</p>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
