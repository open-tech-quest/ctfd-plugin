"""Physical venue inventory and one persisted table assignment per native team."""
from CTFd.models import db


class QuestVenueRoom(db.Model):
    __tablename__ = 'quest_venue_rooms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    table_count = db.Column(db.Integer, nullable=False)
    unavailable_tables = db.Column(db.JSON, nullable=False, default=list)


class QuestTeamLocation(db.Model):
    __tablename__ = 'quest_team_locations'
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id', ondelete='CASCADE'), primary_key=True)
    venue_room_id = db.Column(db.Integer, db.ForeignKey('quest_venue_rooms.id', ondelete='RESTRICT'), nullable=False)
    table_number = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.UniqueConstraint('venue_room_id', 'table_number'),)
