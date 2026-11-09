"""Disposable browser/load fixture. NEVER bundled or invoked in production.

QUEST_ALLOW_DEMO=1 python /opt/quest-tests/demo_fixture.py [--draft]
Use a disposable DATABASE_URL whose name/path contains demo, test or browser.
All accounts use the deliberately public password `quest-demo-only`.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, "/opt/CTFd")
from CTFd import create_app
from CTFd.models import Challenges, ChallengeFiles, Configs, Flags, Hints, Solves, Teams, Users, db
from CTFd.cache import cache
from CTFd.utils import set_config
from CTFd.utils.crypto import hash_password
from CTFd.plugins.open_tech_quest.models import QuestEvent, QuestRoom, QuestStep, QuestStart, QuestOnboarding, QuestReadiness, QuestTeamName


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--members-per-team", type=int, default=2, choices=range(2, 11), metavar="2..10")
    parser.add_argument("--challenges-per-room", type=int, default=5, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    database = os.environ.get("DATABASE_URL", "")
    if os.environ.get("QUEST_ALLOW_DEMO") != "1" or not any(word in database.lower().rsplit("/", 1)[-1] for word in ("demo", "test", "browser")):
        raise SystemExit("Refusing: require QUEST_ALLOW_DEMO=1 and an explicitly disposable demo/test/browser DATABASE_URL")
    app = create_app()
    with app.app_context():
        with db.engine.connect() as connection:
            dialect = db.engine.dialect.name
            connection.execute("PRAGMA foreign_keys=OFF" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=0")
            revisions = [{"key": row.key, "value": row.value} for row in connection.execute(Configs.__table__.select()).fetchall() if row.key.endswith("_alembic_version")]
            transaction = connection.begin()
            for table in reversed(db.metadata.sorted_tables):
                connection.execute(table.delete())
            if revisions:
                connection.execute(Configs.__table__.insert(), revisions)
            transaction.commit()
            connection.execute("PRAGMA foreign_keys=ON" if dialect == "sqlite" else "SET FOREIGN_KEY_CHECKS=1")
        cache.clear()
        at = datetime.utcnow() - timedelta(minutes=40)
        for key, value in (("setup", True), ("ctf_name", "Open Tech Quest — disposable demo"), ("user_mode", "teams"), ("challenge_visibility", "public"), ("account_visibility", "public"), ("verify_emails", False), ("paused", False), ("start", int(at.timestamp()) if not args.draft else None), ("end", None)):
            set_config(key, value)
        e = QuestEvent(id=1, name="Open Tech Quest", status="draft" if args.draft else "live", start_at=None if args.draft else at)
        db.session.add(e)
        db.session.add(QuestOnboarding(id=1, phase="check_in" if args.draft else "finalized", team_size=max(5, args.members_per_team), grandfathered=not args.draft))
        password = hash_password("quest-demo-only")
        names = ["Atlas", "Aurora", "Beacon", "Binary", "Bluebird", "Bridge", "Cedar", "Circuit", "Cloudwalk", "Cobalt", "Comet", "Compass", "Core", "Cosmos", "Crimson", "Delta", "Discovery", "Echo", "Elm", "Ember", "Falcon", "Fern", "Flux", "Forge", "Glider", "Harbor", "Helix", "Horizon", "Indigo", "Juniper", "Keystone", "Kite", "Lighthouse", "Maple", "Meridian", "Meteor", "Nimbus", "Nova", "Orbit", "Pioneer", "Pixel", "Quartz", "Relay", "River", "Signal", "Skyline", "Sparrow", "Summit", "Vector", "Willow"]
        db.session.execute(Users.__table__.insert(), {"id": 1, "name": "organizer", "email": "organizer@example.test", "password": password, "type": "admin", "verified": True, "banned": False, "hidden": False})
        team_ids = []
        for index, name in enumerate(names):
            team = Teams(name=name)
            db.session.add(team)
            db.session.flush()
            team_ids.append(team.id)
            if not args.draft:
                db.session.add(QuestTeamName(team_id=team.id, name_locked=True))
            rows = []
            for member in range(args.members_per_team):
                uid = 2 + index * args.members_per_team + member
                username = ("atlas-one" if member == 0 else "atlas-two") if index == 0 and member < 2 else f"{name.lower()}-{member + 1}"
                rows.append({"id": uid, "name": username, "email": username + "@example.test", "password": password, "type": "user", "verified": True, "banned": False, "hidden": False, "team_id": team.id})
            db.session.execute(Users.__table__.insert(), rows)
            if not args.draft:
                for row in rows:
                    db.session.add(QuestReadiness(user_id=row["id"], ready_at=at))
            team.captain_id = rows[0]["id"]
        for position, (slug, name) in enumerate((("openshift", "OpenShift"), ("ansible", "Ansible"), ("rhel", "RHEL"), ("security", "Security"), ("ai", "AI"))):
            room = QuestRoom(slug=slug, name=name, position=position, subtitle={"openshift": "Build on a platform that moves with you", "ansible": "Turn repeatable work into reliable automation", "rhel": "Get closer to the operating system", "security": "Follow evidence. Protect the system.", "ai": "Explore models, data and open possibilities"}[slug], description="Disposable practice challenges for browser and load verification.", theme={"layout": slug, "accent": "#00a6b2", "logo_url": None if slug == "security" else f"/plugins/open_tech_quest/assets/branding/{slug}.{'webp' if slug == 'ai' else 'png'}", "hero_url": None})
            db.session.add(room)
            db.session.flush()
            ids = [position * args.challenges_per_room + step + 1 for step in range(args.challenges_per_room)]
            for step, cid in enumerate(ids):
                db.session.add(Challenges(id=cid, name=(["Find your bearings", "Inspect the system", "Make a change", "Connect the pieces", "Complete the mission"][step] if args.challenges_per_room == 5 else f"Step {step + 1}: " + ["Inspect the cluster operators and review the deployment configuration", "Read the application logs and trace the failing service", "Configure network policies for a protected workload", "Deploy an application from a Git repository and verify its health", "Investigate the image registry and complete the recovery procedure"][step % 5]), description=f"## {name}: step {step + 1}\n\nThis is an isolated test fixture. Submit **demo-{slug}-{step + 1}** to verify the native challenge flow.\n\nExplore the attachment and hint for this practice task.", value=100 * (step + 1), category=name, type="standard", state="visible", requirements={"prerequisites": ids[:step], "anonymize": False}, position=step, next_id=None))
                db.session.flush()
                if step:
                    Challenges.query.get(ids[step - 1]).next_id = cid
                db.session.add(Flags(challenge_id=cid, type="static", content=f"demo-{slug}-{step + 1}", data=""))
                db.session.add(Hints(id=cid, challenge_id=cid, title="Practice clue", content=f"Use demo-{slug}-{step + 1} for this isolated test.", cost=0 if step == 0 else 25))
                db.session.add(QuestStep(room_id=room.id, challenge_id=cid, position=step))
                location = f"quest-demo-{slug}-{step + 1}/instructions.txt"
                path = Path(app.config["UPLOAD_FOLDER"]) / location
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Open Tech Quest disposable demo attachment.\n")
                db.session.add(ChallengeFiles(challenge_id=cid, location=location))
            if not args.draft:
                for team_index, team_id in enumerate(team_ids):
                    if team_index == 0:
                        continue  # Atlas remains untouched for participant journeys.
                    started = at + timedelta(minutes=(team_index + position) % 10)
                    db.session.add(QuestStart(team_id=team_id, room_id=room.id, started_at=started))
                    solved = (team_index + position * 2) % (args.challenges_per_room + 1)
                    for step in range(solved):
                        db.session.add(Solves(challenge_id=ids[step], user_id=2 + team_index * args.members_per_team, team_id=team_id, date=started + timedelta(seconds=60 * (step + 1) + team_index)))
        db.session.commit()
        print(f"Disposable fixture ready: 50 teams, {50 * args.members_per_team} participants, {5 * args.challenges_per_room} challenges; " + ("draft" if args.draft else "live") + ".")
        print("Login: organizer / quest-demo-only; participants atlas-one and atlas-two / quest-demo-only.")


if __name__ == "__main__":
    main()
