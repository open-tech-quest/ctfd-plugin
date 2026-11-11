export function purchaseQuotedHint(
  send: (
    path: string,
    method: string,
    body: unknown,
    signal?: AbortSignal,
    headers?: Record<string, string>,
  ) => Promise<unknown>,
  hint: { id: number; quote_revision?: string },
  localPreview = false,
) {
  if (!localPreview && !hint.quote_revision)
    throw new Error(
      "This hint's quote is unavailable. Refresh the page and reopen the hint before confirming.",
    );
  return send(
    "/api/v1/unlocks",
    "POST",
    { target: hint.id, type: "hints" },
    undefined,
    localPreview ? undefined : { "X-Quest-Hint-Quote": hint.quote_revision! },
  );
}
