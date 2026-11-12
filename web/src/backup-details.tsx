import type { BackupVersions } from "./backup-types";
import { backupVersionLabel } from "./backup-presentation";

export function VersionDetails({ version }: { version: BackupVersions }) {
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
