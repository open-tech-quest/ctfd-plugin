import type { Viewer } from "./types";

export type PlaceholderContext = {
  "team.number": number | null;
  "team.name": string | null;
  "user.name": string | null;
  "team.points": number | null;
};

export const BODY_PLACEHOLDERS = [
  {
    key: "team.number",
    label: "Team number",
    token: "{{team.number}}",
    help: "The native CTFd team ID; numbers may contain gaps.",
  },
  {
    key: "team.name",
    label: "Team name",
    token: "{{team.name}}",
    help: "The current viewer’s assigned team name.",
  },
  {
    key: "user.name",
    label: "User name",
    token: "{{user.name}}",
    help: "The signed-in viewer’s name.",
  },
  {
    key: "team.points",
    label: "Team points",
    token: "{{team.points}}",
    help: "Current overall net team points, after hint costs.",
  },
] as const;
type PlaceholderKey = keyof PlaceholderContext;

export const EXAMPLE_PLACEHOLDER_CONTEXT: PlaceholderContext = {
  "team.number": 1,
  "team.name": "Example team",
  "user.name": "Example participant",
  "team.points": 250,
};

export function participantPlaceholderContext(
  viewer: Pick<Viewer, "authenticated" | "name" | "team_id" | "team_name">,
  points: number | null,
  preview = false,
): PlaceholderContext {
  if (preview) return { ...EXAMPLE_PLACEHOLDER_CONTEXT };
  const assigned =
    viewer.authenticated &&
    Number.isSafeInteger(viewer.team_id) &&
    viewer.team_id! > 0;
  return {
    "team.number": assigned ? viewer.team_id : null,
    "team.name": assigned ? viewer.team_name : null,
    "user.name": viewer.authenticated ? viewer.name : null,
    "team.points":
      assigned && points !== null && Number.isFinite(points) ? points : null,
  };
}

type Literals = Readonly<Record<string, string>>;

/** Protect Markdown escapes before its parser removes the backslashes. */
export function preparePlaceholderSource(source: string): {
  source: string;
  literals: Literals;
} {
  const occupied = new Set(
    Array.from(
      source.matchAll(/OTQ_LITERAL_PLACEHOLDER_(\d+)_/g),
      (match) => match[1],
    ),
  );
  let generation = 0;
  while (occupied.has(String(generation))) ++generation;
  const prefix = `OTQ_LITERAL_PLACEHOLDER_${generation}_`;
  const literals: Record<string, string> = {};
  let count = 0;
  source = source.replace(
    /(\\*)\{(\\*)\{([^{}\r\n]+?)(\\*)\}(\\*)\}/g,
    (
      match,
      first: string,
      second: string,
      key: string,
      third: string,
      fourth: string,
    ) => {
      const escapes = [first, second, third, fourth];
      if (!escapes.some((value) => value.length % 2)) return match;
      const marker = `${prefix}${count++}_END`;
      const slash = (value: string) =>
        "\\".repeat(Math.floor(value.length / 2));
      literals[marker] =
        `${slash(first)}{${slash(second)}{${key}${slash(third)}}${slash(fourth)}}`;
      return marker;
    },
  );
  return { source, literals };
}

interface Token {
  start: number;
  end: number;
  raw: string;
  key?: string;
  literal?: string;
}
function tokens(source: string, literals: Literals, encoded = false): Token[] {
  const markers = Object.keys(literals).join("|"); // Generated markers contain only ASCII letters, numbers and underscores.
  const pattern = new RegExp(
    `${markers ? markers + "|" : ""}\\{\\{([^{}\\r\\n]+)\\}\\}${encoded ? "|%7[Bb]%7[Bb]((?:(?!%7[Dd]%7[Dd])[^{}\\r\\n])*)%7[Dd]%7[Dd]" : ""}`,
    "g",
  );
  return Array.from(source.matchAll(pattern), (match) => ({
    start: match.index!,
    end: match.index! + match[0].length,
    raw: match[0],
    ...(Object.hasOwn(literals, match[0])
      ? { literal: literals[match[0]] }
      : { key: match[1] ?? match[2] }),
  }));
}
function value(context: PlaceholderContext, key: string) {
  if (!BODY_PLACEHOLDERS.some((item) => item.key === key)) return null;
  if (!Object.hasOwn(context, key)) return null;
  const result = context[key as PlaceholderKey];
  if (typeof result === "number") {
    if (
      !Number.isFinite(result) ||
      (key === "team.number" && (!Number.isSafeInteger(result) || result <= 0))
    )
      return null;
    return String(result);
  }
  return typeof result === "string" && result.length ? result : null;
}
function replaceTokens(
  source: string,
  parts: Token[],
  replacement: (token: Token) => string,
) {
  let result = "",
    previous = 0;
  for (const part of parts) {
    result += source.slice(previous, part.start) + replacement(part);
    previous = part.end;
  }
  return result + source.slice(previous);
}

/** One pass: values containing tokens, Markdown or HTML remain literal data. */
export function resolvePlaceholderText(
  source: string,
  context: PlaceholderContext,
  literals: Literals = {},
) {
  return replaceTokens(
    source,
    tokens(source, literals),
    (token) => token.literal ?? value(context, token.key!) ?? token.raw,
  );
}

