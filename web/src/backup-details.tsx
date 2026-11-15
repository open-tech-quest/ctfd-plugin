import type { BackupVersions, RestoreReview } from "./backup-types";
import { backupVersionLabel } from "./backup-presentation";

export function RestoreInstallationDetails({
  review,
}: {
  review: Pick<RestoreReview, "target" | "snapshot">;
}) {
  const source = review.snapshot.instance;
  return (
    <dl className="backup-version-details" aria-label="Restore installations">
      <div>
        <dt>Backup source</dt>
        <dd>
          {source ? (
            <>
              <strong>{source.name}</strong>
              <br />
              <code>{source.id}</code>
            </>
          ) : (
            "Not recorded"
          )}
        </dd>
      </div>
      <div>
        <dt>Restore destination</dt>
        <dd>
          <strong>{review.target.name}</strong>
          <br />
          <code>{review.target.id}</code>
        </dd>
      </div>
      <div>
        <dt>Destination URL</dt>
        <dd>{review.target.origin}</dd>
      </div>
    </dl>
  );
}

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
