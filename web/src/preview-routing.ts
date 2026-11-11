export const previewHome = "/quest/preview";
export function previewRoom(slug: string) {
  return `${previewHome}/rooms/${encodeURIComponent(slug)}`;
}
export function isPreviewPath(path: string) {
  return (
    path === previewHome ||
    path === `${previewHome}/` ||
    path.startsWith(`${previewHome}/rooms/`)
  );
}
export function previewSlug(path: string): string | null {
  const match = /^\/quest\/preview\/rooms\/([^/]+)\/?$/.exec(path);
  if (!match) return null;
  try {
    const value = decodeURIComponent(match[1]);
    return value && !/[/?#\x00-\x20]/.test(value) ? value : null;
  } catch {
    return null;
  }
}
