"""Private event archives and atomic transition to a fresh competition round."""
import hmac
import hashlib

from sqlalchemy import inspect

from CTFd.cache import (clear_challenges, clear_standings, clear_team_session,
                        clear_user_session)
from CTFd.models import (Awards, ChallengeComments, ChallengeFiles, ChallengeTopics,
                         Challenges, Comments, Files, Flags, Hints, Notifications, Ratings,
                         Solves, SolutionFiles, Solutions, Submissions, Tags, TeamComments,
                         TeamFieldEntries, Teams, Tokens, Tracking, Unlocks, UserComments,
                         UserFieldEntries, Users, db)
from . import archive, projector, registration
from .models import (QuestAsset, QuestChallengeSource, QuestAudit, QuestCorrection, QuestExclusion,
                     QuestHintPurchase, QuestPriceChange, QuestMaintainer, QuestOnboarding, QuestPause, QuestRoomPause,
                     QuestDemoBatch, QuestDemoChallenge, QuestRoom, QuestStep,
                     QuestReadiness, QuestStart, QuestTeamName)
from .recovery_models import QuestRecoverySolve
from .service import iso, now

ARCHIVE_ACTION = 'event.archive'


class RoundError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status


def stored_archives():
    # Select summary JSON only: normal organizer reads never load old documents.
    rows = db.session.query(QuestAudit.id, QuestAudit.created_at,
                            QuestAudit.detail['summary'].label('summary')).filter(
        QuestAudit.action == ARCHIVE_ACTION).order_by(QuestAudit.id.desc()).all()
    return [dict(row.summary, id=row.id, created_at=iso(row.created_at)) for row in rows]


def save_archive(e, source, actor_id, kind='snapshot'):
    document = archive.make_document(source)
    summary = {'archive_id': document['archive_id'], 'name': e.name,
               'event_status': e.status, 'sha256': document['integrity']['sha256'],
               'kind': kind, 'counts': {key: len(source.get(key, [])) for key in
                   ('participants', 'teams', 'rooms', 'challenges', 'attempts', 'starts', 'hint_purchases')}}
    row = QuestAudit(actor_id=actor_id, action=ARCHIVE_ACTION,
                     detail={'summary': summary, 'document': document,
                             'sha256': document['integrity']['sha256']})
    db.session.add(row)
    db.session.flush()
    return row, document, summary


# Explicit pinned native/plugin coverage. Dynamically registered plugin models do
# not silently become supported just because they share SQLAlchemy metadata.
KNOWN_TABLES = set("awards brackets challenge_topics challenges comments config dynamic_challenge field_entries fields files flags hints notifications pages ratings solutions solves submissions tags teams tokens topics tracking unlocks users quest_events quest_rooms quest_steps quest_starts quest_pauses quest_exclusions quest_corrections quest_audit quest_demo_batches quest_demo_challenges quest_onboarding quest_readiness quest_team_names quest_maintainers quest_read_versions quest_hint_purchases quest_recovery_solves".split())
KNOWN_TABLES.update({"quest_assets", "quest_challenge_sources", "quest_room_pauses", "quest_price_changes"})


def unsupported_dependents(mode):
    inspector = inspect(db.session.connection())
    parents = {'users', 'teams', 'submissions', 'solves', 'awards', 'unlocks',
               'quest_starts', 'quest_pauses', 'quest_room_pauses', 'quest_corrections', 'quest_exclusions',
               'quest_readiness', 'quest_team_names', 'quest_maintainers',
               'quest_hint_purchases', 'quest_price_changes', 'quest_recovery_solves', 'ratings',
               'comments', 'field_entries', 'notifications', 'tokens', 'tracking', 'quest_audit'}
    if mode == 'defaults':
        parents.update({'challenges', 'dynamic_challenge', 'flags', 'hints', 'solutions',
                        'files', 'tags', 'challenge_topics', 'quest_rooms', 'quest_steps',
                        'quest_demo_batches', 'quest_demo_challenges'})
    references = {name: {fk['referred_table'] for fk in inspector.get_foreign_keys(name)}
                  for name in inspector.get_table_names()}
    affected = set(parents)
    while True:
        expanded = affected | {name for name, refs in references.items() if refs & affected}
        if expanded == affected:
            break
        affected = expanded
    return sorted(affected - KNOWN_TABLES)


def reset_reason(e, mode):
    if mode not in ('new_round', 'defaults'):
        raise RoundError('Choose another round or restore default rooms.', 400)
    if e.status not in ('draft', 'closed', 'published'):
        return 'Close the event before preparing another round.'
    unsupported = sorted({c.type for c in Challenges.query.all()} - {'standard', 'dynamic'})
    if unsupported:
        return 'Reset needs review for these additional challenge types: ' + ', '.join(unsupported)
    supported = {'correct', 'incorrect', 'partial', 'ratelimited', 'discard', 'quest_recovery'}
    types = {kind for (kind,) in db.session.query(Submissions.type).distinct().all()}
    if types - supported:
        return 'Reset needs review for an additional submission type.'
    unsupported = unsupported_dependents(mode)
    if unsupported:
        return 'Reset needs an archive/cleanup plan for additional plugin tables: ' + ', '.join(unsupported)
    return None


