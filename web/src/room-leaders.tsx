import type { StandingTeam, Standings } from "./types";
import { orderedTeams, rankingDuration } from "./standings-presentation";
import { pageItems } from "./projector-rotation";

type StandingRoom = Standings["rooms"][number];
export function RoomLeaders({
  data,
  room,
  stage = false,
  elapsed = 0,
  places = 3,
}: {
  data: Standings;
  room: StandingRoom;
  stage?: boolean;
  elapsed?: number;
  places?: number;
}) {
  const leaders = orderedTeams(data.teams, room).filter((t) => {
    const rank = t.room_progress.find((p) => p.room_id === room.id)?.rank;
    return rank != null && rank <= places;
  });
  const completed = data.teams.filter(
    (t) =>
      room.total > 0 &&
      t.room_progress.some((p) => p.room_id === room.id && p.complete),
  ).length;
  const page = pageItems(leaders, 3, elapsed);
  const shown = stage ? page.items : leaders.slice(0, 3);
  return (
    <section
      className={`room-leaders${stage ? " projected-room" : ""}`}
      aria-label={`${room.name} current leaders`}
    >
      <header>
        <h2 title={room.name}>{room.name}</h2>
        <p>
          {room.total === 0
            ? "Challenges being prepared"
            : `${completed} ${completed === 1 ? "team has" : "teams have"} completed this room`}
        </p>
        {room.pause?.paused && (
          <p className="room-pause-summary">
            <strong>Room paused</strong>
            {room.pause.reason ? ` · ${room.pause.reason}` : ""}
          </p>
        )}
      </header>
      <p className="leader-caption">
        {data.event.results_published ? "Final standings" : "Current leaders"} ·
        {places === 1
          ? "room award, including joint winners"
          : "leading three ranks, including ties"}
      </p>
      {leaders.length ? (
        <>
          <LeaderRows
            teams={shown}
            room={room}
            points={data.scoring_version === 2}
          />
          {!stage && leaders.length > 3 && (
            <details className="additional-leaders">
              <summary>
                Show {leaders.length - 3} more leading teams, including ties
              </summary>
              <LeaderRows
                teams={leaders.slice(3)}
                room={room}
                points={data.scoring_version === 2}
              />
            </details>
          )}
        </>
      ) : (
        <p className="leader-empty">
          A first qualifying solve sets the benchmark.
        </p>
      )}
      {stage && page.pages > 1 && (
        <p className="leader-page">
          Leaders {page.page * 3 + 1}–
          {Math.min((page.page + 1) * 3, leaders.length)} of {leaders.length} ·
          shared ranks included
        </p>
      )}
    </section>
  );
}
function LeaderRows({
  teams,
  room,
  points,
}: {
  teams: StandingTeam[];
  room: StandingRoom;
  points: boolean;
}) {
  return (
    <ol className="leader-list">
      {teams.map((team) => {
        const p = team.room_progress.find((p) => p.room_id === room.id)!;
        return (
          <li key={team.id} value={p.rank ?? 1}>
            <span className="leader-name" title={team.name}>
              {team.name}
            </span>
            <div className="leader-metrics">
              <strong>
                {p.solved}/{room.total} solved
              </strong>
              {points && (
                <span>{(p.points ?? 0).toLocaleString()} net pts</span>
              )}
              <span>{rankingDuration(p.milestone_elapsed_ms)} active</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
export function RoomOverview({
  data,
  elapsed = 0,
}: {
  data: Standings;
  elapsed?: number;
}) {
  const pages = pageItems(data.rooms, 6, elapsed, 20);
  return (
    <div className="room-overview">
      <p className="award-explanation">
        Each room has its own award. Every qualifying solve also{" "}
        {data.scoring_version === 2 ? "earns points toward" : "contributes to"}{" "}
        the overall award.
      </p>
      {!data.rooms.length ? (
        <p className="leader-empty">The organizers are preparing the rooms.</p>
      ) : (
        <div className="room-leader-grid">
          {pages.items.map((room) => (
            <RoomLeaders
              key={room.id}
              data={data}
              room={room}
              stage
              elapsed={pages.visibleElapsed}
            />
          ))}
        </div>
      )}
      {pages.pages > 1 && (
        <p className="overview-page">
          Rooms {pages.page * 6 + 1}–
          {Math.min((pages.page + 1) * 6, data.rooms.length)} of{" "}
          {data.rooms.length}
        </p>
      )}
    </div>
  );
}
