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

/** An unchecked catalog row can request a review, never confirm a restore. */
export function backupReviewAvailable(
  snapshot: Pick<BackupSnapshot, "origin" | "compatibility">,
) {
  return (
    snapshot.origin !== "legacy" &&
    (snapshot.compatibility.checked === false ||
      backupRestoreSupported(snapshot))
  );
}
export function backupIntervalLabel(seconds: number) {
  for (const [unit, size] of [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ] as const) {
    if (seconds >= size && seconds % size === 0) {
      const count = seconds / size;
      return `Every ${count.toLocaleString()} ${unit}${count === 1 ? "" : "s"}`;
    }
  }
  return `Every ${seconds.toLocaleString()} seconds`;
}
export function backupDayLabel(day: string) {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(day + "T00:00:00Z"));
}
export function backupTimeLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}
