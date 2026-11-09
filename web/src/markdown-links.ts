// Preserve explicit HTML targets; ordinary Markdown resource links use a separate tab.
export function markdownLinkTarget(
  href: string,
  baseURL: string,
  explicit: string | null,
) {
  if (explicit !== null) return explicit;
  try {
    const base = new URL(baseURL);
    const destination = new URL(href, base);
    return ["http:", "https:"].includes(destination.protocol) &&
      destination.origin !== base.origin
      ? "_blank"
      : null;
  } catch {
    return null;
  }
}

// Run only on sanitized markup. No global sanitizer hooks or Markdown extensions.
export function prepareMarkdownLinks(root: DocumentFragment, baseURL: string) {
  for (const link of root.querySelectorAll<HTMLAnchorElement>("a[href]")) {
    if (!(link instanceof HTMLAnchorElement)) continue;
    const target = markdownLinkTarget(
      link.getAttribute("href")!,
      baseURL,
      link.getAttribute("target"),
    );
    if (target === null) continue;
    link.setAttribute("target", target);
    if (!target || ["_self", "_parent", "_top"].includes(target.toLowerCase()))
      continue;
    for (const token of [...link.relList])
      if (token.toLowerCase() === "opener") link.relList.remove(token);
    link.relList.add("noopener");
    const hint = " (opens in a new tab)";
    if (link.hasAttribute("aria-label")) {
      link.setAttribute("aria-label", link.getAttribute("aria-label") + hint);
    } else {
      const cue = document.createElement("span");
      cue.className = "sr-only";
      cue.textContent = hint;
      link.append(cue);
    }
  }
}
