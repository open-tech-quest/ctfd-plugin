"""Open Tech Quest for pinned CTFd 3.8.9; no core fork or seeded challenges."""
from flask import Blueprint, render_template, session
from sqlalchemy.exc import IntegrityError
from CTFd.models import Solves, db
from CTFd.plugins import (register_admin_plugin_menu_bar, register_plugin_stylesheet,
                          register_plugin_script, register_admin_plugin_script)
from CTFd.plugins.migrations import upgrade
from CTFd.utils import get_config, set_config
from .models import QuestEvent, QuestRoom
from .recovery_models import QuestRecoverySolve


def load(app):
    from .backup_maintenance import install as install_backup_maintenance
    install_backup_maintenance(app)
    upgrade(plugin_name="open_tech_quest")
    from .schema import upgrade_sqlite_attendance
    upgrade_sqlite_attendance()
    if get_config("user_mode") != "teams":
        if Solves.query.filter_by(team_id=None).first():
            raise RuntimeError("Open Tech Quest requires teams; individual solves exist. Migrate ownership explicitly before enabling.")
        set_config("user_mode", "teams")
    if QuestEvent.query.get(1) is None:
        db.session.add(QuestEvent(id=1))
        from .defaults import default_rooms
        db.session.add_all(default_rooms())
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if QuestEvent.query.get(1) is None:
                raise
    from .onboarding import initialize
    initialize(QuestEvent.query.get(1))
    from .room_topology import initialize_visibility
    initialize_visibility()
    from .read_cache import install
    install(app)
    from .api import api, viewer
    from .guards import install_guards
    shell = Blueprint("quest", __name__, template_folder="templates", static_folder="assets", static_url_path="/plugins/open_tech_quest/assets")

    @shell.route("/quest")
    @shell.route("/quest/<path:path>")
    def index(path=None):
        return render_template("quest.html", init={"csrfNonce": session.get("nonce"), "user": viewer()})

    app.register_blueprint(shell)
    app.register_blueprint(api, url_prefix="/api/v1/quest")
    from .backups import backups_api
    app.register_blueprint(backups_api, url_prefix="/api/v1/quest")
    from .authoring import authoring_api
    app.register_blueprint(authoring_api, url_prefix="/api/v1/quest")
    from .assets import assets_api, install as install_assets
    app.register_blueprint(assets_api, url_prefix="/api/v1/quest")
    from .preview import preview_api
    app.register_blueprint(preview_api, url_prefix="/api/v1/quest/manage/preview")
    install_guards(app)
    from .recovery import install_recovery
    install_recovery(app)
    install_assets(app)
    register_plugin_stylesheet("/plugins/open_tech_quest/assets/app/native-auth.css")
    register_plugin_script("/plugins/open_tech_quest/assets/app/native-brand.js")
    register_admin_plugin_script("/plugins/open_tech_quest/assets/app/native-brand.js")
    register_admin_plugin_menu_bar("Open Tech Quest", "/quest/admin")
