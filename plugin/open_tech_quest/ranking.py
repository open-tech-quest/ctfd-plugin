"""Pure UTC time and competition-rank rules, shared by every presentation."""
def active_ms(origin, milestone, pauses):
    if origin is None or milestone is None:
        return None
    if milestone <= origin:
        return 0
    elapsed = (milestone - origin).total_seconds()
    intervals = []
    for start, end in pauses:
        left, right = max(start, origin), min(end or milestone, milestone)
        if right > left:
            intervals.append((left, right))
    # Room and event interruptions may overlap; remove their union exactly once.
    merged = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    elapsed -= sum((right - left).total_seconds() for left, right in merged)
    return max(0, round(elapsed * 1000))


def prefix(ids, solves):
    result = []
    for challenge_id in ids:
        if challenge_id not in solves:
            break
        result.append(solves[challenge_id])
    return result


def assign_ranks(rows, count_key, time_key):
    """Competition ranks preserve genuine ties; zero solves is ineligible."""
    rows.sort(key=lambda row: (-row[count_key], row[time_key] if row[time_key] is not None else float("inf"), row.get("name", ""), row.get("id", 0)))
    last, rank = None, None
    for index, row in enumerate(rows, 1):
        key = (row[count_key], row[time_key])
        if key != last:
            rank = index
        row["rank"] = rank if row[count_key] else None
        last = key
    return rows


def assign_point_ranks(rows, time_key, room=False):
    """Progress precedes points inside rooms; overall compares net points."""
    def sporting(row):
        return ((-row["solved"],) if room else ()) + (
            -row["points"], row[time_key] if row[time_key] is not None else float("inf"))
    # Ineligible rows cannot consume a sporting rank, including negative scores.
    rows.sort(key=lambda r: (not bool(r["solved"] if room else r["total"]),
                            *sporting(r), r.get("name", ""), r.get("id", 0)))
    last, rank, eligible = None, None, 0
    for row in rows:
        if not (row["solved"] if room else row["total"]):
            row["rank"] = None
            continue
        eligible += 1
        key = sporting(row)
        if key != last:
            rank = eligible
        row["rank"], last = rank, key
    return rows
