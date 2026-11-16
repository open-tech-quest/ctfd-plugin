"""Physical venue rooms and exclusive per-team table assignments."""
revision = 'quest_0014'
down_revision = 'quest_0013'
branch_labels = None
depends_on = None


def upgrade(op):
    from CTFd.plugins.open_tech_quest.location_models import QuestVenueRoom, QuestTeamLocation
    for model in (QuestVenueRoom, QuestTeamLocation):
        model.__table__.create(bind=op.get_bind(), checkfirst=True)
