import { useEffect, useState } from "react";
import type { AdminState, Projector } from "./types";
import { rotationDefaults } from "./projector-rotation";
import { Arrow } from "./ui";
type Mutate = (
  path: string,
  method: string,
  body: unknown,
  success: string,
) => Promise<void>;
function draftFor(config: Projector) {
  const rotation = { ...rotationDefaults, ...config.rotation };
  return {
    view: config.view,
    room_slug: config.room_slug ?? "",
    enabled: rotation.enabled,
    overview_seconds: String(rotation.overview_seconds),
    room_seconds: String(rotation.room_seconds),
    overall_seconds: String(rotation.overall_seconds),
  };
}
export function ProjectorControls({
  data,
  busy,
  mutate,
}: {
  data: AdminState;
  busy: boolean;
  mutate: Mutate;
}) {
  const [draft, setDraft] = useState(() => draftFor(data.projector));
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!dirty) setDraft(draftFor(data.projector));
  }, [data.projector, dirty]);
  const rotation = data.projector.rotation;
  function edit(patch: Partial<typeof draft>) {
    setDirty(true);
    setDraft((previous) => ({ ...previous, ...patch }));
  }
  async function action(value: string) {
    try {
      await mutate(
        "/admin/projector",
        "PATCH",
        { rotation_action: value },
        `Projector rotation ${value === "pause" ? "paused" : value === "resume" ? "resumed" : "restarted"}`,
      );
    } catch {
      /* shared organizer notice shows the failure */
    }
  }
  return (
    <section className="admin-panel projector-settings">
      <div className="section-heading">
        <h2>The room’s shared screen</h2>
        <a
          className="button secondary"
          href="/quest/stage"
          target="_blank"
          rel="noopener"
        >
          Open projector <Arrow />
        </a>
      </div>
      <p>
        Give each room equal space. Automatic rotation shows the room overview,
        one featured room, then overall leaders. The featured room changes each
        cycle.
      </p>
      {rotation?.enabled && (
        <div className="rotation-actions">
          <span>Rotation {rotation.paused_at ? "paused" : "running"}</span>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void action(rotation.paused_at ? "resume" : "pause")}
          >
            {rotation.paused_at ? "Resume rotation" : "Pause rotation"}
          </button>
          <button
            className="text-button"
            disabled={busy}
            onClick={() => void action("restart")}
          >
            Restart from overview
          </button>
        </div>
      )}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const slug =
            draft.view === "room"
              ? draft.room_slug || data.rooms[0]?.slug
              : null;
          try {
            await mutate(
              "/admin/projector",
              "PATCH",
              {
                view: draft.view,
                room_slug: slug,
                rotation: {
                  enabled: draft.enabled,
                  overview_seconds: Number(draft.overview_seconds),
                  room_seconds: Number(draft.room_seconds),
                  overall_seconds: Number(draft.overall_seconds),
                },
              },
              "Projector display updated",
            );
            setDirty(false);
          } catch {
            /* keep the unsaved draft */
          }
        }}
      >
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) =>
              edit({
                enabled: e.target.checked,
                view: e.target.checked ? "overview" : draft.view,
              })
            }
          />
          Automatic room-focused rotation
        </label>
        {draft.enabled ? (
          <div className="rotation-durations">
            {(
              [
                { key: "overview_seconds", label: "Room overview" },
                { key: "room_seconds", label: "Featured room" },
                { key: "overall_seconds", label: "Overall leaders" },
              ] as const
            ).map((field) => (
              <label className="field" key={field.key}>
                <span>{field.label} (seconds)</span>
                <input
                  type="number"
                  min={10}
                  max={600}
                  step={1}
                  required
                  value={draft[field.key]}
                  onChange={(e) =>
                    edit({
                      [field.key]: e.target.value,
                    })
                  }
                />
              </label>
            ))}
          </div>
        ) : (
          <>
            <div className="projector-options">
              {(
                [
                  {
                    id: "overview",
                    label: "Room award overview",
                    detail: "Leading three ranks in each room, including ties",
                  },
                  {
                    id: "room",
                    label: "Featured room",
                    detail: "The room’s full standings and challenge progress",
                  },
                  {
                    id: "overall",
                    label: "Overall leaders",
                    detail:
                      data.standings.scoring_version === 2
                        ? "Leading five places by net points and active time"
                        : "Leading five places by solves and active time",
                  },
                  {
                    id: "matrix",
                    label: "Full team matrix",
                    detail: "Every team’s overall position and room progress",
                  },
                  {
                    id: "awards",
                    label: "Final awards",
                    detail:
                      "Published overall and room winners, including ties",
                  },
                ] as const
              ).map((option) => (
                <label
                  key={option.id}
                  className={draft.view === option.id ? "selected" : ""}
                >
                  <input
                    type="radio"
                    name="view"
                    value={option.id}
                    checked={draft.view === option.id}
                    disabled={
                      option.id === "awards" && !data.event.results_published
                    }
                    onChange={() => edit({ view: option.id })}
                  />
                  <strong>{option.label}</strong>
                  <span>{option.detail}</span>
                </label>
              ))}
            </div>
            {!data.event.results_published && (
              <p className="meta">
                Final awards become available after results are published.
              </p>
            )}
            {draft.view === "room" && (
              <label className="field">
                <span>Featured room</span>
                <select
                  required
                  value={draft.room_slug || data.rooms[0]?.slug || ""}
                  onChange={(e) => edit({ room_slug: e.target.value })}
                >
                  {data.rooms.map((room) => (
                    <option key={room.id} value={room.slug}>
                      {room.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
        <button
          className="button"
          disabled={
            busy ||
            (!draft.enabled && draft.view === "room" && !data.rooms.length)
          }
        >
          Update projector
        </button>
      </form>
    </section>
  );
}
