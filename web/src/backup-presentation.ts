import type { BackupSnapshot } from "./backup-types";

export function backupOriginLabel(origin: string) {
  return origin === "legacy" ? "Legacy Restic backup" : origin;
}
export function backupSizeLabel(
  snapshot: Pick<BackupSnapshot, "origin" | "bytes">,
) {
  const value = snapshot.bytes;
  if (snapshot.origin === "legacy" && value === 0) return "Size unknown";
  if (value < 1024) return `${value.toLocaleString()} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}
export function backupVersionLabel(value: string) {
  return !value || value === "unknown" || value === "legacy"
    ? "Unknown"
    : value;
}
export function backupChecksumLabel(sha256: string) {
  return sha256 ? `SHA-256: ${sha256}` : "Checksum not recorded.";
}
export function backupCountsKnown(snapshot: Pick<BackupSnapshot, "counts">) {
  return Object.keys(snapshot.counts).length > 0;
}
export function backupRestoreSupported(
  snapshot: Pick<BackupSnapshot, "origin" | "compatibility">,
) {
  return snapshot.origin !== "legacy" && snapshot.compatibility.supported;
}
