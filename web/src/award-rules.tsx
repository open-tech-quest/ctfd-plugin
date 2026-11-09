import type { Standings } from "./types";
export function AwardRules({ data }: { data: Standings }) {
  return (
    <section className="award-rules">
      <h2>How the awards work</h2>
      <div>
        <article>
          <h3>Each room</h3>
          <p>
            {data.scoring_version === 2
              ? "Most challenges solved comes first, then room points minus that room’s hint costs, then active time to the last counted solve. A full clear leads every partial clear."
              : "Teams rank by the longest consecutively solved challenge path, then by active time to their last qualifying solve. A completed room leads every incomplete path."}
          </p>
          <p className="meta">
            Room time starts{" "}
            {data.event.room_clock === "event"
              ? "at the common event start"
              : "when your team starts the room"}
            .
          </p>
        </article>
        <article>
          <h3>Overall</h3>
          <p>
            {data.scoring_version === 2
              ? "Challenge points across all rooms, minus paid hints, come first. Active time to the last counted solve breaks ties. Simultaneous room clocks are never added together."
              : "Distinct challenge solves come first, then active time to the last qualifying solve. Simultaneous room clocks are never added together."}
          </p>
          <p className="meta">
            Overall time starts{" "}
            {data.event.overall_clock === "team"
              ? "at your team’s first room start"
              : "at the common event start"}
            .
          </p>
        </article>
        <article>
          <h3>Fair results</h3>
          <p>
            Organizer pauses are excluded. No qualifying solves means no prize
            eligibility. Genuine ties share the same rank. Published results are
            a fixed final snapshot.
          </p>
          <p>
            Only eligible contributions count. Banned accounts' contributions
            are excluded, and teammates can re-solve affected challenges.
            Unbanning restores eligibility of retained contributions without
            counting a challenge twice.
          </p>
        </article>
      </div>
    </section>
  );
}
