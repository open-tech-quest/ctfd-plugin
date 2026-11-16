"""Administrator-only, reviewed venue allocation; participant reads are own-team only."""
import hashlib
import hmac
import json

from flask import Blueprint
from CTFd.models import Teams, Users, db
from .api import admin, body, event, fail, success, audit
from .api import QuestError, error, http_error, conflict
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import IntegrityError
from .location_models import QuestTeamLocation, QuestVenueRoom
from .models import QuestAudit, QuestMaintainer

locations_api = Blueprint('quest_locations', __name__)
locations_api.register_error_handler(QuestError, error)
locations_api.register_error_handler(HTTPException, http_error)
locations_api.register_error_handler(IntegrityError, conflict)


@locations_api.after_request
def private(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def _value(assignment, name):
    return {'venue_room_id': assignment.venue_room_id, 'room_name': name,
            'table_number': assignment.table_number,
            'label': f'{name} · Table {assignment.table_number}'}


def location_map():
    """Bulk primitive projection for administrator/report callers only."""
    return {assignment.team_id: _value(assignment, name)
            for assignment, name in db.session.query(QuestTeamLocation, QuestVenueRoom.name)
            .join(QuestVenueRoom, QuestVenueRoom.id == QuestTeamLocation.venue_room_id)}


def location(team_id):
    if not team_id:
        return None
    row = (db.session.query(QuestTeamLocation, QuestVenueRoom.name)
           .join(QuestVenueRoom, QuestVenueRoom.id == QuestTeamLocation.venue_room_id)
           .filter(QuestTeamLocation.team_id == team_id).first())
    if row is None:
        return None
    return _value(*row)


def snapshot(e):
    rooms = [{'id': r.id, 'name': r.name, 'position': r.position,
              'table_count': r.table_count, 'unavailable_tables': r.unavailable_tables}
             for r in QuestVenueRoom.query.order_by(QuestVenueRoom.position, QuestVenueRoom.id)]
    assigned = {r.team_id: {'venue_room_id': r.venue_room_id, 'table_number': r.table_number}
                for r in QuestTeamLocation.query.all()}
    # Same eligibility as onboarding.competing_teams, projected in bulk so the
    # admin workspace does not issue a membership/role query for every team.
    active = {tid for (tid,) in db.session.query(Teams.id).join(Users, Users.team_id == Teams.id)
              .outerjoin(QuestMaintainer, QuestMaintainer.user_id == Users.id)
              .filter(Teams.banned.is_(False), Teams.hidden.is_(False), Users.type == 'user',
                      Users.banned.is_(False), QuestMaintainer.user_id.is_(None)).distinct()}
    members = dict(db.session.query(Users.team_id, db.func.count(Users.id)).group_by(Users.team_id).all())
    teams = [{'id': t.id, 'name': t.name, 'members': members.get(t.id, 0), 'active': t.id in active,
              'location': assigned.get(t.id)} for t in Teams.query.order_by(Teams.id)]
    epoch = db.session.query(db.func.max(QuestAudit.id)).filter_by(action='event.new_round').scalar()
    revision = hashlib.sha256(json.dumps([e.status, epoch, rooms, teams], sort_keys=True).encode()).hexdigest()
    return {'rooms': rooms, 'teams': teams, 'revision': revision,
            'can_edit_layout': e.status == 'draft', 'can_assign': e.status in ('draft', 'live', 'paused')}


def reviewed(data, saved, fields, expected_revision=None):
    if set(data) != set(fields) | {'revision'}:
        fail('Use the current location form and supply all required fields', 400)
    supplied = data['revision']
    expected = saved['revision'] if expected_revision is None else expected_revision
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), expected.encode()):
        fail('Teams or locations changed. Refresh and review the current assignments.', 409)


def number(value, label, maximum=500):
    if type(value) is not int or not 1 <= value <= maximum:
        fail(f'{label} must be a whole number from 1 to {maximum}', 400)
    return value


def available(room):
    return set(range(1, room['table_count'] + 1)) - set(room['unavailable_tables'])


@locations_api.route('/admin/locations')
@admin
def index():
    return success(snapshot(event()))


