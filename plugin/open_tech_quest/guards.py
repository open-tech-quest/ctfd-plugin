"""Guard native content paths as well as the SPA; known URLs grant no access."""
import re
from flask import abort, g, jsonify, redirect, request, session
from CTFd.models import Challenges, Configs, Files, Hints, Solutions, Submissions, Teams, Users, db
from CTFd.utils.dates import ctftime
from CTFd.utils.user import is_admin
from CTFd.utils.security.signing import unserialize, BadSignature, BadTimeSignature, SignatureExpired
from .models import QuestStep, QuestStart, QuestAudit
from .service import event, now, participant_steps, released, solve_map
from . import onboarding, registration, lifecycle
from .lifecycle import current_user as get_current_user


def denied(message, status=403):
    return jsonify(success=False, error=message), status


def native_id(value):
    if isinstance(value, str) and len(value) <= 10 and value.isascii() and value.isdigit():
        value = int(value)
    return value if type(value) is int and 1 <= value <= 2147483647 else None


def controlled_config_write(names, controlled):
    names = [name for name in names if isinstance(name, str)]
    if any(name.casefold() in controlled for name in names):
        return True
    # Match the native lookup using the column's own database collation. On
    # MariaDB, accents and trailing spaces can alias an existing canonical key.
    # Python normalization cannot accurately reproduce those SQL semantics.
    return Configs.query.filter(Configs.key.in_(controlled), Configs.key.in_(names)).first() is not None


