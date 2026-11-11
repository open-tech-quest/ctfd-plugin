/** Insert a complete token using the textarea's UTF-16 selection offsets. */
export function insertDescriptionToken(
  value: string,
  token: string,
  selectionStart: number,
  selectionEnd: number,
  maxLength: number,
) {
  const start = Math.max(0, Math.min(value.length, selectionStart));
  const end = Math.max(start, Math.min(value.length, selectionEnd));
  if (value.length - (end - start) + token.length > maxLength) return null;
  return {
    value: value.slice(0, start) + token + value.slice(end),
    caret: start + token.length,
  };
}
