import type { TeamLocation } from "./types";

export function TeamNumber({
  number,
  location,
}: {
  number: number | null;
  location?: TeamLocation | null;
}) {
  if (number === null || !Number.isSafeInteger(number) || number <= 0)
    return null;
  return (
    <span
      className="team-identity"
      role="group"
      aria-label="Team number and location"
    >
      <span className="team-number">
        Team number: <strong>{number}</strong>
      </span>
      <span className="team-location">
        Location: <strong>{location?.label || "Not assigned yet"}</strong>
      </span>
    </span>
  );
}
