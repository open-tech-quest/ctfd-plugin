export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  headers?: Record<string, string>,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(
      path.startsWith("/api/") ? path : `/api/v1/quest${path}`,
      {
        method,
        credentials: "same-origin",
        signal,
        headers: {
          ...headers,
          Accept: "application/json",
          "X-Quest-Attendance-UI": "1",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(method === "GET"
            ? {}
            : { "CSRF-Token": window.QUEST_INIT?.csrfNonce ?? "" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      },
    );
  } catch {
    throw new ApiError(
      "Connection lost. Your team’s saved progress is safe. Try again.",
      0,
    );
  }
  let envelope: {
    success?: boolean;
    data?: T;
    error?: string;
    message?: string;
    errors?: unknown;
  };
  try {
    envelope = await response.json();
  } catch {
    throw new ApiError(
      response.status === 401
        ? "Please sign in to continue."
        : "The server returned an unexpected response. Try again.",
      response.status,
    );
  }
  const nativeData = envelope.data as { message?: unknown } | undefined;
  const nativeMessage =
    typeof nativeData?.message === "string" ? nativeData.message : undefined;
  if (!response.ok || envelope.success === false)
    throw new ApiError(
      envelope.error ||
        envelope.message ||
        nativeMessage ||
        (response.status === 401
          ? "Please sign in to continue."
          : response.status === 403
            ? "This action is not available to your account."
            : "The request could not be completed."),
      response.status,
    );
  return envelope.data as T;
}
export function duration(ms: number | null | undefined) {
  if (ms == null) return "—";
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 3600)
    .toString()
    .padStart(
      2,
      "0",
    )}:${Math.floor(seconds / 60) % 60 < 10 ? "0" : ""}${Math.floor(seconds / 60) % 60}:${(seconds % 60).toString().padStart(2, "0")}`;
}
export function dateTime(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "Not set";
}
export function assetUrl(url: string | undefined) {
  if (!url) return undefined;
  try {
    const parsed = new URL(url, location.origin);
    return ["https:", "http:"].includes(parsed.protocol)
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function fileUrl(url: string) {
  try {
    const parsed = new URL(url, location.origin);
    return parsed.origin === location.origin &&
      ["https:", "http:"].includes(parsed.protocol)
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}

export function decode(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}
