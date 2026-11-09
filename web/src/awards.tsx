import type { Standings } from "./types";
import { rankingDuration, orderedTeams } from "./standings-presentation";
import { pageItems } from "./projector-rotation";
import { RoomLeaders } from "./room-leaders";
export function Awards({
  data,
  elapsed = 0,
}: {
  data: Standings;
  elapsed?: number;
}) {
  const winners = orderedTeams(data.teams).filter(
    (t) => t.rank === 1 && t.total > 0,
  );
  const overall = pageItems(winners, 1, elapsed);
  const team = overall.items[0];
  const rooms = pageItems(data.rooms, 3, elapsed, 20);
  return (
    <div className="stage-awards">
      <section className="overall-award">
        <span>
          {data.scoring_version === 2
            ? "Overall · most net points"
            : "Overall quest"}
        </span>
        <h2 title={team?.name}>{team?.name ?? "No qualifying solves"}</h2>
        {team && (
          <p>
            {data.scoring_version === 2
              ? `${(team.points ?? 0).toLocaleString()} net points`
              : `${team.total} challenges solved`}{" "}
            · {rankingDuration(team.overall_elapsed_ms)} active time
          </p>
        )}
        {winners.length > 1 && (
          <p>
            Shared first place · team {overall.page + 1} of {winners.length}
          </p>
        )}
      </section>
      <div className="room-leader-grid">
        {rooms.items.map((room) => (
          <RoomLeaders
            key={room.id}
            data={data}
            room={room}
            stage
            places={1}
            elapsed={rooms.visibleElapsed}
          />
        ))}
      </div>
      {rooms.pages > 1 && (
        <p className="overview-page">
          Room awards {rooms.page * 3 + 1}–
          {Math.min((rooms.page + 1) * 3, data.rooms.length)} of{" "}
          {data.rooms.length}
        </p>
      )}
    </div>
  );
}
