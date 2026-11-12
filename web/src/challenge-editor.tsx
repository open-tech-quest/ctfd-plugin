import { useEffect, useRef, useState } from "react";
import { ApiError, request } from "./api";
import { deleteChallenge, deletionFailure } from "./challenge-deletion";
import { reviewChallengeWrite, saveChallengeWrite } from "./challenge-write";
import { useResource } from "./hooks";
import type {
  ChallengeWorkspace,
  EditableChallenge,
  QuestEvent,
  Room,
} from "./types";
import { ConfirmDialog, Empty, Markdown, Notice } from "./ui";
import { DescriptionEditor } from "./description-editor";
import {
  challengeDraftOf as draftOf,
  challengePayloadOf,
  type ChallengeDraft as Draft,
} from "./challenge-draft";
import {
  MaintenanceConfirmation,
  type MaintenanceApproval,
  type MaintenanceReview,
} from "./maintenance";

export function ChallengeEditor({
  room,
  eventStatus,
  orderDirty,
  roomBusy,
  onDeleting,
  onChanged,
  onDirty,
}: {
  room: Room;
  eventStatus: QuestEvent["status"];
  orderDirty: boolean;
  roomBusy: boolean;
  onDeleting: (busy: boolean) => void;
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
  async function save(draft: Draft, approval?: MaintenanceApproval) {
    setBusy(true);
    setError("");
    try {
      const result = await saveChallengeWrite(
        request,
        {
          room: room.slug,
          id: challenge?.id,
          revision: challenge?.revision ?? workspace?.revision,
        },
        challengePayloadOf(draft, challenge, eventStatus),
        approval,
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
            Edit instructions, accepted answers, points and hints for standard
            challenges.
          </p>
        </div>
        <button
          className="button secondary"
          disabled={
            busy ||
            !["draft", "live", "paused"].includes(eventStatus) ||
            !workspace.can_create
          }
          onClick={() => select("new")}
        >
          {eventStatus === "live" || eventStatus === "paused"
            ? "Append challenge"
            : "New challenge"}
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
              eventStatus={eventStatus}
              canCreate={workspace.can_create}
              createReason={workspace.create_reason}
              busy={busy}
              orderDirty={orderDirty}
              roomBusy={roomBusy}
              onRefresh={() => resource.refresh()}
              onDirty={setDirty}
              onSave={save}
              onReview={(draft) =>
                reviewChallengeWrite(
                  request,
                  {
                    room: room.slug,
                    id: challenge?.id,
                    revision: challenge?.revision ?? workspace.revision,
                  },
                  challengePayloadOf(draft, challenge, eventStatus),
                )
              }
              onCancel={() => select(null)}
              onDelete={async () => {
                if (!challenge) return;
                if (roomBusy)
                  throw new Error(
                    "Wait for the room change to finish before deleting a challenge.",
                  );
                if (orderDirty)
                  throw new Error(
                    "Save or discard room order changes before deleting a challenge.",
                  );
                setBusy(true);
                onDeleting(true);
                setError("");
                try {
                  await deleteChallenge(request, room.slug, challenge.id);
                  setSelected(null);
                  setDirty(false);
                  await Promise.all([resource.refresh(), onChanged()]);
                  setMessage("Challenge deleted; saved room path updated");
                } catch (error) {
                  throw new Error(
                    deletionFailure(
                      (error as Error).message,
                      error instanceof ApiError ? error.status : undefined,
                    ),
                  );
                } finally {
                  setBusy(false);
                  onDeleting(false);
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
  eventStatus,
  canCreate,
  createReason,
  busy,
  orderDirty,
  roomBusy,
  onRefresh,
  onDirty,
  onSave,
  onReview,
  onCancel,
  onDelete,
  previewPath,
}: {
  challenge?: EditableChallenge;
  eventStatus: QuestEvent["status"];
  canCreate: boolean;
  createReason: string | null;
  busy: boolean;
  orderDirty: boolean;
  roomBusy: boolean;
  onRefresh: () => Promise<void>;
  onDirty: (dirty: boolean) => void;
  onSave: (
    draft: Draft,
    approval?: MaintenanceApproval,
  ) => Promise<EditableChallenge>;
  onReview: (draft: Draft) => Promise<MaintenanceReview>;
  onCancel: () => void;
  onDelete: () => Promise<void>;
  previewPath: string | null;
}) {
  const [draft, setDraft] = useState(() => draftOf(challenge)),
    [remote, setRemote] = useState(false),
    [submission, setSubmission] = useState(""),
    [checking, setChecking] = useState(false),
    [deleteOpen, setDeleteOpen] = useState(false),
    [deleteError, setDeleteError] = useState(""),
    [reviewing, setReviewing] = useState(false),
    [saveError, setSaveError] = useState(""),
    [maintenance, setMaintenance] = useState<{
      review: MaintenanceReview;
      draft: Draft;
      withdrawing: boolean;
      preservedDraft?: Draft;
    } | null>(null),
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
  const phaseEditable = ["draft", "live", "paused"].includes(eventStatus);
  const editable =
    phaseEditable && (challenge ? challenge.can_edit : canCreate);
  const reason = !phaseEditable
    ? "This event is read-only. Ask an administrator to unpublish results if needed and reopen the event before maintenance."
    : challenge
      ? challenge.edit_reason
      : createReason;
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
    ) &&
    draft.hints.length <= 20 &&
    draft.hints.every(
      (hint) =>
        hint.title.length <= 80 &&
        hint.content.trim().length > 0 &&
        hint.content.length <= 10000 &&
        typeof hint.cost === "number" &&
        Number.isInteger(hint.cost) &&
        hint.cost >= 0 &&
        hint.cost <= 2147483647,
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
  async function applySave(
    nextDraft: Draft,
    approval?: MaintenanceApproval,
    preservedDraft?: Draft,
  ) {
    const saved = await onSave(nextDraft, approval);
    const next = draftOf(saved);
    const retained = preservedDraft
      ? { ...preservedDraft, state: next.state }
      : next;
    baseline.current = JSON.stringify(next);
    setDraft(retained);
    setRemote(false);
    onDirty(JSON.stringify(retained) !== JSON.stringify(next));
  }
  async function prepareSave(nextDraft: Draft, withdrawing = false) {
    setSaveError("");
    setReviewing(true);
    const preservedDraft = withdrawing && dirty ? draft : undefined;
    try {
      if (eventStatus === "live" || eventStatus === "paused") {
        const review = await onReview(nextDraft);
        if (review.requires_confirmation) {
          setMaintenance({
            review,
            draft: nextDraft,
            withdrawing,
            preservedDraft,
          });
          return;
        }
      }
      await applySave(nextDraft, undefined, preservedDraft);
    } catch (error) {
      setSaveError((error as Error).message);
    } finally {
      setReviewing(false);
    }
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
      {(eventStatus === "live" || eventStatus === "paused") && (
        <Notice>
          Content fixes can be saved during play. Changes to answers, points,
          visibility or hint prices require an impact review and reason. Choose
          whether price changes also adjust previous solves or purchases.
          Existing records and original hint receipts remain preserved.
        </Notice>
      )}
      {saveError && <Notice tone="error">{saveError}</Notice>}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!editable || !valid || busy || reviewing || remote || maintenance)
            return;
          await prepareSave(draft);
        }}
      >
        <fieldset disabled={!editable || busy || reviewing || !!maintenance}>
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
          {challenge &&
            (eventStatus === "live" || eventStatus === "paused") && (
              <div className="pricing-scope">
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={draft.adjust_existing_solves}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        adjust_existing_solves: event.target.checked,
                      })
                    }
                  />
                  <span>Also adjust existing solves</span>
                </label>
                <p className="meta">
                  {draft.adjust_existing_solves
                    ? "Existing solves will use this point value too. Review any score changes before applying."
                    : "Only new solves earn the new value. Earlier solves keep their existing credit."}
                </p>
              </div>
            )}
          <DescriptionEditor
            label="Instructions (Markdown)"
            rows={10}
            maxLength={10000}
            value={draft.description}
            disabled={!editable || busy}
            onChange={(description) => setDraft({ ...draft, description })}
          />
          <label className="field">
            <span>Connection details / URL (optional)</span>
            <textarea
              rows={2}
              maxLength={10000}
              value={draft.connection_info}
              onChange={(event) =>
                setDraft({ ...draft, connection_info: event.target.value })
              }
            />
          </label>
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
              <option value="locked">Locked — remains in the path</option>
            </select>
          </label>
          <p className="meta" id="challenge-visibility-help">
            Hidden withdraws the challenge from paths, solve credit and hint
            costs without deleting history. Locked keeps the step and its
            credit; unsolved locked steps block later challenges until made
            visible. Staff can browse either state.
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
                    type="text"
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
          <div className="section-heading">
            <h3>Hints</h3>
            <span className="meta">{draft.hints.length} of 20</span>
          </div>
          <p className="meta hint-policy">
            Each hint has its own text and cost. A team pays once, using its
            overall points. The cost also reduces its score in this room. Set a
            cost of 0 for a free hint.
            {eventStatus === "live" || eventStatus === "paused"
              ? " Choose separately for each hint whether its price also adjusts existing purchases. Original receipts are preserved. Purchased hints cannot be removed."
              : ""}
          </p>
          {draft.hints.length ? (
            draft.hints.map((hint, index) => {
              const change = (update: Partial<Draft["hints"][number]>) =>
                setDraft({
                  ...draft,
                  hints: draft.hints.map((item, i) =>
                    i === index ? { ...item, ...update } : item,
                  ),
                });
              const requiredBy =
                hint.id !== undefined &&
                draft.hints.some((other) =>
                  other.prerequisites.includes(hint.id!),
                );
              return (
                <section
                  className="hint-editor"
                  key={hint.id ?? `new-${index}`}
                  aria-label={`Hint ${index + 1}`}
                >
                  <div className="section-heading">
                    <h4>Hint {index + 1}</h4>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!requiredBy}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          hints: draft.hints.filter((_, i) => i !== index),
                        })
                      }
                    >
                      Remove hint {index + 1}
                    </button>
                  </div>
                  <div className="hint-editor-fields">
                    <label className="field">
                      <span>Hint {index + 1} title (optional)</span>
                      <input
                        value={hint.title}
                        maxLength={80}
                        onChange={(event) =>
                          change({ title: event.target.value })
                        }
                      />
                    </label>
                    <label className="field hint-cost-field">
                      <span>Hint {index + 1} cost</span>
                      <input
                        type="number"
                        min={0}
                        max={2147483647}
                        step={1}
                        required
                        value={hint.cost}
                        onChange={(event) =>
                          change({
                            cost:
                              event.target.value === ""
                                ? ""
                                : event.target.valueAsNumber,
                          })
                        }
                      />
                      <small>
                        {hint.cost === 0 ? "Free" : "Points per team"}
                      </small>
                    </label>
                  </div>
                  {challenge &&
                    hint.id !== undefined &&
                    challenge.hints.some((saved) => saved.id === hint.id) &&
                    (eventStatus === "live" || eventStatus === "paused") && (
                      <div className="pricing-scope">
                        <label className="checkbox-field">
                          <input
                            type="checkbox"
                            checked={hint.adjust_existing_purchases}
                            onChange={(event) =>
                              change({
                                adjust_existing_purchases: event.target.checked,
                              })
                            }
                          />
                          <span>
                            Also adjust existing purchases of hint {index + 1}
                          </span>
                        </label>
                        <p className="meta">
                          {hint.adjust_existing_purchases
                            ? "This cost will also replace the current deduction for earlier purchases. Increasing it can reduce teams’ balances below zero; decreasing it returns points."
                            : "Only new purchases use the new cost. Earlier purchases keep their current deduction."}
                        </p>
                      </div>
                    )}
                  <label className="field">
                    <span>Hint {index + 1} text (Markdown)</span>
                    <textarea
                      rows={4}
                      maxLength={10000}
                      required
                      value={hint.content}
                      onChange={(event) =>
                        change({ content: event.target.value })
                      }
                    />
                  </label>
                  <details className="introduction-preview">
                    <summary>Preview hint {index + 1}</summary>
                    {hint.content.trim() ? (
                      <Markdown source={hint.content} />
                    ) : (
                      <p className="meta">Write a hint to preview it here</p>
                    )}
                  </details>
                  {!!hint.prerequisites.length && (
                    <p className="meta">
                      This hint requires other hints. Its existing requirements
                      will be preserved.
                    </p>
                  )}
                  {requiredBy && (
                    <p className="meta">
                      Another hint requires this one. Remove that dependency
                      before deleting it.
                    </p>
                  )}
                </section>
              );
            })
          ) : (
            <p className="meta">
              No hints yet. Add a clue participants can reveal when they need
              help.
            </p>
          )}
          <button
            type="button"
            className="button secondary"
            disabled={draft.hints.length >= 20}
            onClick={() =>
              setDraft({
                ...draft,
                hints: [
                  ...draft.hints,
                  {
                    title: "",
                    content: "",
                    cost: 25,
                    prerequisites: [],
                    adjust_existing_purchases: false,
                  },
                ],
              })
            }
          >
            Add hint
          </button>
        </fieldset>
        <div className="actions">
          <button
            className="button"
            disabled={
              busy ||
              reviewing ||
              !!maintenance ||
              !editable ||
              !valid ||
              (!!challenge && !dirty) ||
              remote
            }
          >
            Save challenge
          </button>
        </div>
      </form>
      {challenge &&
        editable &&
        (eventStatus === "live" || eventStatus === "paused") &&
        challenge.state !== "hidden" && (
          <section className="challenge-deletion">
            <h3>Withdraw a broken challenge</h3>
            <p>
              Hidden withdraws this step from every team's path and removes its
              solve credit and hint costs from current results. The original
              solves and purchases remain recorded. Locked keeps the step and
              its credit in the path and can block later challenges.
            </p>
            <button
              type="button"
              className="button secondary"
              disabled={busy || reviewing || !!maintenance || remote}
              onClick={() =>
                void prepareSave(
                  { ...draftOf(challenge), state: "hidden" },
                  true,
                )
              }
            >
              Review withdrawal
            </button>
          </section>
        )}
      {maintenance && (
        <MaintenanceConfirmation
          review={maintenance.review}
          label={
            maintenance.withdrawing
              ? "Withdraw challenge"
              : challenge
                ? "Save competitive changes"
                : "Append challenge"
          }
          description={
            maintenance.withdrawing
              ? "Hide this challenge for all teams, withdrawing its current credit and hint costs without deleting history. Other unsaved edits remain in this editor."
              : "Apply these changes to the running event. Review their effect on teams and keep a reason in the audit."
          }
          stale={remote || !phaseEditable}
          onClose={() => setMaintenance(null)}
          onConfirm={async (approval) => {
            try {
              await applySave(
                maintenance.draft,
                approval,
                maintenance.preservedDraft,
              );
            } catch (error) {
              setSaveError((error as Error).message);
              throw error;
            }
          }}
        />
      )}
      {challenge && (
        <section className="challenge-deletion" aria-label="Delete challenge">
          <h3>Delete challenge</h3>
          <p>
            Permanently delete this challenge, its accepted answers, hints,
            solution and attachment links. Its saved path step is removed and
            the remaining challenges are relinked automatically. You do not need
            to unmap it first. To keep its content, use Unmap in the room path
            instead.
          </p>
          <p className="meta">
            Deletion requires Draft, no loaded demo, no room starts or
            corrections, and no submissions, unlocks or ratings in the room's
            path. External challenge dependencies, remaining hint prerequisites
            and unsupported native content can also block deletion. History is
            never cleared by this action.
          </p>
          {challenge.source_key && (
            <p className="authoring-lock">
              This challenge is catalog-managed ({challenge.source_key}).
              Deletion also removes its source mapping; a later catalog sync can
              create it again. Unmap retains its catalog identity.
            </p>
          )}
          {dirty && (
            <p className="meta">
              Unsaved challenge edits will be discarded if you delete it.
            </p>
          )}
          {orderDirty && (
            <p className="authoring-lock">
              Save or discard room order changes before deleting a challenge.
              Use Save room or Discard changes in Room presentation and path
              above.
            </p>
          )}
          {!challenge.can_delete && challenge.delete_reason && (
            <Notice>{challenge.delete_reason}</Notice>
          )}
          {deleteError && <Notice tone="error">{deleteError}</Notice>}
          <div className="actions">
            <button
              type="button"
              className="button danger"
              disabled={busy || roomBusy || orderDirty || !challenge.can_delete}
              onClick={() => setDeleteOpen(true)}
            >
              Delete challenge
            </button>
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => void onRefresh()}
            >
              Refresh deletion status
            </button>
          </div>
          {deleteOpen && (
            <ConfirmDialog
              label="Delete challenge"
              description={`Permanently delete “${challenge.name}”, its accepted answers, hints, solution and attachment links? Its saved path step will be removed and the remaining challenges relinked. This cannot be undone.`}
              disabled={busy || roomBusy || orderDirty || !challenge.can_delete}
              onClose={() => setDeleteOpen(false)}
              onConfirm={async () => {
                setDeleteError("");
                try {
                  await onDelete();
                } catch (error) {
                  setDeleteError((error as Error).message);
                  throw error;
                }
              }}
            >
              {orderDirty && (
                <Notice>
                  Save or discard room order changes before deleting a
                  challenge.
                </Notice>
              )}
              {!challenge.can_delete && challenge.delete_reason && (
                <Notice>{challenge.delete_reason}</Notice>
              )}
              {challenge.source_key && (
                <p>
                  A later catalog sync can recreate this catalog-managed
                  challenge.
                </p>
              )}
            </ConfirmDialog>
          )}
        </section>
      )}
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
                type="text"
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
