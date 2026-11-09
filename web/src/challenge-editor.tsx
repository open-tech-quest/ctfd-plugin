import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { useResource } from "./hooks";
import type {
  ChallengeWorkspace,
  EditableChallenge,
  EditableFlag,
  Room,
} from "./types";
import { Confirm, Empty, Markdown, Notice } from "./ui";
interface Draft {
  name: string;
  value: number | "";
  description: string;
  state: EditableChallenge["state"];
  flags: EditableFlag[];
}
function draftOf(challenge?: EditableChallenge): Draft {
  return {
    name: challenge?.name || "",
    value: challenge?.value ?? 100,
    description: challenge?.description || "",
    state: challenge?.state || "hidden",
    flags: challenge?.flags.map((flag) => ({ ...flag })) || [
      { content: "", case_insensitive: false },
    ],
  };
}

export function ChallengeEditor({
  room,
  onChanged,
  onDirty,
}: {
  room: Room;
  onChanged: () => Promise<void>;
  onDirty: (dirty: boolean) => void;
}) {
  const resource = useResource<ChallengeWorkspace>(
    `/manage/rooms/${room.slug}/challenges`,
    20000,
  );
  const [selected, setSelected] = useState<number | "new" | null>(null),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const workspace = resource.data;
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  function select(id: number | "new" | null) {
    if (dirty && !window.confirm("Discard unsaved challenge edits?")) return;
    setSelected(id);
    setDirty(false);
    setMessage("");
    setError("");
  }
  if (!workspace)
    return resource.error ? (
      <Notice tone="error" retry={() => void resource.refresh()}>
        {resource.error}
      </Notice>
    ) : (
      <p role="status">Loading room challenges…</p>
    );
  const challenge =
    typeof selected === "number"
      ? workspace.challenges.find((item) => item.id === selected)
      : undefined;
  async function save(draft: Draft) {
    setBusy(true);
    setError("");
    try {
      const result = await request<EditableChallenge>(
        `/manage/rooms/${room.slug}/challenges${selected === "new" ? "" : `/${selected}`}`,
        selected === "new" ? "POST" : "PATCH",
        draft,
      );
      setDirty(false);
      await Promise.all([resource.refresh(), onChanged()]);
      setSelected(result.id);
      setMessage("Challenge saved");
      return result;
    } catch (error) {
      setError((error as Error).message);
      throw error;
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="admin-panel challenge-authoring">
      <div className="section-heading">
        <div>
          <h2>Challenges in {room.name}</h2>
          <p>
            Write Markdown instructions, accepted answers and visibility for
            standard flag challenges.
          </p>
        </div>
        <button
          className="button secondary"
          disabled={busy || !workspace.can_create}
          onClick={() => select("new")}
        >
          New challenge
        </button>
      </div>
      {!workspace.can_create && workspace.create_reason && (
        <p className="authoring-lock">{workspace.create_reason}</p>
      )}
      {message && <Notice tone="success">{message}</Notice>}
      {(error || resource.error) && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          {error || resource.error}
        </Notice>
      )}
      <div className="challenge-authoring-layout">
        <nav className="challenge-editor-list" aria-label="Room challenges">
          {workspace.challenges.map((item) => (
            <button
              key={item.id}
              className={selected === item.id ? "active" : ""}
              onClick={() => select(item.id)}
              disabled={busy}
            >
              <span>{item.name}</span>
              <small>
                {item.state === "visible"
                  ? "Visible"
                  : item.state === "locked"
                    ? "Locked"
                    : "Hidden"}{" "}
                · {item.type}
                {!item.can_edit ? " · Read-only" : ""}
              </small>
            </button>
          ))}
          {!workspace.challenges.length && (
            <p className="meta">No challenges yet</p>
          )}
        </nav>
        <div>
          {selected === "new" || challenge ? (
            <ChallengeForm
              key={selected}
              challenge={challenge}
              canCreate={workspace.can_create}
              createReason={workspace.create_reason}
              busy={busy}
              onDirty={setDirty}
              onSave={save}
              onCancel={() => select(null)}
              onDelete={async () => {
                if (!challenge) return;
                setBusy(true);
                setError("");
                try {
                  await request(
                    `/manage/rooms/${room.slug}/challenges/${challenge.id}`,
                    "DELETE",
                  );
                  setSelected(null);
                  setDirty(false);
                  await Promise.all([resource.refresh(), onChanged()]);
                  setMessage("Challenge removed");
                } catch (error) {
                  setError((error as Error).message);
                  throw error;
                } finally {
                  setBusy(false);
                }
              }}
              previewPath={
                challenge
                  ? `/manage/rooms/${room.slug}/challenges/${challenge.id}/preview`
                  : null
              }
            />
          ) : selected !== null ? (
            <Notice>
              The selected challenge is no longer in this room. Choose another
              challenge.
            </Notice>
          ) : (
            <Empty title="Choose a challenge">
              Open a challenge to edit its text and accepted answers, or create
              one when authoring is available.
            </Empty>
          )}
        </div>
      </div>
    </section>
  );
}
function ChallengeForm({
  challenge,
  canCreate,
  createReason,
  busy,
  onDirty,
  onSave,
  onCancel,
  onDelete,
  previewPath,
}: {
  challenge?: EditableChallenge;
  canCreate: boolean;
  createReason: string | null;
  busy: boolean;
  onDirty: (dirty: boolean) => void;
  onSave: (draft: Draft) => Promise<EditableChallenge>;
  onCancel: () => void;
  onDelete: () => Promise<void>;
  previewPath: string | null;
}) {
  const [draft, setDraft] = useState(() => draftOf(challenge)),
    [remote, setRemote] = useState(false),
    [showFlags, setShowFlags] = useState(false),
    [submission, setSubmission] = useState(""),
    [checking, setChecking] = useState(false),
    [feedback, setFeedback] = useState<{
      good: boolean;
      message: string;
    } | null>(null);
  const baseline = useRef(JSON.stringify(draftOf(challenge)));
  const incoming = JSON.stringify(draftOf(challenge));
  const dirty = JSON.stringify(draft) !== baseline.current;
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (JSON.stringify(draft) === baseline.current) {
      const next = draftOf(challenge);
      baseline.current = JSON.stringify(next);
      setDraft(next);
      setRemote(false);
    } else setRemote(incoming !== baseline.current);
  }, [incoming]);
  const editable = challenge ? challenge.can_edit : canCreate;
  const reason = challenge ? challenge.edit_reason : createReason;
  useEffect(() => {
    if (dirty) setFeedback(null);
  }, [dirty]);
  const valid =
    draft.name.trim().length > 0 &&
    typeof draft.value === "number" &&
    Number.isInteger(draft.value) &&
    draft.value >= 0 &&
    draft.value <= 2147483647 &&
    draft.flags.length > 0 &&
    draft.flags.length <= 20 &&
    draft.flags.every(
      (flag) =>
        flag.content.trim().length > 0 &&
        flag.content === flag.content.trim() &&
        flag.content.length <= 1024,
    );
  function reload() {
    if (
      dirty &&
      !window.confirm("Discard unsaved edits and reload the saved challenge?")
    )
      return;
    const next = draftOf(challenge);
    baseline.current = JSON.stringify(next);
    setDraft(next);
    setRemote(false);
    setFeedback(null);
  }
  return (
    <div className="challenge-edit-form">
      <div className="section-heading">
        <h3>{challenge ? challenge.name : "New standard challenge"}</h3>
        <button className="text-button" disabled={busy} onClick={onCancel}>
          Close editor
        </button>
      </div>
      {!editable && reason && <Notice>{reason}</Notice>}
      {remote && (
        <Notice>
          The saved challenge changed while you were editing. Your draft is
          preserved.{" "}
          <button className="text-button" onClick={reload}>
            Reload saved version
          </button>
        </Notice>
      )}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!editable || !valid || busy || remote) return;
          try {
            const saved = await onSave(draft);
            const next = draftOf(saved);
            baseline.current = JSON.stringify(next);
            setDraft(next);
            setRemote(false);
            onDirty(false);
          } catch {
            /* The workspace displays the server error without exposing answers. */
          }
        }}
      >
        <label className="checkbox-field answer-visibility">
          <input
            type="checkbox"
            checked={showFlags}
            onChange={(event) => setShowFlags(event.target.checked)}
          />
          Show accepted answer values
        </label>
        <fieldset disabled={!editable || busy}>
          <label className="field">
            <span>Challenge name</span>
            <input
              maxLength={80}
              required
              value={draft.name}
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>Points</span>
            <input
              type="number"
              min={0}
              max={2147483647}
              step={1}
              required
              value={draft.value}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  value:
                    event.target.value === "" ? "" : event.target.valueAsNumber,
                })
              }
            />
            <small>
              Points count once per team. Paid hints reduce the team’s score.
            </small>
          </label>
          <label className="field">
            <span>Instructions (Markdown)</span>
            <textarea
              rows={10}
              maxLength={10000}
              value={draft.description}
              onChange={(event) =>
                setDraft({ ...draft, description: event.target.value })
              }
            />
          </label>
          <details className="introduction-preview">
            <summary>Preview challenge text</summary>
            {draft.description ? (
              <Markdown source={draft.description} />
            ) : (
              <p className="meta">Write instructions to preview them here</p>
            )}
          </details>
          <label className="field">
            <span>Visibility</span>
            <select
              aria-describedby="challenge-visibility-help"
              value={draft.state}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  state: event.target.value as Draft["state"],
                })
              }
            >
              <option value="hidden">Hidden from participants</option>
              <option value="visible">
                Visible when its room step is available
              </option>
              <option value="locked">Locked until published as visible</option>
            </select>
          </label>
          <p className="meta" id="challenge-visibility-help">
            Hidden challenges are left out of participant paths and progress.
            Locked challenges stay in the path; unsolved ones block later steps
            until made visible. Staff can preview either state.
          </p>
          <div className="section-heading">
            <h3>Accepted answers</h3>
          </div>
          {draft.flags.length ? (
            draft.flags.map((flag, index) => (
              <div className="accepted-answer" key={index}>
                <label className="field">
                  <span>Answer {index + 1}</span>
                  <input
                    type={showFlags ? "text" : "password"}
                    autoComplete="off"
                    maxLength={1024}
                    required
                    spellCheck={false}
                    value={flag.content}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        flags: draft.flags.map((item, i) =>
                          i === index
                            ? { ...item, content: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={flag.case_insensitive}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        flags: draft.flags.map((item, i) =>
                          i === index
                            ? {
                                ...item,
                                case_insensitive: event.target.checked,
                              }
                            : item,
                        ),
                      })
                    }
                  />
                  Ignore letter case
                </label>
                <button
                  type="button"
                  className="text-button"
                  disabled={draft.flags.length <= 1}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      flags: draft.flags.filter((_, i) => i !== index),
                    })
                  }
                >
                  Remove answer
                </button>
              </div>
            ))
          ) : (
            <p className="meta">
              This challenge uses unsupported flag logic and remains read-only
            </p>
          )}
          <button
            type="button"
            className="button secondary"
            disabled={draft.flags.length >= 20}
            onClick={() =>
              setDraft({
                ...draft,
                flags: [
                  ...draft.flags,
                  { content: "", case_insensitive: false },
                ],
              })
            }
          >
            Add accepted answer
          </button>
          <p className="meta answer-policy">
            One matching answer solves this challenge. Exact letter case is
            required unless “Ignore letter case” is selected. Answers cannot
            begin or end with whitespace.
          </p>
        </fieldset>
        <div className="actions">
          <button
            className="button"
            disabled={
              busy || !editable || !valid || (!!challenge && !dirty) || remote
            }
          >
            Save challenge
          </button>
          {challenge && (
            <Confirm
              key={`${challenge.id}-${challenge.can_delete}`}
              label="Remove challenge"
              description={`Permanently remove “${challenge.name}” and its accepted answers? The ordered room path will be rebuilt. Challenges with activity cannot be deleted.`}
              danger
              disabled={busy || !challenge.can_delete}
              onConfirm={onDelete}
            />
          )}
        </div>
        {challenge && !challenge.can_delete && challenge.delete_reason && (
          <p className="meta">{challenge.delete_reason}</p>
        )}
      </form>
      {challenge && (
        <section className="answer-preview">
          <h3>Check a saved answer</h3>
          <p>
            Uses the saved challenge's accepted answers without recording
            competition activity.
            {dirty ? " Save your edits before checking them." : ""}
          </p>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (!previewPath || dirty || checking) return;
              setChecking(true);
              setFeedback(null);
              try {
                const result = await request<{
                  status: "correct" | "incorrect";
                  message: string;
                }>(previewPath, "POST", { submission });
                setFeedback({
                  good: result.status === "correct",
                  message:
                    result.message ||
                    (result.status === "correct"
                      ? "Correct answer"
                      : "Incorrect answer"),
                });
                setSubmission("");
              } catch (error) {
                setFeedback({ good: false, message: (error as Error).message });
              } finally {
                setChecking(false);
              }
            }}
          >
            <label className="field">
              <span>Preview answer</span>
              <input
                type="password"
                autoComplete="off"
                maxLength={1024}
                value={submission}
                onChange={(event) => setSubmission(event.target.value)}
                disabled={checking || dirty || !challenge.flags.length}
              />
            </label>
            <button
              className="button secondary"
              disabled={
                checking || dirty || !submission || !challenge.flags.length
              }
            >
              {checking ? "Checking…" : "Check saved answer"}
            </button>
          </form>
          {feedback && (
            <Notice tone={feedback.good ? "success" : "error"}>
              {feedback.message}
            </Notice>
          )}
        </section>
      )}
    </div>
  );
}
