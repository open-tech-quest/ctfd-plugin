"""Authoritative quest history; native CTFd remains owner of all solves."""
from datetime import datetime
from CTFd.models import db
from sqlalchemy.dialects.mysql import DATETIME

# Preserve identical microsecond clock origins on MariaDB and SQLite.
UTCDateTime = db.DateTime().with_variant(DATETIME(fsp=6), "mysql")


class QuestEvent(db.Model):
    __tablename__ = "quest_events"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, default="Open Tech Quest")
    status = db.Column(db.String(16), nullable=False, default="draft")
    start_at = db.Column(UTCDateTime)
    end_at = db.Column(UTCDateTime)
    paused_at = db.Column(UTCDateTime)
    rosters_release_at = db.Column(UTCDateTime)
    room_clock = db.Column(db.String(16), nullable=False, default="explicit")
    overall_clock = db.Column(db.String(16), nullable=False, default="event")
    projector = db.Column(db.JSON, nullable=False, default=lambda: {"view": "overview", "room_slug": None})
    snapshot = db.Column(db.JSON)


class QuestRoom(db.Model):
    __tablename__ = "quest_rooms"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)
    subtitle = db.Column(db.String(160), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    position = db.Column(db.Integer, nullable=False)
    theme = db.Column(db.JSON, nullable=False, default=dict)


class QuestStep(db.Model):
    __tablename__ = "quest_steps"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("quest_rooms.id", ondelete="CASCADE"), nullable=False)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="RESTRICT"), unique=True, nullable=False)
    position = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.UniqueConstraint("room_id", "position"),)


class QuestStart(db.Model):
    __tablename__ = "quest_starts"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("quest_rooms.id", ondelete="CASCADE"), nullable=False)
    started_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("team_id", "room_id"),)


class QuestPause(db.Model):
    __tablename__ = "quest_pauses"
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(UTCDateTime, nullable=False)
    ended_at = db.Column(UTCDateTime)


class QuestRoomPause(db.Model):
    __tablename__ = "quest_room_pauses"
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("quest_rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at = db.Column(UTCDateTime, nullable=False)
    ended_at = db.Column(UTCDateTime)
    reason = db.Column(db.Text, nullable=False)
    closed_by_event = db.Column(db.Boolean, nullable=False, default=False)


class QuestExclusion(db.Model):
    __tablename__ = "quest_exclusions"
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    reason = db.Column(db.Text, nullable=False)


class QuestCorrection(db.Model):
    __tablename__ = "quest_corrections"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("quest_rooms.id", ondelete="CASCADE"), nullable=False)
    adjustment_ms = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)


class QuestAudit(db.Model):
    __tablename__ = "quest_audit"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(64), nullable=False)
    detail = db.Column(db.JSON, nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)


class QuestDemoBatch(db.Model):
    """Exact ownership and reversible presentation values for an explicit demo."""
    __tablename__ = "quest_demo_batches"
    id = db.Column(db.Integer, primary_key=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Only one batch can own the active slot, including on SQLite where
    # SELECT FOR UPDATE is unavailable. Removed batches release it to NULL.
    active_slot = db.Column(db.Integer, unique=True, default=1)
    challenge_ids = db.Column(db.JSON, nullable=False)
    rooms = db.Column(db.JSON, nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)
    removed_at = db.Column(UTCDateTime)


class QuestDemoChallenge(db.Model):
    """A deleted native challenge loses ownership, even if its ID is reused."""
    __tablename__ = "quest_demo_challenges"
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("quest_demo_batches.id", ondelete="CASCADE"), nullable=False)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="SET NULL"), unique=True)


class QuestOnboarding(db.Model):
    __tablename__ = "quest_onboarding"
    id = db.Column(db.Integer, primary_key=True)
    phase = db.Column(db.String(16), nullable=False, default="check_in")
    team_size = db.Column(db.Integer, nullable=False, default=5)
    applied_at = db.Column(UTCDateTime)
    finalized_at = db.Column(UTCDateTime)
    grandfathered = db.Column(db.Boolean, nullable=False, default=False)
    attendance_open = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    attendance_generation = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    attendance_opened_at = db.Column(UTCDateTime)
    attendance_closed_at = db.Column(UTCDateTime)
    allow_team_naming = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())


class QuestReadiness(db.Model):
    __tablename__ = "quest_readiness"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ready_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)
    late = db.Column(db.Boolean, nullable=False, default=False)


class QuestTeamName(db.Model):
    __tablename__ = "quest_team_names"
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    claimed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    claimed_at = db.Column(UTCDateTime)
    name_locked = db.Column(db.Boolean, nullable=False, default=False)
    auto_named = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())


class QuestMaintainer(db.Model):
    __tablename__ = "quest_maintainers"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    previous_hidden = db.Column(db.Boolean, nullable=False)
    granted_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)


class QuestReadVersion(db.Model):
    """Transactional cache generations; UUIDs also isolate restored/recreated rows."""
    __tablename__ = "quest_read_versions"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(32), nullable=False)


class QuestHintPurchase(db.Model):
    """Link native unlocks/charges without repricing historical purchases."""
    __tablename__ = "quest_hint_purchases"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="RESTRICT"), nullable=False)
    hint_id = db.Column(db.Integer, db.ForeignKey("hints.id", ondelete="RESTRICT"), nullable=False)
    unlock_id = db.Column(db.Integer, db.ForeignKey("unlocks.id", ondelete="RESTRICT"), nullable=False, unique=True)
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="RESTRICT"), nullable=False, unique=True)
    cost = db.Column(db.Integer, nullable=False)
    purchased_at = db.Column(UTCDateTime, nullable=False)
    __table_args__ = (db.UniqueConstraint("team_id", "hint_id"),)


class QuestPriceChange(db.Model):
    """Reviewed price cutovers; original native solves and receipts stay intact."""
    __tablename__ = "quest_price_changes"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="RESTRICT"), nullable=False, index=True)
    hint_id = db.Column(db.Integer, db.ForeignKey("hints.id", ondelete="RESTRICT"), index=True)
    before_value = db.Column(db.Integer, nullable=False)
    after_value = db.Column(db.Integer, nullable=False)
    existing_through_id = db.Column(db.Integer, nullable=False)
    apply_existing = db.Column(db.Boolean, nullable=False)
    changed_at = db.Column(UTCDateTime, nullable=False)


class QuestChallengeSource(db.Model):
    """Explicit catalog identity; a matching display name never grants ownership."""
    __tablename__ = "quest_challenge_sources"
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("quest_rooms.id", ondelete="CASCADE"), nullable=False)
    source_key = db.Column(db.String(255, collation="utf8mb4_bin").with_variant(db.String(255), "sqlite"), nullable=False)
    __table_args__ = (db.UniqueConstraint("room_id", "source_key"),)


class QuestAsset(db.Model):
    """Kind and bounded metadata for a native, challenge-owned uploaded file."""
    __tablename__ = "quest_assets"
    file_id = db.Column(db.Integer, db.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False)
    kind = db.Column(db.String(16), nullable=False)
    name = db.Column(db.String(160, collation="utf8mb4_bin").with_variant(db.String(160), "sqlite"), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    created_at = db.Column(UTCDateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("challenge_id", "kind", "name"),)
