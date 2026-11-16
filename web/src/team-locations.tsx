import { useEffect, useState } from "react";
import { request } from "./api";
import { useResource } from "./hooks";
import { ConfirmDialog, Empty, Notice } from "./ui";

type Place = { venue_room_id: number; table_number: number };
type Venue = {
  id: number | null;
  name: string;
  table_count: number;
  unavailable_tables: number[];
  unavailable_text?: string;
};
type LocatedTeam = {
  id: number;
  name: string;
  members: number;
  active: boolean;
  location: Place | null;
};
type Locations = {
  revision: string;
  rooms: (Venue & { id: number })[];
  teams: LocatedTeam[];
  can_edit_layout: boolean;
  can_assign: boolean;
};
type Plan = {
  revision: string;
  source_revision: string;
  reassign_existing: boolean;
  shortage: number;
  preserved: number;
  strategy: "spread" | "fill";
  assignments: {
    team_id: number;
    team_name: string;
    previous_label: string;
    room_name: string;
    table_number: number;
  }[];
  occupancy: {
    venue_room_id: number;
    name: string;
    teams: number;
    capacity: number;
  }[];
};

export function TeamLocations({
  onChanged,
}: {
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="panel venue-workspace">
      <div className="venue-heading">
        <div>
          <h2>Team locations</h2>
          <p>
            Give every team a room and table. Only administrators can change
            locations.
          </p>
        </div>
        <button
          type="button"
          className="button secondary"
          aria-expanded={open}
          aria-controls="venue-workspace"
          onClick={() => setOpen(!open)}
        >
          {open ? "Close locations" : "Manage locations"}
        </button>
      </div>
      {open && <LocationResource onChanged={onChanged} />}
    </section>
  );
}

function LocationResource({ onChanged }: { onChanged: () => Promise<void> }) {
  const resource = useResource<Locations>("/admin/locations", 20000);
  if (!resource.data)
    return resource.error ? (
      <Notice tone="error" retry={() => void resource.refresh()}>
        {resource.error}
      </Notice>
    ) : (
      <p role="status">Loading locations…</p>
    );
  return (
    <>
      {resource.error && (
        <Notice tone="error" retry={() => void resource.refresh()}>
          Showing the last saved locations. {resource.error}
        </Notice>
      )}
      <Workspace
        data={resource.data}
        refresh={resource.refresh}
        onChanged={onChanged}
      />
    </>
  );
}