def reset_revision(source, mode, actor_id):
    return hashlib.sha256((str(actor_id) + ':' + mode + ':' + archive.source_fingerprint(source)).encode()).hexdigest()


def reset_preview(e, source=None, mode='new_round', actor_id=None):
    source = source if source is not None else archive.collect_event(e)
    reason = reset_reason(e, mode)
    counts = {name: model.query.count() for name, model in (
        ('teams', Teams), ('accounts', Users), ('challenges', Challenges), ('rooms', QuestRoom),
        ('submissions', Submissions), ('solves', Solves), ('room_starts', QuestStart),
        ('hint_purchases', QuestHintPurchase), ('unlocks', Unlocks), ('awards', Awards),
        ('price_changes', QuestPriceChange),
        ('attendance', QuestReadiness), ('pauses', QuestPause), ('room_pauses', QuestRoomPause), ('ratings', Ratings),
        ('corrections', QuestCorrection), ('exclusions', QuestExclusion),
        ('team_notes', TeamComments), ('team_fields', TeamFieldEntries),
        ('notifications', Notifications))}
    counts['accounts_removed'] = Users.query.filter(Users.id != actor_id).count()
    counts['accounts_retained'] = 1
    protected = Users.query.get(actor_id)
    return {'allowed': reason is None, 'reason': reason, 'mode': mode,
            'administrator': {'id': actor_id, 'name': protected.name if protected else None},
            'revision': reset_revision(source, mode, actor_id), 'event_name': e.name,
            'counts': counts,
            'preserved': ['Only the signed-in administrator account, password and access',
                          'Event name, clock policies, team capacity and naming preference',
                          'Native hosting settings, pages and non-challenge assets',
                          'Saved event archives'] + (
                              ['Rooms, challenge order, flags, hints and challenge uploads'] if mode == 'new_round' else []),
            'removed': ['Every other account, including other administrators and maintainers',
                        'Teams, memberships, names, notes and custom field values',
                        'Attendance and all competition attempts, solves and room starts',
                        'Hint purchases, unlocks, awards, ratings and round notifications',
                        'Pauses, corrections, exclusions and current-round audit entries',
                        'Published results and scheduled event/roster times'] + (
                            ['All challenges, flags, hints, solutions, attachment records and room customization; five empty default rooms are created'] if mode == 'defaults' else []),
            'archive_policy': 'A complete JSON event archive is retained before anything is cleared. Attachment bytes and credentials are not embedded. Public registration and attendance close, and the event returns to draft.'}



