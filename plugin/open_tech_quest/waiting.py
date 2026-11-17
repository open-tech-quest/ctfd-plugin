"""Reviewed, add-only admission with a frozen queue and scoped stale checks."""
import hashlib
import hmac
import json
from collections import defaultdict
from datetime import timedelta

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer
from CTFd.models import Awards, Ratings, Submissions, Teams, Unlocks, Users, db
from . import lifecycle, onboarding
from .locations import location_map
from .models import QuestAudit, QuestExclusion, QuestMaintainer, QuestReadiness
from .recovery_models import QuestRecoverySolve
from .service import iso, now
from .waiting_math import placement

MAX_BATCH = 1000
REVIEW_SECONDS = 900


def serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],
                                  salt='quest-waiting-participants-v1',
                                  signer_kwargs={'digest_method': hashlib.sha256})


def require_available(e):
    blocked = onboarding.late_admission_reason(e)
    if blocked:
        raise onboarding.OnboardingError(blocked)


def waiting_users(ids=None):
    query = Users.query.filter(Users.type == 'user', Users.banned.is_(False), Users.team_id.is_(None))
    query = query.filter(~db.session.query(QuestMaintainer.user_id).filter(QuestMaintainer.user_id == Users.id).exists())
    if onboarding.require_attendance():
        query = query.filter(db.session.query(QuestReadiness.user_id).filter(QuestReadiness.user_id == Users.id).exists())
    for model in (Submissions, Awards, Unlocks, Ratings, QuestRecoverySolve):
        query = query.filter(~db.session.query(model.user_id).filter(model.user_id == Users.id).exists())
    if ids is not None:
        query = query.filter(Users.id.in_(ids))
    return query.order_by(Users.id).limit(MAX_BATCH).all()


def targets(ids=None):
    """Occupied, eligible competing teams; bulk reads avoid per-member queries."""
    query = Teams.query.filter(Teams.hidden.is_(False), Teams.banned.is_(False))
    query = query.filter(~db.session.query(QuestExclusion.team_id).filter(QuestExclusion.team_id == Teams.id).exists())
    if ids is not None:
        query = query.filter(Teams.id.in_(ids))
    teams = query.order_by(Teams.id).all()
    members = defaultdict(list)
    for user in Users.query.filter(Users.team_id.in_([t.id for t in teams])).order_by(Users.id):
        members[user.team_id].append(user)
    staff = {uid for uid, in db.session.query(QuestMaintainer.user_id)}
    required = onboarding.require_attendance()
    ready = {uid for uid, in db.session.query(QuestReadiness.user_id)} if required else set()
    locations = location_map()
    result = {}
    for team in teams:
        people = members[team.id]
        if not people or any(u.type != 'user' or u.banned or u.id in staff or (required and u.id not in ready) for u in people):
            continue
        result[team.id] = {'id': team.id, 'name': team.name,
                           'member_ids': [u.id for u in people],
                           'identities': [lifecycle.identity(u) for u in people],
                           'location': locations.get(team.id)}
    return result


def guard(e, users, existing):
    state = onboarding.settings()
    ready = {r.user_id: iso(r.ready_at) for r in QuestReadiness.query.filter(
        QuestReadiness.user_id.in_([u.id for u in users]))} if onboarding.require_attendance() else {}
    epoch = db.session.query(db.func.max(QuestAudit.id)).filter_by(action='event.new_round').scalar()
    data = {'context': [e.id, e.status, iso(e.start_at), iso(e.end_at), e.room_clock, e.overall_clock,
                        state.phase, state.team_size, state.allow_team_naming,
                        onboarding.require_attendance(), iso(state.finalized_at), epoch],
            'users': [(u.id, lifecycle.identity(u), u.name, ready.get(u.id)) for u in users],
            'teams': [existing[tid] for tid in sorted(existing)]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preview(e):
    require_available(e)
    users = waiting_users()
    existing = targets()
    capacity = onboarding.settings().team_size
    seed = guard(e, users, {})
    order = onboarding.random_order([u.id for u in users], seed)
    groups = placement(order, list(existing.values()), capacity,
                       [name for name, in db.session.query(Teams.name)])
    affected = {g['team_id']: existing[g['team_id']] for g in groups if g['team_id'] is not None}
    actor = lifecycle.current_user()
    signed = {'version': 1, 'actor': [actor.id, lifecycle.identity(actor)],
              'user_ids': [u.id for u in users], 'groups': groups,
              'guard': guard(e, users, affected)}
    return {'token': serializer().dumps(signed) if users else '',
            'participants': [{'id': u.id, 'name': u.name} for u in users],
            'groups': [dict(g,
                            existing_user_ids=affected[g['team_id']]['member_ids'] if g['team_id'] else [],
                            location=affected[g['team_id']]['location'] if g['team_id'] else None)
                       for g in groups],
            'capacity': capacity, 'max_batch': MAX_BATCH, 'event_status': e.status,
            'expires_at': iso(now() + timedelta(seconds=REVIEW_SECONDS))}


def apply(e, token):
    require_available(e)
    if not isinstance(token, str) or not token or len(token) > 200000:
        raise onboarding.OnboardingError('Build a placement preview before applying.', 400)
    try:
        plan = serializer().loads(token, max_age=REVIEW_SECONDS)
    except BadData:
        raise onboarding.OnboardingError('This placement preview expired or is invalid. Build a fresh preview.') from None
    actor = lifecycle.current_user()
    if plan.get('version') != 1 or plan.get('actor') != [actor.id, lifecycle.identity(actor)]:
        raise onboarding.OnboardingError('Review this placement using your current administrator account.')
    users = waiting_users(plan['user_ids'])
    if [u.id for u in users] != plan['user_ids']:
        raise onboarding.OnboardingError('A reviewed participant is no longer eligible or unassigned. Build a fresh preview.')
    ids = [g['team_id'] for g in plan['groups'] if g['team_id'] is not None]
    existing = targets(ids)
    if set(existing) != set(ids) or not hmac.compare_digest(plan['guard'], guard(e, users, existing)):
        raise onboarding.OnboardingError('A reviewed team, participant, location or event setting changed. Build a fresh preview.')
    names = {name.casefold() for name, in db.session.query(Teams.name)}
    for group in plan['groups']:
        if group['team_id'] is None and group['name'].casefold() in names:
            raise onboarding.OnboardingError('A proposed new team name is now in use. Build a fresh preview.')
        if group['team_id'] is not None and len(existing[group['team_id']]['member_ids']) + len(group['user_ids']) > onboarding.settings().team_size:
            raise onboarding.OnboardingError('A reviewed team is now full. Build a fresh preview.')
    people = {u.id: u for u in users}
    placed = []
    for group in plan['groups']:
        created = group['team_id'] is None
        if created:
            team = onboarding.new_team(name=group['name'], captain_id=group['user_ids'][0])
            db.session.add(team)
            db.session.flush()
            onboarding.generated_identity(team.id)
            tid = team.id
        else:
            tid = group['team_id']
        for uid in group['user_ids']:
            people[uid].team_id = tid
        placed.append({'team_id': tid, 'user_ids': group['user_ids'], 'new_team': created})
    return placed
