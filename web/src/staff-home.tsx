import type { Bootstrap } from "./types";
import { StartParticipantPreview } from "./preview";
import { previewRoom } from "./preview-routing";
import { Arrow, Link, Notice } from "./ui";
export function StaffHome({ data }: { data: Bootstrap }) {
  const viewer = data.viewer;
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {viewer.admin ? "Administrator" : "Challenge maintainer"}
          </p>
          <h1>Build the quest experience</h1>
          <p className="lede">
            {viewer.admin
              ? "Prepare rooms, manage accounts and organize the event from your staff workspace."
              : "Prepare room content, challenge paths and accepted answers in your room workspace."}
          </p>
        </div>
      </div>
      <section className="staff-role-card">
        <div>
          <h2>
            {viewer.admin
              ? "Your organizer workspace"
              : "Your room management access"}
          </h2>
          <p>
            You're signed in as {viewer.name}. Staff accounts prepare the quest
            and stay outside participant attendance, teams and awards.
          </p>
        </div>
        {viewer.can_manage_rooms ? (
          <Link className="button" href="/quest/admin">
            Open Organize <Arrow />
          </Link>
        ) : (
          <Notice>
            Room management is not available for this account. An administrator
            can check its access.
          </Notice>
        )}
      </section>
      {viewer.can_manage_rooms && (
        <section className="staff-participant-preview">
          <h2>Play the participant journey</h2>
          <p>
            Walk through the rooms and check saved challenge answers as an
            example team. Preview progress stays in this tab; leaving or
            refreshing clears it.
          </p>
          <StartParticipantPreview />
        </section>
      )}
      {viewer.can_manage_rooms && (
        <section className="staff-room-preview">
          <h2>Preview a room</h2>
          <p className="meta">
            Open a room in the participant preview with local progress.
          </p>
          <div>
            {data.rooms.map((room) => (
              <Link key={room.id} href={previewRoom(room.slug)}>
                {room.name}
                <Arrow />
              </Link>
            ))}
          </div>
          {!data.rooms.length && (
            <p className="meta">
              No rooms yet. Add the first room in Organize.
            </p>
          )}
        </section>
      )}
    </>
  );
}
