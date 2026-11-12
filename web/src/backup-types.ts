export interface BackupVersions {
  ctfd_version: string;
  plugin_version: string;
  schema_revision: string;
  ctfd_schema_revision: string;
  image_digest: string;
}
export interface BackupTarget {
  id: string;
  name: string;
  origin: string;
  current: BackupVersions;
}
export type BackupCounts = Record<string, number>;
export interface BackupSnapshot {
  id: string;
  created_at: string;
  origin: string;
  bytes: number;
  sha256: string;
  source: BackupVersions;
  counts: BackupCounts;
  compatibility: {
    supported: boolean;
    reason: string | null;
    checked?: boolean;
  };
}
export interface BackupJob {
  id: string;
  operation_id?: string;
  kind: "backup" | "restore" | "upload";
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  message: string;
  created_at: string;
  updated_at: string;
  safety_snapshot_id?: string;
  requires_login?: boolean;
}
export interface BackupTicket {
  status_url: string;
  status_token: string;
}
export interface BackupOperation {
  job: BackupJob;
  ticket: BackupTicket;
}
export interface BackupIndex {
  worker_status_origin: string | null;
  available: boolean;
  unavailable_reason: string | null;
  target: BackupTarget | null;
  schedule: {
    enabled: boolean;
    interval_seconds: number;
    last_success_at: string | null;
    next_run_at: string | null;
    last_job?: BackupJob | null;
  } | null;
  snapshots: BackupSnapshot[];
  active_job?: BackupOperation | null;
}
export interface RestoreReview {
  review_token: string;
  expires_at: string;
  confirmation_text: string;
  target: BackupTarget;
  snapshot: BackupSnapshot;
  comparison: {
    current_counts: BackupCounts;
    snapshot_counts: BackupCounts;
  };
  warnings: string[];
  safety_backup_required: true;
  full_replace: true;
}

export interface BackupDayGroup {
  key: string;
  count: number;
}
export interface BackupMonthGroup {
  key: string;
  label: string;
  count: number;
  days: BackupDayGroup[];
}
export interface BackupOverview extends Omit<
  BackupIndex,
  "target" | "snapshots"
> {
  target: Pick<BackupTarget, "id" | "name" | "origin"> | null;
  catalog_revision: string;
  groups: BackupMonthGroup[];
}
export interface BackupDayPage {
  day: string;
  snapshots: BackupSnapshot[];
  next_cursor: string | null;
  total: number;
  catalog_revision: string;
}