function Workspace({
  data,
  refresh,
  onChanged,
}: {
  data: Locations;
  refresh: () => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const [rooms, setRooms] = useState<Venue[]>(data.rooms),
    [layoutRevision, setLayoutRevision] = useState(data.revision),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [query, setQuery] = useState(""),
    [reassignExisting, setReassignExisting] = useState(false),
    [strategy, setStrategy] = useState<"spread" | "fill">("spread"),
    [plan, setPlan] = useState<Plan | null>(null),
    [editing, setEditing] = useState<LocatedTeam | null>(null);
  useEffect(() => {
    if (!dirty) {
      setRooms(data.rooms);
      setLayoutRevision(data.revision);
    }
  }, [data.rooms, data.revision, dirty]);
  useEffect(() => {
    if (!data.can_edit_layout) {
      setReassignExisting(false);
      setPlan(null);
    }
  }, [data.can_edit_layout]);
  async function act<T>(
    path: string,
    method: string,
    payload: unknown,
    changed = true,
  ) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await request<T>(path, method, payload);
      if (changed) {
        setPlan(null);
        await refresh();
        await onChanged();
      }
      return result;
    } catch (e) {
      setError((e as Error).message);
      throw e;
    } finally {
      setBusy(false);
    }
  }
  function editRoom(index: number, change: Partial<Venue>) {
    setRooms(
      rooms.map((room, i) => (i === index ? { ...room, ...change } : room)),
    );
    setDirty(true);
    setPlan(null);
  }
  function label(place: Place | null) {
    return place
      ? `${data.rooms.find((r) => r.id === place.venue_room_id)?.name || "Venue room"} · Table ${place.table_number}`
      : "Not assigned yet";
  }
  const assigned = data.teams.filter((t) => t.location).length;
  const filteredTeams = data.teams.filter((team) =>
    `${team.id} ${team.name} ${label(team.location)}`
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  return (
    <div id="venue-workspace">
      {error && (
        <Notice tone="error" retry={() => void refresh()}>
          {error}
        </Notice>
      )}
      {message && <Notice>{message}</Notice>}
      <div className="venue-section-heading">
        <h3>Venue rooms</h3>
        <span className="meta">
          {assigned} of{" "}
          {data.teams.filter((t) => t.active || t.location).length} teams have a
          location
        </span>
      </div>
      <p className="meta">
        Tables are numbered from 1 within each room. Room order is also the fill
        order. One table holds one whole team.
      </p>
      {data.can_edit_layout ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void act<Locations>("/admin/locations/layout", "PUT", {
              revision: layoutRevision,
              rooms: rooms.map((r) => ({
                id: r.id,
                name: r.name,
                table_count: r.table_count,
                unavailable_tables: r.unavailable_tables,
              })),
            })
              .then((result) => {
                setDirty(false);
                setRooms(result.rooms);
                setLayoutRevision(result.revision);
                setMessage("Venue layout saved.");
              })
              .catch(() => {});
          }}
        >
          {rooms.map((room, index) => (
            <div
              className="venue-room-row"
              key={room.id ?? `new-${index}`}
              role="group"
              aria-label={`Venue room ${index + 1}`}
            >
              <label>
                Venue room
                <input
                  disabled={busy}
                  required
                  maxLength={128}
                  value={room.name}
                  onChange={(event) =>
                    editRoom(index, { name: event.target.value })
                  }
                />
              </label>
              <label>
                Tables
                <input
                  disabled={busy}
                  type="number"
                  min={1}
                  max={500}
                  required
                  value={room.table_count || ""}
                  onChange={(event) =>
                    editRoom(index, { table_count: Number(event.target.value) })
                  }
                />
              </label>
              <label>
                Unavailable tables
                <input
                  disabled={busy}
                  placeholder="e.g. 3, 7"
                  value={
                    room.unavailable_text ?? room.unavailable_tables.join(", ")
                  }
                  onChange={(event) =>
                    editRoom(index, {
                      unavailable_text: event.target.value,
                      unavailable_tables: event.target.value
                        .split(",")
                        .filter((x) => x.trim())
                        .map((x) => Number(x.trim())),
                    })
                  }
                />
              </label>
              <div className="actions">
                <button
                  type="button"
                  className="button secondary"
                  disabled={index === 0 || busy}
                  aria-label={`Move ${room.name || "venue room"} up`}
                  onClick={() => {
                    const next = [...rooms];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    setRooms(next);
                    setDirty(true);
                    setPlan(null);
                  }}
                >
                  Move up
                </button>
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy}
                  onClick={() => {
                    setRooms(rooms.filter((_, i) => i !== index));
                    setDirty(true);
                    setPlan(null);
                  }}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
          {!rooms.length && (
            <Empty title="Add your first venue room">
              Enter its name and number of tables to start assigning locations.
            </Empty>
          )}
          <div className="actions">
            <button
              type="button"
              className="button secondary"
              disabled={busy || rooms.length >= 50}
              onClick={() => {
                setRooms([
                  ...rooms,
                  {
                    id: null,
                    name: "",
                    table_count: 1,
                    unavailable_tables: [],
                  },
                ]);
                setDirty(true);
                setPlan(null);
              }}
            >
              Add venue room
            </button>
            <button className="button" disabled={busy || !dirty}>
              Save venue layout
            </button>
            {dirty && (
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => {
                  setDirty(false);
                  setRooms(data.rooms);
                  setLayoutRevision(data.revision);
                }}
              >
                Discard layout changes
              </button>
            )}
          </div>
          {dirty && layoutRevision !== data.revision && (
            <Notice>
              The saved locations changed while you were editing. Your draft is
              kept; discard it to reload the current layout before saving.
            </Notice>
          )}
        </form>
      ) : (
        <ul className="venue-room-summary">
          {data.rooms.map((room) => (
            <li key={room.id}>
              <strong>{room.name}</strong>
              <span>
                {room.table_count} tables
                {room.unavailable_tables.length
                  ? ` · ${room.unavailable_tables.length} unavailable`
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="venue-distribution">
        <h3>Automatic distribution</h3>
        <p>
          {reassignExisting
            ? "Preview new locations for current participant teams, including manual placements."
            : "Assign teams without a location. Existing locations stay in place."}
        </p>
        {data.can_edit_layout && (
          <>
            <label className="venue-swap">
              <input
                type="checkbox"
                checked={reassignExisting}
                disabled={busy || dirty}
                onChange={(event) => {
                  setReassignExisting(event.target.checked);
                  setPlan(null);
                }}
              />
              Reassign existing locations
            </label>
            <p className="meta">
              When selected, the preview includes current participant teams and
              their manual placements. Review the proposed moves before
              applying.
            </p>
          </>
        )}
        <div className="venue-strategy">
          <label>
            Distribution
            <select
              value={strategy}
              disabled={busy}
              onChange={(event) => {
                setStrategy(event.target.value as "spread" | "fill");
                setPlan(null);
              }}
            >
              <option value="spread">Spread across rooms</option>
              <option value="fill">Fill rooms in order</option>
            </select>
          </label>
          <button
            type="button"
            className="button secondary"
            disabled={busy || dirty || !data.can_assign}
            onClick={() => {
              void act<Plan>(
                "/admin/locations/distribution",
                "POST",
                {
                  revision: data.revision,
                  strategy,
                  reassign_existing: reassignExisting,
                  apply: false,
                },
                false,
              )
                .then(setPlan)
                .catch(() => {});
            }}
          >
            Preview distribution
          </button>
        </div>
        <p className="meta">
          {strategy === "spread"
            ? "Keep team counts as even as table capacity allows."
            : "Fill the first room before assigning the next."}
          {dirty ? " Save your venue layout before assigning teams." : ""}
        </p>
        {plan && (
          <div className="venue-plan" aria-live="polite">
            <h4>Distribution preview</h4>
            <ul className="venue-room-summary">
              {plan.occupancy.map((room) => (
                <li key={room.venue_room_id}>
                  <strong>{room.name}</strong>
                  <span>
                    {room.teams} teams / {room.capacity} available tables
                  </span>
                </li>
              ))}
            </ul>
            <p>
              {plan.assignments.length} assignments · {plan.preserved} existing
              locations preserved
            </p>
            {plan.shortage > 0 && (
              <Notice tone="error">
                Not enough tables: {plan.shortage} more needed. Add capacity
                before applying.
              </Notice>
            )}
            {plan.source_revision !== data.revision && (
              <Notice>
                Teams or locations changed. Preview the distribution again.
              </Notice>
            )}
            {plan.assignments.length > 0 ? (
              <div
                className="table-scroll"
                tabIndex={0}
                role="region"
                aria-label="Proposed team locations"
              >
                <table>
                  <caption className="sr-only">Proposed team locations</caption>
                  <thead>
                    <tr>
                      <th>Team</th>
                      {plan.reassign_existing && <th>Current location</th>}
                      <th>Proposed location</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.assignments.map((row) => (
                      <tr key={row.team_id}>
                        <th scope="row">{row.team_name}</th>
                        {plan.reassign_existing && (
                          <td>{row.previous_label}</td>
                        )}
                        <td>
                          {row.room_name} · Table {row.table_number}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              !plan.shortage && (
                <p>Every eligible team already has a location.</p>
              )
            )}
            <button
              type="button"
              className="button"
              disabled={
                busy ||
                dirty ||
                !data.can_assign ||
                !!plan.shortage ||
                !plan.assignments.length ||
                plan.source_revision !== data.revision
              }
              onClick={() => {
                void act("/admin/locations/distribution", "POST", {
                  revision: plan.revision,
                  strategy: plan.strategy,
                  reassign_existing: plan.reassign_existing,
                  apply: true,
                })
                  .then(() =>
                    setMessage(
                      plan.reassign_existing
                        ? "Team locations reassigned."
                        : "Team locations assigned.",
                    ),
                  )
                  .catch(() => {});
              }}
            >
              {plan.reassign_existing
                ? "Apply reassignment"
                : "Apply distribution"}
            </button>
          </div>
        )}
      </div>
      <h3>Assigned locations</h3>
      <p className="meta">
        Choose a location manually, clear it, or swap two teams. Participants
        see these locations read-only.
      </p>
      {data.teams.length > 0 && (
        <label className="venue-team-search">
          Find a team or location
          <input
            type="search"
            value={query}
            placeholder="Team name, number or venue"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      )}
      {!data.teams.length ? (
        <p>Form teams first, then assign their locations.</p>
      ) : (
        <div className="table-scroll">
          <table className="venue-assignments">
            <caption className="sr-only">Current team locations</caption>
            <thead>
              <tr>
                <th>Team</th>
                <th>Location</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {!filteredTeams.length && (
                <tr>
                  <td colSpan={3}>No teams match this search.</td>
                </tr>
              )}
              {filteredTeams.map((team) => (
                <tr key={team.id}>
                  <th scope="row">
                    {team.name}
                    <small>
                      Team number: {team.id} · {team.members}{" "}
                      {team.members === 1 ? "person" : "people"}
                    </small>
                  </th>
                  <td>{label(team.location)}</td>
                  <td>
                    <button
                      type="button"
                      className="button secondary"
                      disabled={
                        busy ||
                        dirty ||
                        !data.can_assign ||
                        (!team.active && !team.location)
                      }
                      onClick={() => setEditing(team)}
                    >
                      {team.location ? "Change" : "Assign"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <AssignLocation
          key={editing.id}
          data={data}
          team={editing}
          busy={busy}
          label={label}
          onClose={() => setEditing(null)}
          onSave={async (place, swap) => {
            await act(`/admin/locations/teams/${editing.id}`, "PATCH", {
              revision: data.revision,
              venue_room_id: place?.venue_room_id ?? null,
              table_number: place?.table_number ?? null,
              swap,
            });
            setMessage("Team location saved.");
          }}
        />
      )}
    </div>
  );
}

function AssignLocation({
  busy,
  data,
  team,
  label,
  onSave,
  onClose,
}: {
  data: Locations;
  busy: boolean;
  team: LocatedTeam;
  label: (place: Place | null) => string;
  onSave: (place: Place | null, swap: boolean) => Promise<void>;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(
      team.location
        ? `${team.location.venue_room_id}:${team.location.table_number}`
        : "",
    ),
    [swap, setSwap] = useState(false);
  const [revision] = useState(data.revision);
  const values = selected.split(":").map(Number),
    place = selected
      ? { venue_room_id: values[0], table_number: values[1] }
      : null;
  const other =
    place &&
    data.teams.find(
      (t) =>
        t.id !== team.id &&
        t.location?.venue_room_id === place.venue_room_id &&
        t.location.table_number === place.table_number,
    );
  return (
    <ConfirmDialog
      label="Save team location"
      description={`Choose where ${team.name} will work. All teammates see the same location.`}
      onClose={onClose}
      disabled={
        revision !== data.revision ||
        (!team.active && !!place) ||
        (!!other && (!swap || !team.location))
      }
      onConfirm={() => onSave(place, !!other && swap)}
    >
      <p>
        Current location: <strong>{label(team.location)}</strong>
      </p>
      <label>
        Location
        <select
          disabled={busy}
          value={selected}
          onChange={(event) => {
            setSelected(event.target.value);
            setSwap(false);
          }}
        >
          <option value="">Not assigned</option>
          {data.rooms.map((room) => (
            <optgroup key={room.id} label={room.name}>
              {Array.from({ length: room.table_count }, (_, i) => i + 1)
                .filter((n) => !room.unavailable_tables.includes(n))
                .map((n) => {
                  const occupied = data.teams.find(
                    (t) =>
                      t.id !== team.id &&
                      t.location?.venue_room_id === room.id &&
                      t.location.table_number === n,
                  );
                  return (
                    <option
                      key={n}
                      value={`${room.id}:${n}`}
                      disabled={!!occupied && !team.location}
                    >{`${room.name} · Table ${n}${occupied ? ` · ${occupied.name}` : ""}`}</option>
                  );
                })}
            </optgroup>
          ))}
        </select>
      </label>
      {other && (
        <label className="venue-swap">
          <input
            type="checkbox"
            disabled={busy}
            checked={swap}
            onChange={(event) => setSwap(event.target.checked)}
          />
          Swap with {other.name}. They move to {label(team.location)}.
        </label>
      )}
      {revision !== data.revision && (
        <Notice>
          Teams or locations changed. Close this dialog and review the current
          assignments.
        </Notice>
      )}
    </ConfirmDialog>
  );
}
