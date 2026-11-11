import type { request } from "./api";
import type { BackupOperation, BackupJob, RestoreReview } from "./backup-types";

const base = "/admin/backups";
export function backupDownloadPath(id: string) {
  return `/api/v1/quest${base}/${encodeURIComponent(id)}/download`;
}
export function createBackup(
  send: typeof request,
  reason: string,
  operationId: string,
) {
  return send<BackupOperation>(base, "POST", {
    reason: reason.trim(),
    operation_id: operationId,
  });
}
export function reviewBackupRestore(send: typeof request, id: string) {
  return send<RestoreReview>(
    `${base}/${encodeURIComponent(id)}/review`,
    "POST",
    {},
  );
}
export function restoreBackup(
  send: typeof request,
  id: string,
  reviewToken: string,
  reason: string,
  confirmation: string,
  operationId: string,
) {
  return send<BackupOperation>(
    `${base}/${encodeURIComponent(id)}/restore`,
    "POST",
    {
      review_token: reviewToken,
      reason: reason.trim(),
      confirmation,
      operation_id: operationId,
    },
  );
}
export function getBackupJob(send: typeof request, id: string) {
  return send<BackupOperation>(`${base}/jobs/${encodeURIComponent(id)}`);
}
export async function uploadBackup(
  file: File,
  reason: string,
  operationId: string,
  nonce: string,
  send: typeof fetch = fetch,
) {
  const body = new FormData();
  body.set("file", file);
  body.set("reason", reason.trim());
  body.set("operation_id", operationId);
  body.set("nonce", nonce);
  let response: Response;
  try {
    response = await send(`/api/v1/quest${base}/upload`, {
      method: "POST",
      credentials: "same-origin",
      body,
      headers: { Accept: "application/json", "CSRF-Token": nonce },
    });
  } catch {
    throw new Error(
      "The upload was not confirmed. Refresh backup status before trying again.",
    );
  }
  let envelope: { success?: boolean; data?: BackupOperation; error?: string };
  try {
    envelope = await response.json();
  } catch {
    throw new Error(
      "The upload returned an unexpected response. Refresh backup status before trying again.",
    );
  }
  if (!response.ok || envelope.success === false || !envelope.data)
    throw new Error(
      typeof envelope.error === "string"
        ? envelope.error
        : "The backup could not be uploaded.",
    );
  return envelope.data;
}

export function workerStatusUrl(
  configuredOrigin: string,
  operation: BackupOperation,
) {
  const origin = new URL(configuredOrigin);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(
    origin.hostname,
  );
  if (
    (origin.protocol !== "https:" &&
      !(origin.protocol === "http:" && loopback)) ||
    origin.username ||
    origin.password ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  )
    throw new Error(
      "The configured backup status service is invalid. Ask an operator to review its configuration.",
    );
  const url = new URL(operation.ticket.status_url);
  if (
    url.origin !== origin.origin ||
    url.pathname !==
      `/v1/jobs/${encodeURIComponent(operation.job.id)}/status` ||
    url.search ||
    url.hash ||
    url.username ||
    url.password
  )
    throw new Error(
      "The backup status address does not match the configured service. Ask an operator to review it.",
    );
  return url.href;
}
export async function readBackupStatus(
  configuredOrigin: string,
  operation: BackupOperation,
  signal?: AbortSignal,
  send: typeof fetch = fetch,
): Promise<BackupJob> {
  const url = workerStatusUrl(configuredOrigin, operation);
  let response: Response;
  try {
    response = await send(url, {
      signal,
      method: "GET",
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${operation.ticket.status_token}`,
      },
    });
  } catch {
    throw new Error(
      "Backup status is unavailable. The operation may still be running. Keep this page open and retry status.",
    );
  }
  if (!response.ok)
    throw new Error(
      "Backup status could not be read. The operation may still be running. Retry status or contact an operator.",
    );
  let data: { job?: BackupJob };
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "The backup service returned an unexpected status. Contact an operator before taking another action.",
    );
  }
  if (
    !data.job ||
    data.job.id !== operation.job.id ||
    data.job.kind !== operation.job.kind ||
    !["queued", "running", "succeeded", "failed"].includes(data.job.status)
  )
    throw new Error(
      "The backup service returned an unexpected status. Contact an operator before taking another action.",
    );
  return data.job;
}
