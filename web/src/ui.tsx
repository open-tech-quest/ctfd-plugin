import {
  useEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ReactNode,
} from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { prepareMarkdownLinks } from "./markdown-links";
import { navigate } from "./hooks";
export function Link({
  href = "",
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      href={href}
      {...props}
      onClick={(event) => {
        props.onClick?.(event);
        if (
          !event.defaultPrevented &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.shiftKey &&
          event.button === 0 &&
          href.startsWith("/quest")
        ) {
          event.preventDefault();
          navigate(href);
        }
      }}
    >
      {children}
    </a>
  );
}
export function Arrow() {
  return (
    <svg
      viewBox="0 0 20 20"
      width="20"
      height="20"
      fill="none"
      aria-hidden="true"
    >
      <path d="M3 10h13m-5-5 5 5-5 5" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
export function Check() {
  return (
    <svg
      viewBox="0 0 20 20"
      width="20"
      height="20"
      fill="none"
      aria-hidden="true"
    >
      <path d="m4 10 4 4 8-8" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
export function Notice({
  children,
  tone = "info",
  retry,
}: {
  children: ReactNode;
  tone?: "info" | "error" | "success";
  retry?: () => void;
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
      {retry && (
        <button className="text-button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}
export function Markdown({
  source,
  omitTitle,
}: {
  source: string;
  omitTitle?: string;
}) {
  const html = DOMPurify.sanitize(
    marked.parse(source, { async: false }) as string,
    {
      ADD_ATTR: ["target"],
      FORBID_TAGS: [
        "form",
        "input",
        "button",
        "style",
        "iframe",
        "object",
        "embed",
      ],
    },
  );
  const template = document.createElement("template");
  template.innerHTML = html;
  if (omitTitle) {
    const heading = template.content.firstElementChild;
    const normalize = (value: string) =>
      value
        .trim()
        .replace(/^demo(?:\s+task)?:\s*/i, "")
        .replace(/\s+/g, " ")
        .toLowerCase();
    if (
      heading?.matches("h1, h2, h3, h4, h5, h6") &&
      normalize(heading.textContent || "") === normalize(omitTitle)
    ) {
      heading.remove();
    }
  }
  prepareMarkdownLinks(template.content, window.location.href);
  return (
    <div
      className="prose"
      dangerouslySetInnerHTML={{ __html: template.innerHTML }}
    />
  );
}
export function Confirm({
  label,
  description,
  onConfirm,
  disabled = false,
  danger = false,
}: {
  label: string;
  description: string;
  onConfirm: () => Promise<void>;
  disabled?: boolean;
  danger?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={danger ? "button danger" : "button secondary"}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
      {open && (
        <ConfirmDialog
          label={label}
          description={description}
          onConfirm={onConfirm}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
export function ConfirmDialog({
  label,
  description,
  onConfirm,
  onClose,
  disabled = false,
  children,
}: {
  label: string;
  description: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.showModal();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      className="dialog"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <h2>{label}?</h2>
      <p>{description}</p>
      {children}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="actions">
        <button
          type="button"
          className="button secondary"
          autoFocus
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          type="button"
          className="button"
          disabled={busy || disabled}
          onClick={async () => {
            if (disabled) return;
            setBusy(true);
            setError("");
            try {
              await onConfirm();
              onClose();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Working…" : label}
        </button>
      </div>
    </dialog>
  );
}
