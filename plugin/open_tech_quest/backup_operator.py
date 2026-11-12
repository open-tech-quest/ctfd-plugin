"""Private worker-side operations. `extract` never writes to the application."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from . import backup_contract as contract
    from .backup_worker import Profile, WorkerError, write_json, now
    from .backup_openshift import OpenShiftAdapter, DUMP, run, extract_uploads
except ImportError:
    import backup_contract as contract
    from backup_worker import Profile, WorkerError, write_json, now
    from backup_openshift import OpenShiftAdapter, DUMP, run, extract_uploads


def select(profile, selector):
    profile.adapter.sync_inventory(profile.root / "snapshots")
    records = [json.loads(p.read_text()) for p in (profile.root / "snapshots").glob("*.json")]
    candidates = [r for r in records if selector == "latest" or r["storage_id"].startswith(selector)
                  or r["manifest"]["snapshot_id"] == selector]
    if not candidates or (selector != "latest" and len(candidates) != 1):
        raise WorkerError("Backup is missing or ambiguous")
    return max(candidates, key=lambda r: r["manifest"]["created_at"])


def extract(profile, record):
    folder = profile.root / "restores" / (record["manifest"]["snapshot_id"] + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(mode=0o700, parents=True)
    try:
        with profile.materialize(record, operation_locked=True) as (bundle, components):
            shutil.copyfile(bundle, folder / "portable.otqbackup")
            for name, path in components.items():
                shutil.copyfile(path, folder / name)
            extract_uploads(folder / "uploads.tar.gz", folder / "uploads", profile.max_bytes)
            write_json(folder / "manifest.json", record["manifest"])
        return {"snapshot": record["storage_id"], "folder": str(folder), "checksums": "verified"}
    except Exception:
        shutil.rmtree(folder)
        raise


def drill(profile, record):
    """Exact-stack recovery, isolated network, no ports or provider authority."""
    extracted = extract(profile, record)
    source = Path(extracted["folder"])
    try:
        return _drill(profile, record, source)
    finally:
        shutil.rmtree(source)


def _drill(profile, record, source):
    adapter = profile.adapter
    infrastructure = json.loads((source / "infrastructure.json").read_text())
    secret = {k: base64.b64decode(v).decode() for k, v in infrastructure["secret/" + adapter.secret]["data"].items()}
    identity = "otq-backup-drill-" + uuid.uuid4().hex[:12]
    database, cache, application, network = (identity + suffix for suffix in ("-db", "-cache", "-app", "-net"))
    environment = source / "drill.env"
    # Never forward worker keys/tokens, host access or unrelated environment.
    allowed = ("SECRET_KEY", "DATABASE_URL", "REDIS_URL", "MARIADB_PASSWORD", "MARIADB_ROOT_PASSWORD", "REDIS_PASSWORD")
    env = {name: secret[name] for name in allowed}
    database_url, cache_url = urlsplit(env["DATABASE_URL"]), urlsplit(env["REDIS_URL"])
    if not database_url.username or not database_url.hostname or not cache_url.hostname:
        raise WorkerError("Recovery database/cache configuration needs operator handling")
    env.update(MARIADB_DATABASE=adapter.options.get("database_name", "ctfd"), MARIADB_USER=unquote(database_url.username),
               UPLOAD_FOLDER="/var/uploads", LOG_FOLDER="/tmp/ctfd-logs", WORKERS="1")
    if any("\n" in value or "\r" in value for value in env.values()):
        raise WorkerError("Multiline recovery configuration requires operator handling")
    environment.write_text("".join(k + "=" + v + "\n" for k, v in env.items()))
    environment.chmod(0o600)
    image = lambda name: infrastructure["deployment/" + name]["spec"]["template"]["spec"]["containers"][0]["image"]
    try:
        run(["podman", "network", "create", "--internal", network])
        images = [image(name) for name in (adapter.database, adapter.cache, adapter.app)]
        for pinned in images:
            if "@sha256:" not in pinned:
                raise WorkerError("Recovery drills require exact image digest pins")
            run(["podman", "pull", pinned], timeout=600)
        common = ["podman", "run", "-d", "--network", network, "--env-file", environment]
        parameters = record["manifest"].get("database_parameters", {"charset": "utf8mb4", "collation": "utf8mb4_unicode_ci"})
        run(common + ["--name", database, "--network-alias", "database", images[0],
                      "--character-set-server=" + parameters["charset"], "--collation-server=" + parameters["collation"]])
        for _ in range(90):
            ready = subprocess.run(["podman", "exec", database, "healthcheck.sh", "--connect", "--innodb_initialized"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise WorkerError("The drill database did not become ready")
        with (source / "database.sql").open("rb") as sql:
            restored = subprocess.run(["podman", "exec", "-i", database, "sh", "-ec",
                                       'exec mariadb --binary-mode --user=root --password="$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'],
                                      stdin=sql, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if restored.returncode:
            raise WorkerError("The drill database import failed")
        with (source / "roundtrip.sql").open("xb") as output:
            run(["podman", "exec", database, "sh", "-ec", DUMP], output=output)
        if contract.file_digest(source / "roundtrip.sql") != contract.file_digest(source / "database.sql"):
            raise WorkerError("The drill SQL roundtrip differs from the selected backup")
        run(common + ["--name", cache, "--network-alias", "cache", "--entrypoint", "sh", images[1],
                      "-ec", 'exec redis-server --requirepass "$REDIS_PASSWORD"'])
        hosts = []
        for alias, container in ((database_url.hostname, database), (cache_url.hostname, cache)):
            address = run(["podman", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container]).decode().strip()
            if not address:
                raise WorkerError("A drill container has no internal address")
            hosts += ["--add-host", alias + ":" + address]
        probe = r'''import json
from pathlib import Path
from CTFd import create_app
from CTFd.models import db
from sqlalchemy import text
app=create_app()
with app.app_context():
 tables=db.session.execute(text('SHOW TABLES')).scalars().all()
 counts={t:db.session.execute(text('SELECT COUNT(*) FROM `'+t+'`')).scalar() for t in tables}
 locations=db.session.execute(text('SELECT location FROM files')).scalars().all()
 assert all((Path('/var/uploads')/p).is_file() for p in locations),'Missing referenced upload'
 revision=db.session.execute(text("SELECT value FROM config WHERE `key`='open_tech_quest_alembic_version'")).scalar()
 assert app.test_client().get('/login').status_code==200,'Restored login page failed'
 print('RESTORE_PROOF:'+json.dumps({'tables':len(tables),'counts':counts,'schema':revision,'login':200,'referenced_uploads':len(locations)}))
'''
        output = run(["podman", "run", "--rm", "--name", application, "--network", network, *hosts,
                      "--user", "0", "--security-opt=label=disable", "--env-file", environment,
                      "-v", str(source / "uploads") + ":/var/uploads:ro", "--entrypoint", "python", images[2], "-c", probe], timeout=300)
        proof = json.loads(next(line.split(b":", 1)[1] for line in output.splitlines() if line.startswith(b"RESTORE_PROOF:")))
        manifest = record["manifest"]
        if proof["schema"] != manifest["source"]["schema_revision"] or any(proof["counts"].get(k) != v for k, v in manifest["counts"].items() if k != "active_administrators"):
            raise WorkerError("Application startup differs from the selected backup")
        result = {"snapshot": record["storage_id"], "checksums": "verified", "sql_roundtrip": "byte-identical",
                  "isolation": "internal Podman network, no published ports, no cluster access", "application": proof}
        write_json(profile.root / "latest-drill.json", result)
        (profile.root / "drill-failure.json").unlink(missing_ok=True)
        return result
    finally:
        for container in (application, cache, database):
            subprocess.run(["podman", "rm", "-fv", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["podman", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("action", choices=("init", "backup", "status", "snapshots", "check", "extract", "drill"))
    parser.add_argument("--snapshot", default="latest")
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    config = next(p for p in json.loads(args.config.read_text())["profiles"] if p["id"] == args.profile)
    profile = Profile(config, OpenShiftAdapter(config))
    if args.action == "backup":
        body = {"operation_id": str(uuid.uuid4()), "scheduled": args.scheduled}
        try:
            result = profile.enqueue("backup", None, body, synchronous=True)["job"]
        except WorkerError as error:
            if not args.scheduled:
                raise
            # A busy repository can refuse before enqueue creates a job. Keep
            # that genuine scheduled attempt visible without touching the
            # running operation or making a manual attempt look scheduled.
            stamp = now()
            job = {"id": body["operation_id"], "operation_id": body["operation_id"], "kind": "backup",
                   "status": "failed", "stage": "not_started", "message": str(error),
                   "created_at": stamp, "updated_at": stamp, "scheduled": True,
                   "actor": None, "reason": "Scheduled backup",
                   "request_hash": hashlib.sha256(contract.canonical({"kind": "backup", "actor": None, "body": body, "snapshot": None})).hexdigest()}
            write_json(profile._job_path(job["id"]), job)
            write_json(profile.root / "backup-failure.json", {"at": stamp, "job_id": job["id"]})
            result = profile.public_job(job)
        print(json.dumps(result))
        if result["status"] != "succeeded":
            raise SystemExit(1)
        return
    with (profile.root / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        adapter = profile.adapter
        if args.action == "init":
            adapter.restic(["init", "--repository-version", "2"])
            result = {"initialized": True}
        elif args.action == "snapshots":
            result = json.loads(adapter.restic(["snapshots", "--tag", adapter.options["restic_tag"], "--host", adapter.options["restic_host"], "--json"]))
        elif args.action == "check":
            adapter.restic(["check", "--read-data"])
            result = {"repository": "verified"}
        elif args.action in ("extract", "drill"):
            record = select(profile, args.snapshot)
            try:
                result = extract(profile, record) if args.action == "extract" else drill(profile, record)
            except Exception:
                if args.action == "drill":
                    write_json(profile.root / "drill-failure.json", {"at": datetime.now(timezone.utc).isoformat()})
                raise
        else:
            result = {"schedule": adapter.schedule(), "backup_failed": (profile.root / "backup-failure.json").exists(),
                      "drill_failed": (profile.root / "drill-failure.json").exists()}
            scheduled = sorted((json.loads(path.read_text()) for path in (profile.root / "jobs").glob("*.json")),
                               key=lambda job: job["created_at"], reverse=True)
            scheduled = [job for job in scheduled if job.get("scheduled")]
            latest = scheduled[0] if scheduled else None
            result["schedule"]["last_job"] = profile.public_job(latest) if latest else None
            result["backup_failed"] = result["backup_failed"] or bool(latest and latest["status"] == "failed")
            successful = next((job for job in scheduled if job["status"] == "succeeded"), None)
            if successful:
                result["schedule"]["last_success_at"] = successful["updated_at"]
            success = profile.root / "latest-success.json"
            result["last_success"] = json.loads(success.read_text()) if success.exists() else None
            if result["last_success"]:
                result["age_seconds"] = (datetime.now(timezone.utc) - datetime.fromisoformat(result["last_success"]["created"].replace("Z", "+00:00"))).total_seconds()
                result["stale"] = result["age_seconds"] > 2 * adapter.options.get("schedule", {}).get("interval_seconds", 900)
            files = [p for p in (adapter.repository / "repo").rglob("*") if p.is_file()]
            result["repo_bytes"] = sum(p.stat().st_size for p in files)
            result["repo_allocated_bytes"] = sum(p.stat().st_blocks * 512 for p in files)
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, (WorkerError, contract.BackupError)) else type(error).__name__
        print("Backup operator failed: " + message, file=sys.stderr)
        raise SystemExit(1)
