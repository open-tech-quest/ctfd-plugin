"""Versioned shared reads, invalidated in the same transaction as domain writes.

The database generation is read in the same snapshot as the cached inputs. A
reader finishing after a concurrent commit can therefore only fill its old key,
never attach old inputs to the writer's new generation. Redis is an optional
accelerator; it does not grant access or decide whether a write is permitted.
"""
import copy
import hashlib
import json
import re
import uuid

from flask import g, has_request_context, request
from sqlalchemy import event as sa_event
from CTFd.cache import cache
from CTFd.models import db
from .models import QuestReadVersion

_SCHEMA = "quest-read-v3-hint-usage"
_TABLE = re.compile(r'^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+[`"\[]?([\w]+)', re.I)
_CONTENT = {"quest_rooms", "quest_steps", "challenges", "flags", "hints", "files",
            "solutions", "tags", "challenge_tags", "topics", "challenge_topics"}
_ROSTER = {"users", "teams", "quest_maintainers", "quest_onboarding", "quest_readiness", "quest_team_names"}
_HISTORY = {"submissions", "solves", "quest_recovery_solves", "quest_starts", "quest_corrections", "awards", "unlocks", "quest_hint_purchases"}
_ACTIVITY = {"submissions", "solves", "quest_recovery_solves", "awards", "unlocks", "ratings"}


def read_request():
    return has_request_context() and request.method == "GET"


def clear_request_reads():
    """Forget facts when the request crosses a transaction/write boundary."""
    if has_request_context():
        for key in ("quest_read_versions", "quest_projections", "quest_read_user", "quest_read_roles", "quest_read_event"):
            g.pop(key, None)


def token(parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def versions(team_id=None, user_id=None):
    # An autoflush may change generations. Do it before consulting a request
    # memo, including GET handlers that deliberately stage a mutation.
    if db.session.new or db.session.dirty or db.session.deleted:
        db.session.flush()
    names = {"event", "content", "roster", "progress", "history"}
    if team_id is not None:
        names.add(f"team:{team_id}")
    if user_id is not None:
        names.add(f"user:{user_id}")
    values = getattr(g, "quest_read_versions", {}) if read_request() else {}
    missing = names - values.keys()
    if missing:
        rows = db.session.query(QuestReadVersion.key, QuestReadVersion.value).filter(QuestReadVersion.key.in_(missing)).all()
        values.update({name: "" for name in missing})
        values.update(dict(rows))
    if read_request():
        g.quest_read_versions = values
    return {"event": values["event"], "content": values["content"], "roster": values["roster"],
            "progress": values["progress"], "history": values["history"],
            "team": values.get(f"team:{team_id}", ""), "user": values.get(f"user:{user_id}", "")}


def public_revisions(team_id=None, user_id=None):
    values = versions(team_id, user_id)
    return {"content": token([values["event"], values["content"]]),
            "roster": token([values["event"], values["roster"], values["user"]]),
            "progress": token([values["event"], values["content"], values["roster"], values["history"],
                               values["progress"] if team_id is None else values["team"]])}


def shared_reads_enabled():
    """Only verified repeatable-read MySQL/MariaDB snapshots can share inputs."""
    engine = db.engine
    if not getattr(engine, "_quest_shared_reads_enabled", False):
        return False
    # Honor connection-level SQLAlchemy overrides as well as engine startup
    # configuration. Checking execution options sends no database query.
    override = db.session.connection().get_execution_options().get("isolation_level")
    return override is None or override.replace("_", " ").upper() == "REPEATABLE READ"


def cached(kind, scope, token_parts, factory):
    """Share primitive inputs for GETs only; callers keep authorization fresh."""
    # Python sqlite3's legacy SELECTs do not pin a physical read transaction;
    # READ COMMITTED also permits generation and input reads to see different
    # states. Preserve those databases' native transaction behavior and bypass
    # shared caching, rather than attach old inputs to a newer generation.
    if not read_request() or not shared_reads_enabled():
        return factory()
    database = str(db.engine.url)
    key = _SCHEMA + ":" + token([database, kind, scope, token_parts])
    try:
        existing = cache.get(key)
    except Exception:
        existing = None
    if existing is not None:
        return copy.deepcopy(existing["data"])
    value = factory()
    try:
        cache.set(key, {"data": value}, timeout=30)
    except Exception:
        pass
    return value


def _insert_ids(context, column):
    """Only inserts have unambiguous new ownership; unknown SQL falls back."""
    if not context.isinsert or not context.compiled_parameters:
        return None
    values = set()
    for parameters in context.compiled_parameters:
        value = parameters.get(column)
        if type(value) is not int:
            return None
        values.add(value)
    return values or None


def _changed_keys(table, context):
    keys = set()
    if table in _CONTENT:
        keys.update(("content", "history", "progress"))
    if table in _ROSTER:
        keys.add("roster")
        if table in ("users", "teams", "quest_maintainers"):
            keys.update(("history", "progress"))
    if table in ("quest_events", "quest_pauses", "configs"):
        keys.update(("event", "history", "progress"))
    if table == "quest_exclusions":
        keys.add("progress")
    if table in _HISTORY:
        keys.add("progress")
        teams = _insert_ids(context, "team_id")
        if teams is None:
            keys.add("history")
        else:
            keys.update(f"team:{tid}" for tid in teams)
    if table in _ACTIVITY:
        users = _insert_ids(context, "user_id")
        if users is None:
            keys.add("roster")
        else:
            keys.update(f"user:{uid}" for uid in users)
    return keys


def install(app):
    """Install once per engine, after migrations and initial event creation."""
    engine = db.engine
    if getattr(engine, "_quest_read_versions_installed", False):
        return
    engine._quest_read_versions_installed = True
    engine._quest_shared_reads_enabled = False
    if engine.dialect.name in ("mysql", "mariadb"):
        isolation = db.session.connection().get_isolation_level().replace("_", " ").upper()
        engine._quest_shared_reads_enabled = isolation == "REPEATABLE READ"

    @sa_event.listens_for(engine, "after_cursor_execute")
    def invalidate(connection, cursor, statement, parameters, context, executemany):
        if context.execution_options.get("quest_read_bump"):
            return
        compiled = getattr(context, "compiled", None)
        operation = getattr(compiled, "statement", None)
        if not (context.isinsert or context.isupdate or context.isdelete) and not _TABLE.match(statement):
            return
        table = getattr(getattr(operation, "table", None), "name", None)
        if table is None:
            match = _TABLE.match(statement)
            table = match[1].lower() if match else None
        if table is None or table == "quest_read_versions":
            return
        keys = _changed_keys(table, context)
        if not keys:
            return
        # Do not reuse the caller's DBAPI cursor: ORM inserts still need its
        # original lastrowid and rowcount after this callback returns.
        if connection.dialect.name == "sqlite":
            sql = "INSERT INTO quest_read_versions (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        elif connection.dialect.name in ("mysql", "mariadb"):
            sql = "INSERT INTO quest_read_versions (`key`, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value=VALUES(value)"
        else:
            sql = "INSERT INTO quest_read_versions (key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        connection.exec_driver_sql(sql, [(key, uuid.uuid4().hex) for key in sorted(keys)],
                                   execution_options={"quest_read_bump": True})
        clear_request_reads()

    @sa_event.listens_for(engine, "commit")
    def committed(connection):
        clear_request_reads()

    @sa_event.listens_for(engine, "rollback")
    def rolled_back(connection):
        clear_request_reads()