@locations_api.route('/admin/locations/layout', methods=['PUT'])
@admin
def save_layout():
    data, e = body(), event(lock=True)
    saved = snapshot(e)
    reviewed(data, saved, {'rooms'})
    if not saved['can_edit_layout']:
        fail('Venue layout can be changed throughout Draft. Existing locations can still be moved during play.', 409)
    proposed = data['rooms']
    if not isinstance(proposed, list) or len(proposed) > 50:
        fail('Configure up to 50 venue rooms', 400)
    existing = {r.id: r for r in QuestVenueRoom.query.all()}
    names, ids, validated = set(), set(), []
    for item in proposed:
        if not isinstance(item, dict) or set(item) != {'id', 'name', 'table_count', 'unavailable_tables'}:
            fail('Each venue room needs id, name, table_count and unavailable_tables', 400)
        rid, name = item['id'], item['name']
        if rid is not None and (type(rid) is not int or rid not in existing or rid in ids):
            fail('A venue room changed. Reload the layout.', 409)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128 or name.strip().casefold() in names:
            fail('Venue rooms need unique names of 1–128 characters', 400)
        count = number(item['table_count'], 'Table count')
        blocked = item['unavailable_tables']
        if not isinstance(blocked, list) or any(type(n) is not int or not 1 <= n <= count for n in blocked) or len(blocked) != len(set(blocked)):
            fail('Unavailable tables must be distinct numbers within this room', 400)
        names.add(name.strip().casefold())
        if rid is not None:
            ids.add(rid)
        validated.append((rid, name.strip(), count, sorted(blocked)))
    desired = {rid: (count, blocked) for rid, _, count, blocked in validated if rid is not None}
    for assigned in QuestTeamLocation.query.all():
        limits = desired.get(assigned.venue_room_id)
        if limits is None or assigned.table_number > limits[0] or assigned.table_number in limits[1]:
            fail('Move or clear teams from affected tables before removing a room, reducing its table count or marking its tables unavailable.', 409)
    for rid in set(existing) - ids:
        db.session.delete(existing[rid])
    for position, (rid, name, count, blocked) in enumerate(validated):
        room = existing[rid] if rid else QuestVenueRoom()
        room.name, room.position, room.table_count, room.unavailable_tables = name, position, count, blocked
        db.session.add(room)
    audit('locations.layout', {'room_count': len(validated)})
    db.session.commit()
    return success(snapshot(e))


@locations_api.route('/admin/locations/teams/<int:team_id>', methods=['PATCH'])
@admin
def assign(team_id):
    data, e = body(), event(lock=True)
    saved = snapshot(e)
    reviewed(data, saved, {'venue_room_id', 'table_number', 'swap'})
    if not saved['can_assign']:
        fail('Locations can be assigned in Draft, Live or Paused.', 409)
    if type(data['swap']) is not bool:
        fail('Confirm whether to swap occupied locations', 400)
    team = next((t for t in saved['teams'] if t['id'] == team_id), None)
    if team is None:
        fail('Team not found', 404)
    rid, table = data['venue_room_id'], data['table_number']
    current = QuestTeamLocation.query.get(team_id)
    before = team['location']
    if rid is None:
        if table is not None or data['swap']:
            fail('Clearing a location needs an empty room and table', 400)
        if current:
            db.session.delete(current)
    else:
        if not team['active']:
            fail('Assign locations to occupied, visible, non-banned participant teams', 409)
        room = next((r for r in saved['rooms'] if type(rid) is int and r['id'] == rid), None)
        if room is None or type(table) is not int or table not in available(room):
            fail('Choose an available table in a configured venue room', 400)
        other = QuestTeamLocation.query.filter_by(venue_room_id=rid, table_number=table).first()
        if other and other.team_id != team_id:
            if not data['swap'] or current is None:
                fail('This table is occupied. Choose a free table, or explicitly swap two assigned teams.', 409)
            other_id, old_room, old_table = other.team_id, current.venue_room_id, current.table_number
            # Delete/flush avoids a transient unique conflict while swapping.
            # All changes and audit still roll back together on any failure.
            db.session.delete(other)
            db.session.delete(current)
            db.session.flush()
            db.session.add_all([QuestTeamLocation(team_id=team_id, venue_room_id=rid, table_number=table),
                                QuestTeamLocation(team_id=other_id, venue_room_id=old_room, table_number=old_table)])
            audit('locations.swap', {'team_id': team_id, 'other_team_id': other_id})
        elif other is None or other.team_id != team_id:
            if current:
                current.venue_room_id, current.table_number = rid, table
            else:
                db.session.add(QuestTeamLocation(team_id=team_id, venue_room_id=rid, table_number=table))
    audit('locations.assign', {'team_id': team_id, 'venue_room_id': rid, 'table_number': table, 'previous': before})
    db.session.commit()
    return success(snapshot(e))


