import { useState } from "react";
import { dateTime } from "./api";
import {
  orderedTeams,
  rankingDuration,
  rankingOrder,
} from "./standings-presentation";
import { useResource } from "./hooks";
import type { StandingTeam, Standings, Viewer } from "./types";
import { Empty, Link, Notice } from "./ui";
import { StandingTable } from "./standing-table";
import { AwardRules } from "./award-rules";
import { RoomLeaders } from "./room-leaders";

type StandingRoom = Standings["rooms"][number];
export function StandingsPage({ viewer }: { viewer: Viewer }) {
  // Participant identity is supplied by the existing shared session, not a new poller.
  return (
    <ParticipantStandings
      key={`${viewer.role}-${viewer.id}-${viewer.team_id}`}
      viewer={viewer}
    />
  );
}
function ParticipantStandings({ viewer }: { viewer: Viewer }) {
  const state = useResource<Standings>("/standings", 10000);
  const participant = viewer.role === "participant";
  const requested = new URLSearchParams(location.search).get("view");
  const [view, setView] = useState<"team" | "room" | "overall">(
    requested === "overall" ? "overall" : participant ? "team" : "room",
  );
  const [roomId, setRoomId] = useState<number | null>(null);
  if (!state.data)
    return state.error ? (
      <Notice tone="error" retry={() => void state.refresh()}>
        {state.error}
      </Notice>
    ) : (
      <p role="status">Loading progress…</p>
    );
  const data = state.data;
  const room = data.rooms.find((r) => r.id === roomId) ?? data.rooms[0];
  const own = participant
    ? data.teams.find((t) => t.id === viewer.team_id)
    : undefined;
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Progress & standings</h1>
          <p className="lede">
            {data.event.results_published
              ? "Final results, published by the organizers."
              : "Follow your progress. Compare a room or explore the overall standings when you want."}
          </p>
        </div>
        <Link className="button secondary" href="/quest/stage">
          Open projector
        </Link>
      </div>
      {state.error && (
        <Notice tone="error" retry={() => void state.refresh()}>
          Showing saved progress. {state.error}
        </Notice>
      )}
      {!data.event.results_published && (
        <p className="results-status">
          {data.event.status === "closed"
            ? "Results under review"
            : data.event.status === "draft"
              ? "Event preparation"
              : data.event.status === "paused"
                ? "Event paused · provisional results"
                : "Provisional results"}
        </p>
      )}
      <nav
        className="room-tabs progress-views"
        aria-label="Progress and comparison"
      >
        {participant && (
          <button
            className={view === "team" ? "active" : ""}
            aria-pressed={view === "team"}
            onClick={() => setView("team")}
          >
            Your team
          </button>
        )}
        <button
          className={view === "room" ? "active" : ""}
          aria-pressed={view === "room"}
          onClick={() => setView("room")}
        >
          Compare a room
        </button>
        <button
          className={view === "overall" ? "active" : ""}
          aria-pressed={view === "overall"}
          onClick={() => setView("overall")}
        >
          Overall standings
        </button>
      </nav>
      {view === "team" ? (
        own ? (
          <YourTeam
            data={data}
            team={own}
            compare={(id) => {
              setRoomId(id);
              setView("room");
            }}
          />
        ) : (
          <Empty title="Your team’s progress will appear here">
            {viewer.team_id
              ? "Your team is not included in the released standings. Ask an organizer if you expected to see it."
              : "Your team will appear after the organizers release the assignments."}
          </Empty>
        )
      ) : (
        <>
          {view === "room" &&
            (room ? (
              <>
                <label className="field standings-room-picker">
                  <span>Compare a room</span>
                  <select
                    value={room.id}
                    onChange={(e) => setRoomId(Number(e.target.value))}
                  >
                    {data.rooms.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </label>
                <RoomLeaders data={data} room={room} />
              </>
            ) : (
              <Empty title="No rooms configured">
                The organizers are preparing the event.
              </Empty>
            ))}
          {(view === "overall" || room) && (
            <>
              <p className="standings-order">
                {rankingOrder(data, view === "room")}
              </p>
              {!data.teams.length ? (
                <Empty title="The standings are waiting">
                  Teams appear after roster release. A first qualifying solve
                  gives a team a prize rank.
                </Empty>
              ) : (
                <div className="standings-scroll">
                  <StandingTable
                    data={data}
                    teams={orderedTeams(
                      data.teams,
                      view === "room" ? room : undefined,
                    )}
                    room={view === "room" ? room : undefined}
                    overall={view === "overall"}
                    ownTeamId={own?.id}
                  />
                </div>
              )}
            </>
          )}
        </>
      )}
      <p className="meta updated">
        Updated {dateTime(data.as_of)} ·{" "}
        {data.event.results_published
          ? "Final snapshot"
          : "Provisional results"}
      </p>
      <AwardRules data={data} />
    </>
  );
}
function YourTeam({
  data,
  team,
  compare,
}: {
  data: Standings;
  team: StandingTeam;
  compare: (id: number) => void;
}) {
  const points = data.scoring_version === 2;
  const completed = team.room_progress.filter(
    (p) => p.total > 0 && p.complete,
  ).length;
  return (
    <section className="personal-progress" aria-label="Your team progress">
      <header>
        <h2>{team.name}</h2>
        <p>
          {team.total} challenges solved · {completed}{" "}
          {completed === 1 ? "room" : "rooms"} completed
        </p>
      </header>
      {points && (
        <dl className="personal-score">
          <div>
            <dt>Net points</dt>
            <dd>{(team.points ?? 0).toLocaleString()}</dd>
          </div>
          <div>
            <dt>Challenge points earned</dt>
            <dd>{(team.gross_points ?? 0).toLocaleString()}</dd>
          </div>
          <div>
            <dt>Spent on hints</dt>
            <dd>{(team.hint_costs ?? 0).toLocaleString()}</dd>
          </div>
        </dl>
      )}
      <p className="award-explanation">
        Each room has its own award. Every qualifying solve also{" "}
        {points ? "earns points toward" : "contributes to"} the overall award.
      </p>
      <div className="personal-room-grid">
        {data.rooms.map((room) => {
          const p = team.room_progress.find((p) => p.room_id === room.id);
          return (
            <article key={room.id} className="personal-room">
              <header>
                <h3>{room.name}</h3>
                <span>
                  {room.total > 0 && p?.complete
                    ? "Completed"
                    : p?.solved
                      ? "In progress"
                      : "No counted solves yet"}
                </span>
              </header>
              <p className="personal-solved">
                <strong>{p?.solved ?? 0}</strong> / {room.total} solved
              </p>
              <progress
                value={p?.solved ?? 0}
                max={Math.max(1, room.total)}
                aria-label={`${room.name} progress`}
              />
              <dl>
                <div>
                  <dt>{points ? "Net room points" : "Challenges solved"}</dt>
                  <dd>
                    {points
                      ? (p?.points ?? 0).toLocaleString()
                      : (p?.solved ?? 0)}
                  </dd>
                </div>
                {points && (
                  <div>
                    <dt>Spent on hints</dt>
                    <dd>{(p?.hint_costs ?? 0).toLocaleString()}</dd>
                  </div>
                )}
                <div>
                  <dt>Active time to last solve</dt>
                  <dd>{rankingDuration(p?.milestone_elapsed_ms)}</dd>
                </div>
              </dl>
              <div className="personal-room-actions">
                <Link href={`/quest/rooms/${encodeURIComponent(room.slug)}`}>
                  Open room
                </Link>
                <button
                  className="text-button"
                  onClick={() => compare(room.id)}
                >
                  Compare this room
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