def install_guards(app):
    @app.before_request
    def quest_access():
        path = request.path.rstrip("/") or "/"
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        requested_uid = session.get("id")
        # Safely expire a deleted or replaced signed session before native
        # get_current_user can dereference a missing account's password.
        actor = get_current_user()
        administrator = is_admin()
        if administrator:
            # Apply the same post-lock session check to guarded native admin
            # writes, including team and submission mutations.
            g.quest_administrator = True
        synchronize_native = (write and actor is not None and not actor.banned and
                              path.startswith("/api/v1/") and not path.startswith("/api/v1/quest/") and
                              (administrator or path == "/api/v1/tokens" or path.startswith("/api/v1/tokens/")))
        if synchronize_native:
            g.quest_native_round_epoch = db.session.query(db.func.max(QuestAudit.id)).filter_by(action="event.new_round").scalar()
        # Read multipart bodies before holding the event lock: a slow native
        # upload must not stall participant submissions while bytes arrive.
        data = request.get_json(silent=True) if request.is_json else request.form
        data = data if isinstance(data, dict) or hasattr(data, "get") else {}
        if synchronize_native:
            # Participant submissions retain their existing single lock path.
            event(lock=True)
            actor = get_current_user()
            if actor is None or actor.banned:
                return denied("This account is unavailable. Sign in again if your account is active.", 403)
        resource_id = re.match(r"^/api/v1/(challenges|hints|solutions|awards)/([^/]+)(?:/|$)", path)
        if resource_id:
            resource, raw_id = resource_id.groups()
            named = resource == "challenges" and raw_id in ("attempt", "types")
            if not named and native_id(raw_id) is None:
                return denied("Use a positive integer resource ID", 400)
        unlock_model, unlock_target_id = None, None
        if path == "/api/v1/unlocks" and write:
            kind = data.get("type")
            unlock_model = {"hints": Hints, "solutions": Solutions}.get(kind) if isinstance(kind, str) else None
            unlock_target_id = native_id(data.get("target"))
            if unlock_model is None or unlock_target_id is None:
                return denied("Use a valid unlock type and positive integer target", 400)
        if path == "/":
            return redirect("/quest")
        if path == "/register":
            # Check the database under the same write lock as organizer
            # toggles. A cached public value cannot admit an already-open form
            # after registration was closed, even before cache invalidation.
            event(lock=True)
            if not registration.status()["enabled"]:
                message = "Public registration is closed. Existing participants can still sign in and confirm attendance."
                wants_json = request.is_json or request.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json"
                if wants_json:
                    return denied(message, 403)
                abort(403, description=message)
        elif write and (path == "/settings" or path.startswith("/api/v1/users")):
            # Native account handling remains responsible for credentials.
            event(lock=True)
            actor = get_current_user()
            if requested_uid is not None and (actor is None or actor.banned):
                return denied("This account is unavailable. Sign in again if your account is active.", 403 if actor else 401)
            native_target = re.fullmatch(r"/api/v1/users/(\d+)", path)
            if native_target or path == "/api/v1/users":
                if not lifecycle.administrator():
                    return denied("Administrator access required", 403)
                administrator = True
            if native_target:
                target = Users.query.populate_existing().filter_by(id=int(native_target[1])).first()
                if target:
                    e = event()
                    if request.method == "DELETE":
                        if target.type != "admin":
                            return denied("Delete unused accounts through the identity-bound Open Tech Quest Users control. Deactivate accounts with history.", 409)
                        blocked = lifecycle.delete_reason(target, e, native=True)
                        if blocked:
                            return denied(blocked, 409)
                        if not target.banned and lifecycle.usable_admins() <= 1:
                            return denied("Keep at least one usable administrator account.", 409)
                        uid = target.id
                        tid = lifecycle.safe_delete(target, "native")
                        db.session.commit()
                        lifecycle.invalidate(uid, tid)
                        response = jsonify(success=True, data={"id": uid, "deleted": True})
                        response.headers["Cache-Control"] = "private, no-store"
                        return response
                    if request.method == "PATCH":
                        # Native validation may reject another field later;
                        # ensure staged cleanup is rolled back on every 4xx.
                        g.quest_native_account = (target.id, target.team_id)
                        blocked = lifecycle.native_change(target, data, e)
                        if blocked:
                            db.session.rollback()
                            return denied(blocked, 409)
        elif write and (path.startswith("/api/v1/awards") or path == "/api/v1/unlocks"
                        or re.fullmatch(r"/api/v1/challenges/\d+/ratings", path)):
            # Account removal must not cascade away activity inserted after its
            # history check. Serialize native activity writers with that check.
            event(lock=True)
            actor = get_current_user()
            if actor is None or actor.banned:
                return denied("An active account is required", 403 if actor else 401)
            if path.startswith("/api/v1/awards"):
                if not lifecycle.administrator():
                    return denied("Administrator access required", 403)
                uid = data.get("user_id")
                if uid is not None and str(uid).isdigit() and Users.query.filter_by(id=int(uid)).first() is None:
                    return denied("User not found", 404)
        if path in ("/teams/new", "/teams/join", "/teams/invite"):
            return denied("Team allocation and naming are managed through Open Tech Quest")
        if path == "/team":
            user = get_current_user()
            if user and not user.team_id:
                return redirect("/quest")
        if path == "/setup" and request.method == "POST":
            # The plugin is team-based even when the upstream wizard defaults
            # its account-mode radio button to individual users.
            form = request.form.copy()
            form["user_mode"] = "teams"
            request.form = form
        # Topology remains managed in one place, even when native challenge
        # authoring is used for the actual descriptions, files and flags.
        match = re.fullmatch(r"/api/v1/challenges/(\d+)", path)
        if administrator:
            if write:
                from .maintenance import protect_native_content
                rejection = protect_native_content(path, data, event())
                if rejection is not None:
                    return rejection
                from .scoring import protect_native_score_write
                rejection = protect_native_score_write(path, data)
                if rejection is not None:
                    return rejection
                if path == "/api/v1/unlocks" and data.get("type") == "hints":
                    target_hint = Hints.query.get(unlock_target_id)
                    if target_hint and QuestStep.query.filter_by(challenge_id=target_hint.challenge_id).first():
                        return denied("Use organizer preview to read event hints without a purchase")
            if write and (path.startswith("/api/v1/configs") or path.startswith("/admin/plugins/")):
                controlled = {"user_mode", "start", "end", "paused", registration.KEY}
                names = [path.rsplit("/", 1)[-1], *data.keys(), data.get("key")]
                if controlled_config_write(names, controlled):
                    return denied("Manage team mode, event clocks and public registration through Open Tech Quest organizer controls", 409)
            if match and write:
                mapped = QuestStep.query.filter_by(challenge_id=int(match[1])).first()
                if mapped:
                    e = event(lock=True)
                    challenge = Challenges.query.get(int(match[1]))
                    price_fields = ("value", "function", "initial", "minimum", "decay", "type")
                    price_changed = any(k in data and str(data[k]) != str(getattr(challenge, k)) for k in price_fields)
                    if price_changed:
                        from .room_topology import activity_reason
                        from .models import QuestRoom
                        if e.status != "draft" or activity_reason(QuestRoom.query.get(mapped.room_id), [challenge.id]):
                            return denied("Challenge scoring is frozen after competition activity", 409)
                    changed = "requirements" in data and data["requirements"] != challenge.requirements
                    # Native edit forms serialize numeric values as strings and
                    # always send position, even when only description changed.
                    changed = changed or any(k in data and str(data[k]) != str(getattr(challenge, k)) for k in ("next_id", "position"))
                    if request.method == "DELETE" or changed:
                        return denied("Manage mapped challenge topology through Open Tech Quest room settings", 409)
            if write and path.startswith("/api/v1/teams"):
                e = event(lock=True)
                moderation = request.method == "PATCH" and re.fullmatch(r"/api/v1/teams/\d+", path) and set(data) <= {"hidden", "banned"}
                if not moderation or e.status != "draft" or onboarding.settings().phase not in ("check_in", "closed"):
                    return denied("Use Open Tech Quest team controls for capacity, roster and reasoned name changes", 409)
            user_match = re.fullmatch(r"/api/v1/users/(\d+)", path)
            if write and user_match and "type" in data:
                target = Users.query.get(int(user_match[1]))
                if target and target.team_id and data["type"] != target.type and onboarding.settings().phase in ("naming", "finalized"):
                    return denied("An assigned participant's account role is frozen after optimization; unassign while rosters are editable first", 409)
            if write and path.startswith("/api/v1/users/") and "team_id" in data:
                e = event()
                if e.status != "draft" or onboarding.settings().phase not in ("check_in", "closed"):
                    return denied("Team membership and identity are frozen after attendance optimization", 409)
            if write and path.startswith("/api/v1/submissions") and event(lock=True).status != "draft":
                submission_match = re.fullmatch(r"/api/v1/submissions/(\d+)", path)
                submission = Submissions.query.get(int(submission_match[1])) if submission_match else None
                challenge_id = submission.challenge_id if submission else data.get("challenge_id")
                if str(challenge_id).isdigit() and QuestStep.query.filter_by(challenge_id=int(challenge_id)).first():
                    return denied("Native event submissions are preserved. Use audited quest corrections.", 409)
            return
        if path in ("/", "/challenges", "/scoreboard"):
            return redirect("/quest/standings" if path == "/scoreboard" else "/quest")
        # Native self-service must never undo organizer allocation. Session
        # and password management still use CTFd's own supported routes.
        if path in ("/teams/new", "/teams/join", "/teams/invite") or (write and path.startswith("/api/v1/teams")):
            return denied("Team allocation is managed by the organizers")
        if path.startswith("/api/v1/scoreboard") or path.startswith("/api/v1/statistics"):
            return denied("Use the Open Tech Quest standings")
        if (path in ("/teams", "/team", "/users") or path.startswith(("/teams/", "/users/", "/api/v1/teams", "/api/v1/users"))) and not released():
            # Permit personal settings reads/updates, but keep allocation private.
            if path not in ("/api/v1/users/me",):
                return denied("Team rosters have not been released")
        if path == "/api/v1/challenges":
            return denied("Use the Open Tech Quest room board")
        challenge_id = None
        token_user = None
        protected_solution = None
        attempt = path == "/api/v1/challenges/attempt"
        if attempt:
            challenge_id = data.get("challenge_id")
        elif path.startswith("/api/v1/challenges/"):
            match = re.match(r"/api/v1/challenges/(\d+)(?:/|$)", path)
            if match:
                challenge_id = int(match[1])
        elif path.startswith("/api/v1/hints/"):
            match = re.fullmatch(r"/api/v1/hints/(\d+)", path)
            hint = Hints.query.get(int(match[1])) if match else None
            if match and hint is None:
                return denied("This hint is not available", 404)
            challenge_id = hint.challenge_id if hint else None
        elif path.startswith("/api/v1/solutions/"):
            match = re.fullmatch(r"/api/v1/solutions/(\d+)", path)
            solution = Solutions.query.get(int(match[1])) if match else None
            protected_solution = solution
            challenge_id = solution.challenge_id if solution else None
        elif path == "/api/v1/unlocks" and write:
            model = unlock_model
            target = model.query.get(unlock_target_id)
            if target is None:
                return denied("This unlock is not available", 404)
            challenge_id = target.challenge_id if target else None
            protected_solution = target if model is Solutions else None
        elif path.startswith("/files/"):
            file = Files.query.filter_by(location=path[len("/files/"):]).first()
            if file and request.method in ("GET", "HEAD"):
                from .models import QuestAsset
                from .roles import can_manage_rooms
                managed = QuestAsset.query.get(file.id)
                if managed and can_manage_rooms() and QuestStep.query.filter_by(challenge_id=managed.challenge_id).first():
                    from .assets import download_file, member_file
                    # Recheck native ownership, including solution linkage.
                    return download_file(member_file(managed.challenge_id, file.id), managed)
            challenge_id = getattr(file, "challenge_id", None)
            if file and file.type == "solution":
                solution = Solutions.query.get(file.solution_id)
                protected_solution = solution
                challenge_id = solution.challenge_id if solution else None
            if file and challenge_id and request.args.get("token"):
                try:
                    claims = unserialize(request.args["token"], max_age=3600)
                except (BadSignature, BadTimeSignature, SignatureExpired):
                    return denied("The download token is invalid or expired")
                if not isinstance(claims, dict):
                    return denied("Invalid download token")
                token_user = Users.query.get(claims.get("user_id")) if type(claims.get("user_id")) is int else None
                token_team = Teams.query.get(claims.get("team_id")) if type(claims.get("team_id")) is int else None
                if (not token_user or token_user.banned or not token_team or token_team.banned
                        or token_user.team_id != token_team.id or claims.get("file_id") != file.id):
                    return denied("The download token does not match this file and active team")
                current = get_current_user()
                if current and current.id != token_user.id:
                    return denied("The download token belongs to another account")
        if challenge_id is None:
            return
        try:
            challenge_id = int(challenge_id)
        except (ValueError, TypeError, OverflowError):
            return denied("Invalid challenge ID", 400)
        hint_read = request.method in ("GET", "HEAD") and re.fullmatch(r"/api/v1/hints/\d+", path)
        e = event(lock=write or bool(hint_read))
        if hint_read:
            # As with purchases, ownership/content can change before the lock.
            hint = Hints.query.populate_existing().get(int(path.rsplit("/", 1)[-1]))
            if hint is None:
                return denied("This hint is no longer available", 404)
            challenge_id = hint.challenge_id
        if path == "/api/v1/unlocks" and write:
            # The final lock discards the earlier read transaction. Targets can
            # have moved or disappeared while waiting: authorize the fresh row.
            model = {"hints": Hints, "solutions": Solutions}.get(data.get("type"))
            target = model.query.populate_existing().get(unlock_target_id) if model else None
            if target is None:
                return denied("This unlock is no longer available", 404)
            challenge_id = target.challenge_id
            protected_solution = target if model is Solutions else None
        step = QuestStep.query.filter_by(challenge_id=challenge_id).first()
        if step is None:
            # Unmapped challenges stay in native authoring, never in the event.
            return denied("This challenge is not part of Open Tech Quest", 404)
        user = token_user or get_current_user()
        if user is None:
            return denied("Sign in to open a challenge", 401)
        if (hint_read or (path == "/api/v1/unlocks" and data.get("type") == "hints")) and request.headers.get("Authorization"):
            # Native authentication precedes the lock. A normal participant
            # token can be revoked/expire while waiting; validate without a
            # native helper that may commit or remove expired token rows.
            from CTFd.models import UserTokens
            token = request.headers["Authorization"].split(" ", 1)[-1]
            credential = UserTokens.query.filter_by(value=token, user_id=user.id).first()
            if not request.is_json or not credential or not credential.expiration or credential.expiration <= now():
                return denied("Your access token is invalid or expired", 401)
        if not onboarding.eligible(user):
            return denied("Only eligible participants may access native challenge attempts, hints or unlocks")
        if not user.team_id:
            return denied("An organizer must assign you to a team")
        if e.status == "draft" or not e.start_at or now() < e.start_at or not released(e):
            return denied("The event has not opened")
        if attempt and e.status == "paused":
            message = "The event is paused. Your answer was not submitted; try again when organizers resume."
            return jsonify(success=False, error=message, data={"status": "paused", "message": message}), 409
        if attempt:
            from .room_pauses import active
            if active(step.room_id):
                message = "This room is paused. Your answer was not submitted; try again when organizers resume the room."
                return jsonify(success=False, error=message, data={"status": "room_paused", "message": message}), 409
        if attempt and (e.status != "live" or not ctftime() or (e.end_at and now() > e.end_at)):
            return denied("Submissions are accepted only while the event is live", 409)
        start = QuestStart.query.filter_by(team_id=user.team_id, room_id=step.room_id).first()
        if start is None:
            return denied("Start the room before opening challenge content")
        challenge = Challenges.query.get(challenge_id)
        if not challenge or challenge.state != "visible":
            return denied("This challenge is not available", 404)
        prerequisites = {s.challenge_id for s in participant_steps(step.room_id) if s.position < step.position}
        solved = set(solve_map(e, team_id=user.team_id).get(user.team_id, {}))
        if not solved >= prerequisites:
            return denied("Solve the previous steps before opening this challenge")
        if hint_read:
            from .scoring import hint_access_reason
            reason = hint_access_reason(user.team_id, hint)
            if reason:
                return denied(*reason)
        if protected_solution and protected_solution.state == "solved" and challenge_id not in solved:
            return denied("Solve this challenge with an eligible team member before opening its solution")
        if path.startswith("/files/") and protected_solution and protected_solution.state in ("visible", "solved"):
            from .models import QuestAsset
            managed = QuestAsset.query.get(file.id)
            if managed and managed.challenge_id == challenge_id:
                from CTFd.models import SolutionUnlocks
                if not SolutionUnlocks.query.filter_by(team_id=user.team_id, target=protected_solution.id).first():
                    return denied("Unlock this solution before opening its files")
                from .assets import download_file, member_file
                # Native files only recognizes visible solutions. Managed solved
                # writeups use the effective-solve checks above, never a weaker
                # native unlock or the fact that somebody knows the file URL.
                return download_file(member_file(challenge_id, file.id), managed)
        if path == "/api/v1/unlocks" and write and data.get("type") == "hints":
            from .scoring import purchase_hint
            return purchase_hint(user, target, e)
        if attempt:
            from .recovery import prepare_attempt
            return prepare_attempt(user, challenge, e, solved)

    @app.after_request
    def native_account_result(response):
        staged = getattr(g, "quest_native_account", None)
        if staged:
            response.headers["Cache-Control"] = "private, no-store"
            if response.status_code >= 400:
                db.session.rollback()
            else:
                lifecycle.invalidate(*staged)
        detail = re.fullmatch(r"/api/v1/challenges/\d+/?", request.path)
        hint_read = re.fullmatch(r"/api/v1/hints/\d+/?", request.path)
        if request.method == "GET" and (detail or hint_read):
            response.headers["Cache-Control"] = "private, no-store"
        if request.method == "GET" and hint_read and response.status_code == 200 and response.is_json:
            payload = response.get_json()
            hint = Hints.query.get(int(request.path.rstrip("/").rsplit("/", 1)[-1]))
            if hint and payload.get("success") and isinstance(payload.get("data"), dict):
                from .scoring import hint_quote
                payload["data"]["quote_revision"] = hint_quote(hint)
                response.set_data(__import__("json").dumps(payload))
        if request.method == "GET" and detail and response.status_code == 200 and response.is_json:
            user = get_current_user()
            from .roles import is_maintainer
            if user is None or (user.type != "admin" and not is_maintainer(user)):
                payload = response.get_json()
                data = payload.get("data", {})
                hints = data.get("hints", [])
                from CTFd.models import HintUnlocks
                ids = [hint["id"] for hint in hints]
                unlocked = {target for (target,) in db.session.query(HintUnlocks.target).filter(
                    HintUnlocks.team_id == user.team_id, HintUnlocks.target.in_(ids))} if user and user.team_id else set()
                removed = False
                for hint in hints:
                    if hint["id"] not in unlocked:
                        for key in ("content", "html"):
                            if key in hint:
                                removed = True
                                del hint[key]
                if removed:
                    # Native ctf_ended() also embeds revealed hints in its
                    # pre-rendered HTML. Quest renders its own challenge UI.
                    if "view" in data:
                        data["view"] = ""
                    response.set_data(__import__("json").dumps(payload))
        return response