def reset_round(e, data, actor_id):
    """Caller owns the event lock and fresh administrator/CSRF authority."""
    from .api import native_config, clear_native_config
    if set(data) != {'revision', 'confirm', 'event_name', 'reason', 'mode'}:
        raise RoundError('Use a fresh reset preview and confirm the event name and reason.', 400)
    if data.get('confirm') is not True or data.get('event_name') != e.name:
        raise RoundError('Type the current event name to confirm a new round.', 400)
    reason = data.get('reason')
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 1000:
        raise RoundError('Record a reason for starting another round (3–1000 characters).', 400)
    mode = data.get('mode')
    source = archive.collect_event(e)
    preview = reset_preview(e, source, mode, actor_id)
    if not preview['allowed']:
        raise RoundError(preview['reason'])
    revision = data.get('revision')
    if not isinstance(revision, str) or not hmac.compare_digest(revision.encode(), preview['revision'].encode()):
        raise RoundError('The event changed after the preview. Review a fresh preview before clearing it.')
    # Archive insertion and every deletion share this one transaction. A failed
    # insert/delete/commit leaves both the event and earlier archives untouched.
    row, document, summary = save_archive(e, source, actor_id, mode)
    archive_id = row.id
    user_ids = [uid for (uid,) in db.session.query(Users.id)]
    team_ids = [tid for (tid,) in db.session.query(Teams.id)]
    for model in (QuestPriceChange, QuestHintPurchase, QuestRecoverySolve):
        db.session.execute(model.__table__.delete())
    db.session.execute(Solves.__table__.delete())
    db.session.execute(Submissions.__table__.delete())
    for model in (Unlocks, Awards, Ratings, QuestStart, QuestPause, QuestRoomPause,
                  QuestCorrection, QuestExclusion, QuestReadiness, QuestTeamName):
        db.session.execute(model.__table__.delete())
    # These are native single-table subclasses, so filter by their team field;
    # unrelated user/challenge/page notes and personal profile fields survive.
    TeamComments.query.filter(TeamComments.team_id.isnot(None)).delete(synchronize_session=False)
    TeamFieldEntries.query.filter(TeamFieldEntries.team_id.isnot(None)).delete(synchronize_session=False)
    Notifications.query.delete(synchronize_session=False)
    Users.query.filter(Users.team_id.isnot(None)).update({'team_id': None}, synchronize_session=False)
    Teams.query.update({'captain_id': None}, synchronize_session=False)
    db.session.execute(Teams.__table__.delete())
    # Keep the executing administrator. Authored challenge/page notes survive
    # with a cleared author reference; account-specific notes/profile data do not.
    removed = [uid for uid in user_ids if uid != actor_id]
    if removed:
        Comments.query.filter(Comments.author_id.in_(removed)).update({'author_id': None}, synchronize_session=False)
        UserComments.query.filter(UserComments.user_id.in_(removed)).delete(synchronize_session=False)
        UserFieldEntries.query.filter(UserFieldEntries.user_id.in_(removed)).delete(synchronize_session=False)
        Tokens.query.filter(Tokens.user_id.in_(removed)).delete(synchronize_session=False)
        Tracking.query.filter(Tracking.user_id.in_(removed)).delete(synchronize_session=False)
        QuestAudit.query.filter(QuestAudit.actor_id.in_(removed)).update({'actor_id': None}, synchronize_session=False)
    QuestMaintainer.query.delete(synchronize_session=False)
    Users.query.filter(Users.id != actor_id).delete(synchronize_session=False)
    if mode == 'defaults':
        from .defaults import default_rooms
        from CTFd.plugins.dynamic_challenges import DynamicChallenge
        # Foreign keys are explicit so behavior is identical with SQLite's
        # optional enforcement and MariaDB's mandatory constraints.
        db.session.execute(QuestAsset.__table__.delete())
        db.session.execute(QuestChallengeSource.__table__.delete())
        db.session.execute(QuestDemoChallenge.__table__.delete())
        db.session.execute(QuestDemoBatch.__table__.delete())
        db.session.execute(QuestStep.__table__.delete())
        ChallengeComments.query.delete(synchronize_session=False)
        SolutionFiles.query.delete(synchronize_session=False)
        ChallengeFiles.query.delete(synchronize_session=False)
        for model in (Flags, Hints, Solutions, Tags, ChallengeTopics, DynamicChallenge):
            db.session.execute(model.__table__.delete())
        Challenges.query.update({'next_id': None}, synchronize_session=False)
        db.session.execute(Challenges.__table__.delete())
        QuestRoom.query.delete(synchronize_session='fetch')
        db.session.add_all(default_rooms())
    QuestAudit.query.filter(QuestAudit.action != ARCHIVE_ACTION).delete(synchronize_session=False)
    # Native dynamic scores start at their configured initial value with zero
    # solves. Calling calculate_value here would commit outside this transaction.
    for challenge in Challenges.query.filter_by(type='dynamic').all():
        challenge.value = challenge.initial
    state = QuestOnboarding.query.get(1)
    state.phase, state.applied_at, state.finalized_at = 'check_in', None, None
    state.grandfathered = False
    state.attendance_open = False
    state.attendance_generation += 1
    state.attendance_opened_at = state.attendance_closed_at = None
    e.status = 'draft'
    e.start_at = e.end_at = e.paused_at = e.rosters_release_at = None
    e.snapshot = None
    config = projector.settings(e.projector)
    config.update(view='overview', room_slug=None)
    config['rotation'].update(enabled=False, anchor_at=None, paused_at=None)
    e.projector = config
    for key, value in [('start', None), ('end', None), ('paused', False),
                       ('freeze', None), (registration.KEY, 'private')]:
        native_config(key, value)
    db.session.add(QuestAudit(actor_id=actor_id, action='event.new_round',
        detail={'archive_id': archive_id, 'archive_uuid': document['archive_id'],
                'sha256': summary['sha256'], 'reason': reason.strip(),
                'mode': mode, 'cleared': preview['counts']}))
    db.session.commit()
    warning = None
    try:
        clear_native_config()
        registration.invalidate()
        # A native freeze is also a cached CTFd configuration setting.
        from CTFd.cache import cache
        from CTFd.utils import _get_config
        cache.delete_memoized(_get_config, 'freeze')
        for uid in user_ids:
            clear_user_session(uid)
        for tid in team_ids:
            clear_team_session(tid)
        clear_challenges()
        clear_standings()
    except Exception:
        # A committed new round cannot be rolled back by a cache outage. Durable
        # generation keys already invalidate the plugin's shared read inputs.
        warning = 'The new round is ready, but native cache refresh needs operator attention.'
    return {'archive_id': archive_id, 'event_status': 'draft', 'mode': mode,
            'cleared': preview['counts'], 'cache_warning': warning}
