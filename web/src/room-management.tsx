import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { useResource } from "./hooks";
import type { ManagedRooms, Room, Viewer } from "./types";
import { Arrow, Confirm, Empty, Notice } from "./ui";
import { DescriptionEditor } from "./description-editor";
import { StartParticipantPreview } from "./preview";
import { ChallengeEditor } from "./challenge-editor";
const layouts = ["generic", "openshift", "ansible", "rhel", "security", "ai"];
interface RoomDraft {
  name: string;
  subtitle: string;
  description: string;
  theme: { accent: string; logo_url: string; hero_url: string; layout: string };
  challenge_ids: number[];
}
function draftOf(room: Room): RoomDraft {
  return {
    name: room.name,
    subtitle: room.subtitle,
    description: room.description,
    theme: {
      accent: room.theme.accent || "#008b9a",
      logo_url: room.theme.logo_url || "",
      hero_url: room.theme.hero_url || "",
      layout: room.theme.layout || "generic",
    },
    challenge_ids: room.steps.map((step) => step.id),
  };
}

export function RoomManagement({ viewer }: { viewer: Viewer }) {
  const resource = useResource<ManagedRooms>("/manage/rooms", 15000);
  const [slug, setSlug] = useState(""),
    [newRoom, setNewRoom] = useState(false),
    [roomDirty, setRoomDirty] = useState(false),
    [orderDirty, setOrderDirty] = useState(false),
    [challengeDirty, setChallengeDirty] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const data = resource.data;
  useEffect(() => {
    if (data && !data.rooms.some((room) => room.slug === slug)) {
      setSlug(data.rooms[0]?.slug || "");
      setRoomDirty(false);
      setChallengeDirty(false);
    }
  }, [data?.rooms, slug]);
  if (!data)
    return resource.error ? (
      <Notice tone="error" retry={() => void resource.refresh()}>
        {resource.error}
      </Notice>
    ) : (
      <p role="status">Loading room management…</p>
    );
  const room = data.rooms.find((item) => item.slug === slug);
  const dirty = roomDirty || challengeDirty;
  function select(next: string) {
    if (next === slug) return;
    if (
      dirty &&
      !window.confirm(
        "Discard unsaved room or challenge edits and change rooms?",
      )
    )
      return;
    setRoomDirty(false);
    setChallengeDirty(false);
    setSlug(next);
    setMessage("");
    setError("");
  }
  async function saveRoom(body: unknown): Promise<Room> {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await request<Room>(`/admin/rooms/${slug}`, "PATCH", body);
      await resource.refresh();
      setMessage("Room saved");
      return result;
    } catch (error) {
      setError((error as Error).message);
      throw error;
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="section-heading">
        <div>
          <h2>Rooms and challenges</h2>
          <p>
            Manage room presentation, ordered paths and standard flag challenges
            in one workspace.
          </p>
        </div>
        <div className="actions">
          {room && (
            <StartParticipantPreview
              room={room.slug}
              disabled={busy || dirty}
            />
          )}
          <button
            className="button secondary"
            disabled={busy || data.event.status !== "draft" || dirty}
            onClick={() => setNewRoom((value) => !value)}
          >
            {newRoom ? "Close new room" : "Add room"}
          </button>
          {viewer.admin && (
            <a href="/admin/challenges" className="text-button">
              Advanced CTFd authoring <Arrow />
            </a>
          )}
        </div>
      </div>
      {dirty && (
        <p className="meta">
          Unsaved edits are preserved while room data refreshes. Save or discard
          them before adding another room.
        </p>
      )}
      {data.event.status !== "draft" && (
        <p className="meta">
          Rooms can be added or removed in draft. Presentation remains editable;
          challenge mutations follow their server locks.
        </p>
      )}
      {message && <Notice tone="success">{message}</Notice>}
      {(error || resource.error) && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          {error || resource.error}
        </Notice>
      )}
      {newRoom && (
        <NewRoom
          busy={busy}
          onCreate={async (body) => {
            setBusy(true);
            setError("");
            try {
              const created = await request<Room>(
                "/manage/rooms",
                "POST",
                body,
              );
              await resource.refresh();
              setSlug(created.slug);
              setNewRoom(false);
              setMessage("Room created");
            } catch (error) {
              setError((error as Error).message);
              throw error;
            } finally {
              setBusy(false);
            }
          }}
        />
      )}{" "}
      {!data.rooms.length ? (
        <Empty title="Create the first room">
          The quest has no configured rooms. Add a room in draft, then write its
          challenges and choose the presentation.
        </Empty>
      ) : (
        <div className="admin-room-layout">
          <nav className="admin-room-nav" aria-label="Edit room">
            {data.rooms.map((item) => (
              <button
                key={item.id}
                className={item.slug === slug ? "active" : ""}
                onClick={() => select(item.slug)}
                disabled={busy}
              >
                {item.name}
                <span>
                  {item.steps.length} challenge
                  {item.steps.length === 1 ? "" : "s"} ·{" "}
                  {item.steps
                    .reduce((sum, step) => sum + step.value, 0)
                    .toLocaleString()}{" "}
                  pts
                </span>
              </button>
            ))}
          </nav>
          <div className="room-management-content">
            {room && (
              <>
                <RoomPresentation
                  key={room.id}
                  room={room}
                  data={data}
                  busy={busy}
                  onDirty={setRoomDirty}
                  onOrderDirty={setOrderDirty}
                  onSave={saveRoom}
                />
                <ChallengeEditor
                  key={`challenges-${room.id}`}
                  room={room}
                  orderDirty={orderDirty}
                  roomBusy={busy}
                  onDeleting={setBusy}
                  onChanged={resource.refresh}
                  onDirty={setChallengeDirty}
                />
                <section className="admin-panel room-removal">
                  <h3>Remove this room</h3>
                  <p>
                    Remove the room and its mapping. Its native challenges are
                    retained and become unmapped; participant history is
                    preserved.
                  </p>
                  {room.delete_reason && (
                    <p className="authoring-lock">{room.delete_reason}</p>
                  )}
                  <Confirm
                    key={`${room.id}-${room.can_delete}-${data.event.status}`}
                    label="Remove room"
                    description={`Remove “${room.name}” from the quest? Its native challenges will be retained and unmapped. This is allowed only in draft and is refused if the room has activity. Unsaved edits for this room will be discarded.`}
                    danger
                    disabled={
                      busy ||
                      data.event.status !== "draft" ||
                      room.can_delete === false
                    }
                    onConfirm={async () => {
                      setBusy(true);
                      setError("");
                      try {
                        await request(`/manage/rooms/${room.slug}`, "DELETE", {
                          confirm: true,
                        });
                        setRoomDirty(false);
                        setChallengeDirty(false);
                        await resource.refresh();
                        setMessage("Room removed; native challenges retained");
                      } catch (error) {
                        setError((error as Error).message);
                        throw error;
                      } finally {
                        setBusy(false);
                      }
                    }}
                  />
                </section>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
function NewRoom({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (body: unknown) => Promise<void>;
}) {
  const [name, setName] = useState(""),
    [slug, setSlug] = useState(""),
    [subtitle, setSubtitle] = useState("");
  return (
    <section className="admin-panel new-room-form">
      <h3>Add a room</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void onCreate({
            name: name.trim(),
            slug,
            subtitle,
            theme: { layout: "generic" },
          }).catch(() => {});
        }}
      >
        <div className="form-grid">
          <label className="field">
            <span>Room name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={80}
              disabled={busy}
            />
          </label>
          <label className="field">
            <span>Room URL identifier</span>
            <input
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              pattern="[a-z0-9-]{1,32}"
              maxLength={32}
              required
              disabled={busy}
              autoComplete="off"
              spellCheck={false}
            />
            <small>
              Lowercase letters, digits and hyphens. This stable identifier
              becomes /quest/rooms/{slug || "your-room"}.
            </small>
          </label>
        </div>
        <label className="field">
          <span>Subtitle</span>
          <input
            value={subtitle}
            onChange={(event) => setSubtitle(event.target.value)}
            maxLength={160}
            disabled={busy}
          />
        </label>
        <button
          className="button"
          disabled={busy || !name.trim() || !/^[a-z0-9-]{1,32}$/.test(slug)}
        >
          Create room
        </button>
      </form>
    </section>
  );
}
function RoomPresentation({
  room,
  data,
  busy,
  onDirty,
  onOrderDirty,
  onSave,
}: {
  room: Room;
  data: ManagedRooms;
  busy: boolean;
  onDirty: (dirty: boolean) => void;
  onOrderDirty: (dirty: boolean) => void;
  onSave: (body: unknown) => Promise<Room>;
}) {
  const [draft, setDraft] = useState(() => draftOf(room)),
    [selected, setSelected] = useState(""),
    [remote, setRemote] = useState(false);
  const baseline = useRef(draftOf(room));
  const incoming = JSON.stringify(draftOf(room));
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline.current);
  const orderChanged =
    JSON.stringify(draft.challenge_ids) !==
    JSON.stringify(baseline.current.challenge_ids);
  const canOrder = room.can_configure_topology ?? data.event.status === "draft";
  useEffect(() => {
    onOrderDirty(orderChanged);
    return () => onOrderDirty(false);
  }, [orderChanged, onOrderDirty]);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (JSON.stringify(draft) === JSON.stringify(baseline.current)) {
      const next = draftOf(room);
      baseline.current = next;
      setDraft(next);
      setRemote(false);
    } else setRemote(incoming !== JSON.stringify(baseline.current));
  }, [incoming]);
  function reload() {
    if (
      dirty &&
      !window.confirm(
        "Discard unsaved presentation edits and reload this room?",
      )
    )
      return;
    const next = draftOf(room);
    baseline.current = next;
    setDraft(next);
    setRemote(false);
  }
  function move(index: number, delta: number) {
    const next = [...draft.challenge_ids];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    setDraft({ ...draft, challenge_ids: next });
  }
  return (
    <section className="admin-panel room-presentation">
      <h3>Room presentation and path</h3>
      {remote && (
        <Notice>
          This room changed while you were editing. Your draft is preserved.
          Reload before changing its order.{" "}
          <button className="text-button" onClick={reload}>
            Reload saved room
          </button>
        </Notice>
      )}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !dirty || (orderChanged && remote)) return;
          const previous = baseline.current;
          const body: Record<string, unknown> = {};
          for (const key of ["name", "subtitle", "description"] as const)
            if (draft[key] !== previous[key]) body[key] = draft[key];
          const theme: Record<string, string> = {};
          for (const key of [
            "accent",
            "logo_url",
            "hero_url",
            "layout",
          ] as const)
            if (draft.theme[key] !== previous.theme[key])
              theme[key] = draft.theme[key];
          if (Object.keys(theme).length) body.theme = theme;
          if (orderChanged && canOrder)
            body.challenge_ids = draft.challenge_ids;
          try {
            const saved = await onSave(body);
            const next = draftOf(saved);
            baseline.current = next;
            setDraft(next);
            setRemote(false);
          } catch {
            /* The management workspace displays the error. */
          }
        }}
      >
        <div className="form-grid">
          <label className="field">
            <span>Room name</span>
            <input
              required
              maxLength={80}
              value={draft.name}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>Subtitle</span>
            <input
              maxLength={160}
              value={draft.subtitle}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, subtitle: event.target.value })
              }
            />
          </label>
        </div>
        <DescriptionEditor
          label="Room introduction (Markdown)"
          rows={8}
          maxLength={10000}
          value={draft.description}
          disabled={busy}
          onChange={(description) => setDraft({ ...draft, description })}
        />
        <details className="asset-fields">
          <summary>Theme and artwork</summary>
          <div className="form-grid">
            {(["logo_url", "hero_url"] as const).map((key) => (
              <label className="field" key={key}>
                <span>
                  {key === "logo_url" ? "Logo URL" : "Hero image URL"}
                </span>
                <input
                  value={draft.theme[key]}
                  disabled={busy}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      theme: { ...draft.theme, [key]: event.target.value },
                    })
                  }
                  placeholder="/plugins/open_tech_quest/assets/…"
                />
              </label>
            ))}
            <label className="field">
              <span>Theme layout</span>
              <select
                value={draft.theme.layout}
                disabled={busy}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    theme: { ...draft.theme, layout: event.target.value },
                  })
                }
              >
                {layouts.map((layout) => (
                  <option value={layout} key={layout}>
                    {layout === "generic" ? "Generic quest room" : layout}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Accent color</span>
              <input
                pattern="#[0-9a-fA-F]{6}"
                value={draft.theme.accent}
                disabled={busy}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    theme: { ...draft.theme, accent: event.target.value },
                  })
                }
              />
            </label>
          </div>
          <p className="meta">
            Use approved local assets. A generic room uses the quest artwork
            unless you choose a product layout or supply an image.
          </p>
        </details>
        <div className="section-heading">
          <h3>Ordered challenge path</h3>
          <span className="meta">{canOrder ? "Editable" : "Order locked"}</span>
        </div>
        {!canOrder && room.topology_reason && (
          <p className="authoring-lock">{room.topology_reason}</p>
        )}
        {!draft.challenge_ids.length && (
          <p className="meta">
            Create a challenge below or add an existing unmapped challenge
          </p>
        )}
        <ol className="challenge-order">
          {draft.challenge_ids.map((id, index) => (
            <li key={id}>
              <span className="order-number">{index + 1}</span>
              <span>
                {data.available_challenges.find(
                  (challenge) => challenge.id === id,
                )?.name ||
                  room.steps.find((step) => step.id === id)?.title ||
                  `Challenge ${id}`}
              </span>
              {canOrder && (
                <div className="order-actions">
                  <button
                    type="button"
                    aria-label={`Move challenge ${index + 1} up`}
                    disabled={busy || index === 0 || remote}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`Move challenge ${index + 1} down`}
                    disabled={
                      busy || index === draft.challenge_ids.length - 1 || remote
                    }
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    disabled={busy || remote}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        challenge_ids: draft.challenge_ids.filter(
                          (item) => item !== id,
                        ),
                      })
                    }
                  >
                    Unmap
                  </button>
                </div>
              )}
            </li>
          ))}
        </ol>
        {canOrder && (
          <div className="add-challenge">
            <label className="sr-only" htmlFor={`existing-${room.id}`}>
              Existing challenge to add
            </label>
            <select
              id={`existing-${room.id}`}
              value={selected}
              disabled={busy || remote}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">Choose an unmapped challenge</option>
              {data.available_challenges
                .filter(
                  (challenge) =>
                    !draft.challenge_ids.includes(challenge.id) &&
                    (!challenge.room_slug || challenge.room_slug === room.slug),
                )
                .map((challenge) => (
                  <option value={challenge.id} key={challenge.id}>
                    {challenge.name} · {challenge.state}
                  </option>
                ))}
            </select>
            <button
              type="button"
              className="button secondary"
              disabled={busy || !selected || remote}
              onClick={() => {
                setDraft({
                  ...draft,
                  challenge_ids: [...draft.challenge_ids, Number(selected)],
                });
                setSelected("");
              }}
            >
              Add step
            </button>
          </div>
        )}
        <p className="meta">
          Saving the path updates challenge requirements and links. Unmapping
          keeps the native challenge and its content; it does not delete it. To
          permanently delete a challenge, keep it mapped and use Delete
          challenge in its editor below. Deletion removes its saved path step
          automatically.
        </p>
        <div className="actions">
          <button
            className="button"
            disabled={busy || !dirty || (orderChanged && (remote || !canOrder))}
          >
            Save room
          </button>
          {dirty && (
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={reload}
            >
              Discard changes
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
