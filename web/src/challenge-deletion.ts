export function deleteChallenge(
  send: <T>(path: string, method: string, body: unknown) => Promise<T>,
  roomSlug: string,
  challengeId: number,
) {
  // Native CTFd accepts the CSRF header only for JSON requests. An explicit
  // empty body makes the shared transport send application/json for DELETE.
  return send<{ id: number; deleted: boolean }>(
    `/manage/rooms/${roomSlug}/challenges/${challengeId}`,
    "DELETE",
    {},
  );
}

export function deletionFailure(message: string, status?: number): string {
  if (status === 0)
    return "Connection lost while deleting the challenge. Deletion was not confirmed. Refresh the challenge list before retrying; the request may already have completed.";
  if (status !== undefined && status >= 500)
    return `${message} (HTTP ${status}). Deletion was not confirmed. Refresh the challenge list before retrying; the request may already have completed. If the challenge remains, ask an administrator to inspect the server logs.`;
  return message;
}
