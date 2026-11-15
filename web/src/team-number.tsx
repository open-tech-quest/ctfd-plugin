export function TeamNumber({ number }: { number: number | null }) {
  if (number === null || !Number.isSafeInteger(number) || number <= 0)
    return null;
  return (
    <span className="team-number">
      Team number: <strong>{number}</strong>
    </span>
  );
}