function component(value: string) {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}
export function resolvePlaceholderURL(
  source: string,
  context: PlaceholderContext,
  baseURL: string,
  literals: Literals = {},
): { url: string | null; reason: string | null } {
  const parts = tokens(source, literals, true);
  if (!parts.length) return { url: source, reason: null };
  // Browsers normalize these characters and shortened HTTP forms before parsing
  // the authority. Require an unambiguous template before checking token positions.
  if (/[\\\u0000-\u0020\u007f]/.test(source))
    return {
      url: null,
      reason:
        "Links using body placeholders cannot contain whitespace, control characters or backslashes.",
    };
  if (/^https?:/i.test(source) && !/^https?:\/\//i.test(source))
    return {
      url: null,
      reason: "Use a full HTTP or HTTPS URL for body placeholders.",
    };
  for (const part of parts) {
    if (part.literal !== undefined) continue;
    const definition = BODY_PLACEHOLDERS.find((item) => item.key === part.key);
    if (!definition)
      return { url: null, reason: `Unknown body placeholder: ${part.raw}.` };
    if (value(context, part.key!) === null)
      return {
        url: null,
        reason: `${definition.label} is not available for this viewer.`,
      };
  }
  // Names are URL components, never a source of protocols, credentials or hostnames.
  const scheme = /^[^/?#]*:/.exec(source);
  if (scheme && parts.some((part) => part.start < scheme[0].length))
    return {
      url: null,
      reason: "Body placeholders cannot choose a link protocol.",
    };
  const authority = /^(?:https?:)?\/\/([^/?#]*)/i.exec(source);
  let pathStart = 0;
  if (authority) {
    if (!authority[1])
      return { url: null, reason: "This link hostname is invalid." };
    const start = authority[0].length - authority[1].length,
      end = authority[0].length;
    const hostStart = start + authority[1].lastIndexOf("@") + 1;
    const colon = authority[1].lastIndexOf(":");
    const hostEnd = colon >= 0 ? start + colon : end;
    pathStart = end;
    if (
      parts.some(
        (part) =>
          part.start < end &&
          (part.literal !== undefined ||
            part.key !== "team.number" ||
            part.start < hostStart ||
            part.end > hostEnd),
      )
    )
      return {
        url: null,
        reason: "Only the team number can be used in a link hostname.",
      };
  } else if (source.startsWith("//")) {
    return { url: null, reason: "This link hostname is invalid." };
  }
  const url = replaceTokens(source, parts, (token) =>
    component(token.literal ?? value(context, token.key!)!),
  );
  // WHATWG URL parsing normalizes even percent-encoded dot segments. Never turn a name into navigation.
  const pathEnd = [source.indexOf("?"), source.indexOf("#")]
    .filter((index) => index >= 0)
    .reduce((a, b) => Math.min(a, b), source.length);
  let offset = pathStart;
  for (const segment of source.slice(pathStart, pathEnd).split("/")) {
    const local = parts.filter(
      (part) => part.start >= offset && part.end <= offset + segment.length,
    );
    if (local.length) {
      const rendered = replaceTokens(
        segment,
        local.map((part) => ({
          ...part,
          start: part.start - offset,
          end: part.end - offset,
        })),
        (token) => component(token.literal ?? value(context, token.key!)!),
      );
      try {
        if ([".", ".."].includes(decodeURIComponent(rendered)))
          return {
            url: null,
            reason: "This value cannot be used safely as a link path segment.",
          };
      } catch {
        return {
          url: null,
          reason: "This link contains invalid URL encoding.",
        };
      }
    }
    offset += segment.length + 1;
  }
  try {
    if (!["http:", "https:"].includes(new URL(url, baseURL).protocol))
      return {
        url: null,
        reason: "Body placeholders require an HTTP or HTTPS link.",
      };
  } catch {
    return { url: null, reason: "This link is invalid." };
  }
  return { url, reason: null };
}

/** Operates only on sanitized DOM. No context value is parsed as markup. */
export function applyBodyPlaceholders(
  root: DocumentFragment,
  context: PlaceholderContext,
  baseURL: string,
  literals: Literals = {},
) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  for (const node of nodes)
    node.data = resolvePlaceholderText(node.data, context, literals);
  for (const element of root.querySelectorAll("[title], [alt]")) {
    for (const attribute of ["title", "alt"])
      if (element.hasAttribute(attribute))
        element.setAttribute(
          attribute,
          resolvePlaceholderText(
            element.getAttribute(attribute)!,
            context,
            literals,
          ),
        );
  }
  for (const element of root.querySelectorAll("a[href], img[src]")) {
    const attribute = element.localName === "a" ? "href" : "src";
    const result = resolvePlaceholderURL(
      element.getAttribute(attribute)!,
      context,
      baseURL,
      literals,
    );
    if (result.url !== null) element.setAttribute(attribute, result.url);
    else {
      element.removeAttribute(attribute);
      if (attribute === "href") element.setAttribute("aria-disabled", "true");
      element.setAttribute("title", result.reason!);
      const explanation = document.createElement("span");
      explanation.className = "meta";
      explanation.textContent = ` (${attribute === "href" ? "Link" : "Image"} unavailable: ${result.reason})`;
      element.after(explanation);
    }
  }
}
