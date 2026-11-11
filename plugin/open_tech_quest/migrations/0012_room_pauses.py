"""Timestamped organizer interruptions scoped to one room."""
revision = "quest_0012"
down_revision = "quest_0011"
branch_labels = None
depends_on = None


def upgrade(op):
    from CTFd.plugins.open_tech_quest.models import QuestRoomPause
    QuestRoomPause.__table__.create(bind=op.get_bind(), checkfirst=True)
