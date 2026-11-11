import { useId, useLayoutEffect, useRef, useState } from "react";
import { Markdown } from "./ui";
import { BODY_PLACEHOLDERS, EXAMPLE_PLACEHOLDER_CONTEXT } from "./placeholders";
import { insertDescriptionToken } from "./description-insertion";
export function DescriptionEditor({
  label,
  value,
  onChange,
  disabled = false,
  rows = 8,
  maxLength = 10000,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  rows?: number;
  maxLength?: number;
}) {
  const id = useId(),
    field = useRef<HTMLTextAreaElement>(null),
    pendingCaret = useRef<number | null>(null);
  const [selected, setSelected] = useState(BODY_PLACEHOLDERS[0].key),
    [selection, setSelection] = useState({ start: 0, end: 0 }),
    [insertions, setInsertions] = useState(0);
  const placeholder = BODY_PLACEHOLDERS.find((item) => item.key === selected)!;
  const fits =
    insertDescriptionToken(
      value,
      placeholder.token,
      selection.start,
      selection.end,
      maxLength,
    ) !== null;
  useLayoutEffect(() => {
    if (pendingCaret.current === null || !field.current) return;
    const caret = pendingCaret.current;
    pendingCaret.current = null;
    field.current.focus({ preventScroll: true });
    field.current.setSelectionRange(caret, caret);
  }, [value, insertions]);
  return (
    <div className="description-editor">
      <label className="field" htmlFor={id}>
        <span>{label}</span>
        <textarea
          id={id}
          ref={field}
          rows={rows}
          maxLength={maxLength}
          value={value}
          disabled={disabled}
          aria-describedby={`${id}-help`}
          onSelect={(event) =>
            setSelection({
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            })
          }
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      <div
        className="description-placeholder-tools"
        role="group"
        aria-label={`Insert placeholder into ${label}`}
      >
        <label htmlFor={`${id}-placeholder`}>Insert placeholder</label>
        <select
          id={`${id}-placeholder`}
          aria-describedby={`${id}-help`}
          value={selected}
          disabled={disabled}
          onChange={(event) =>
            setSelected(event.target.value as typeof selected)
          }
        >
          {BODY_PLACEHOLDERS.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button secondary"
          aria-describedby={`${id}-help`}
          disabled={disabled || !fits}
          onClick={() => {
            if (disabled || !field.current) return;
            const result = insertDescriptionToken(
              value,
              placeholder.token,
              field.current.selectionStart,
              field.current.selectionEnd,
              maxLength,
            );
            if (!result) return;
            pendingCaret.current = result.caret;
            setSelection({ start: result.caret, end: result.caret });
            onChange(result.value);
            setInsertions((count) => count + 1);
          }}
        >
          Insert
        </button>
        <code>{placeholder.token}</code>
      </div>
      <p className="description-placeholder-help" id={`${id}-help`}>
        {placeholder.help}
        {!fits && (
          <>
            {" "}
            This placeholder would exceed the {maxLength.toLocaleString()}
            -character limit. Select text to replace or shorten the description.
          </>
        )}
      </p>
      <details className="introduction-preview">
        <summary>Preview with example values</summary>
        <p className="meta">
          Examples: team number {EXAMPLE_PLACEHOLDER_CONTEXT["team.number"]},
          team name {EXAMPLE_PLACEHOLDER_CONTEXT["team.name"]}, user name{" "}
          {EXAMPLE_PLACEHOLDER_CONTEXT["user.name"]}, team points{" "}
          {EXAMPLE_PLACEHOLDER_CONTEXT["team.points"]}.
        </p>
        {value ? (
          <Markdown source={value} placeholders={EXAMPLE_PLACEHOLDER_CONTEXT} />
        ) : (
          <p className="meta">Write a description to preview it here.</p>
        )}
      </details>
    </div>
  );
}
