"""Add newcomers without moving any existing participant."""
import heapq
import math


def placement(user_ids, teams, capacity, reserved_names):
    if capacity < 1:
        raise ValueError("Team capacity must be positive")
    groups = []
    heap = []
    for team in sorted(teams, key=lambda t: t['id']):
        count = len(team['member_ids'])
        if count < capacity:
            groups.append({'team_id': team['id'], 'name': team['name'], 'user_ids': []})
            heapq.heappush(heap, (count, team['id'], len(groups) - 1))
    remaining = []
    for uid in user_ids:
        if not heap:
            remaining.append(uid)
            continue
        count, tid, index = heapq.heappop(heap)
        groups[index]['user_ids'].append(uid)
        if count + 1 < capacity:
            heapq.heappush(heap, (count + 1, tid, index))
    groups = [g for g in groups if g['user_ids']]
    count = math.ceil(len(remaining) / capacity)
    names = {name.casefold() for name in reserved_names}
    number, offset = 1, 0
    for index in range(count):
        while f'Team {number}'.casefold() in names:
            number += 1
        name = f'Team {number}'
        names.add(name.casefold())
        size = len(remaining) // count + (index < len(remaining) % count)
        groups.append({'team_id': None, 'name': name,
                       'user_ids': remaining[offset:offset + size]})
        offset += size
    return groups