def distribution(saved, strategy, reassign_existing=False):
    if strategy not in ('spread', 'fill'):
        fail('Choose spread across rooms or fill rooms in order', 400)
    free = {r['id']: available(r) for r in saved['rooms']}
    counts = {r['id']: 0 for r in saved['rooms']}
    pending, plan = [], []
    for team in saved['teams']:
        assigned = team['location']
        if assigned and not (reassign_existing and team['active']):
            free[assigned['venue_room_id']].discard(assigned['table_number'])
            counts[assigned['venue_room_id']] += 1
        elif team['active']:
            pending.append(team)
    shortage = max(0, len(pending) - sum(len(tables) for tables in free.values()))
    for team in pending:
        candidates = [r for r in saved['rooms'] if free[r['id']]]
        if not candidates:
            break
        room = min(candidates, key=lambda r: (counts[r['id']] if strategy == 'spread' else 0, r['position'], r['id']))
        rid = room['id']
        table = min(free[rid])
        free[rid].remove(table)
        counts[rid] += 1
        previous = team['location']
        previous_room = next((r['name'] for r in saved['rooms'] if previous and r['id'] == previous['venue_room_id']), None)
        plan.append({'team_id': team['id'], 'team_name': team['name'], 'venue_room_id': rid,
                     'room_name': room['name'], 'table_number': table,
                     'previous_label': f"{previous_room} · Table {previous['table_number']}" if previous else 'Not assigned'})
    return {'strategy': strategy, 'assignments': plan, 'shortage': shortage,
            'reassign_existing': reassign_existing,
            'preserved': sum(bool(t['location']) and not (reassign_existing and t['active']) for t in saved['teams']),
            'occupancy': [{'venue_room_id': r['id'], 'name': r['name'], 'teams': counts[r['id']],
                           'capacity': len(available(r))} for r in saved['rooms']]}


@locations_api.route('/admin/locations/distribution', methods=['POST'])
@admin
def distribute():
    data, e = body(), event(lock=True)
    saved = snapshot(e)
    if not saved['can_assign']:
        fail('Locations can be assigned in Draft, Live or Paused.', 409)
    if type(data.get('apply')) is not bool or type(data.get('reassign_existing')) is not bool:
        fail('Choose preview/apply and whether to reassign existing locations', 400)
    if data['reassign_existing'] and e.status != 'draft':
        fail('Full redistribution is available throughout Draft. During play, move teams individually or assign unplaced teams.', 409)
    plan = distribution(saved, data.get('strategy'), data['reassign_existing'])
    # Bind confirmation to this strategy/scope as well as current source state.
    plan_revision = hashlib.sha256(json.dumps([saved['revision'], data['strategy'], data['reassign_existing']]).encode()).hexdigest()
    reviewed(data, saved, {'strategy', 'apply', 'reassign_existing'}, plan_revision if data['apply'] else saved['revision'])
    if not data['apply']:
        return success(dict(plan, revision=plan_revision, source_revision=saved['revision']))
    if plan['shortage']:
        fail(f"Not enough tables: {plan['shortage']} more needed. Add capacity or move/clear assignments before applying.", 409)
    if data['reassign_existing']:
        ids = [row['team_id'] for row in plan['assignments']]
        QuestTeamLocation.query.filter(QuestTeamLocation.team_id.in_(ids)).delete(synchronize_session='fetch')
        db.session.flush()
    for row in plan['assignments']:
        db.session.add(QuestTeamLocation(team_id=row['team_id'], venue_room_id=row['venue_room_id'], table_number=row['table_number']))
    audit('locations.distribute', {'strategy': data['strategy'], 'reassign_existing': data['reassign_existing'], 'team_ids': [r['team_id'] for r in plan['assignments']]})
    db.session.commit()
    return success(snapshot(e))
