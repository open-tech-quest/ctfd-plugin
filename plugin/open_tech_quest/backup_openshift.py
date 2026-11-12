"""Configurable OpenShift deployment adapter for the external backup worker."""
import base64
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import backup_contract as contract
    from .backup_worker import WorkerError, write_json, now
except ImportError:
    import backup_contract as contract
    from backup_worker import WorkerError, write_json, now

RESTIC_IMAGE = "docker.io/restic/restic:0.19.1@sha256:08916bcda4a4435f9d9828ebb4e91bb7ada3d2c8a53699788930e0ae1bd4fa67"
DUMP = 'exec mariadb-dump --user=root --password="$MARIADB_ROOT_PASSWORD" --single-transaction --quick --routines --triggers --events --hex-blob --order-by-primary --skip-comments --skip-dump-date "$MARIADB_DATABASE"'


def run(args, data=None, output=None, timeout=180):
    result = subprocess.run([str(a) for a in args], input=data, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise WorkerError("A deployment backup command failed. An operator must inspect the recovery target.", 503)
    return result.stdout


def digest(value):
    return hashlib.sha256(contract.canonical(value)).hexdigest()


def pack_uploads(source, destination):
    """Stable bytes reproduce the signed component after a Restic extraction."""
    with Path(destination).open("xb") as output:
        # The signed component must reproduce across worker runtimes. Python
        # 3.15 changed gzip's default from 9 to 6; format v1 uses level 9.
        with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0, compresslevel=9) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(Path(source).rglob("*")):
                    if path.is_symlink() or not (path.is_file() or path.is_dir()):
                        raise WorkerError("Uploaded symlinks or special files require operator recovery")
                    info = archive.gettarinfo(str(path), arcname=str(path.relative_to(source)))
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.pax_headers = {}
                    info.mode = 0o755 if info.isdir() else 0o644
                    if path.is_file():
                        with path.open("rb") as content:
                            archive.addfile(info, content)
                    else:
                        archive.addfile(info)


def extract_uploads(source, destination, maximum=contract.DEFAULT_MAX_BYTES):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    seen, total = set(), 0
    with tarfile.open(source, "r:*") as archive:
        for entry in archive:
            if entry.isdir() and entry.name.rstrip("/") == ".":
                continue
            parts = Path(entry.name).parts
            if not parts or Path(entry.name).is_absolute() or ".." in parts or entry.name in seen:
                raise WorkerError("The upload archive contains an unsafe or duplicate path")
            seen.add(entry.name)
            if not (entry.isfile() or entry.isdir()):
                raise WorkerError("Uploaded symlinks or special files require operator recovery")
            total += entry.size
            if total > maximum:
                raise WorkerError("The extracted uploads exceed the configured size limit")
            target = destination.joinpath(*parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with archive.extractfile(entry) as content, target.open("xb") as output:
                    shutil.copyfileobj(content, output)
                target.chmod(0o644)


PROBE = r'''import hashlib,json,os,re
from pathlib import Path
from sqlalchemy import create_engine,text
from CTFd import __version__
engine=create_engine(os.environ['DATABASE_URL'])
def h(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
with engine.connect() as c:
 tables=c.execute(text('SHOW TABLES')).scalars().all();ddl={}
 for table in sorted(tables):
  assert re.fullmatch('[A-Za-z0-9_]+',table)
  ddl[table]=re.sub(r' AUTO_INCREMENT=\d+','',c.execute(text('SHOW CREATE TABLE `'+table+'`')).one()[1])
 engines=c.execute(text('SELECT ENGINE FROM information_schema.tables WHERE table_schema=DATABASE()')).scalars().all()
 assert all(v=='InnoDB' for v in engines),'Backup requires InnoDB tables'
 event=dict(c.execute(text('SELECT * FROM quest_events WHERE id=1')).mappings().one())
 revision=c.execute(text("SELECT value FROM config WHERE `key`='open_tech_quest_alembic_version'")).scalar_one()
 native=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
 parameters=dict(c.execute(text('SELECT SCHEMA_NAME AS name,DEFAULT_CHARACTER_SET_NAME AS charset,DEFAULT_COLLATION_NAME AS collation FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()')).mappings().one())
 epoch=c.execute(text("SELECT MAX(id) FROM quest_audit WHERE action='event.new_round'")).scalar()
 counts={name:c.execute(text('SELECT COUNT(*) FROM `'+name+'`')).scalar_one() for name in ('users','teams','challenges','solves','submissions','hints','files')}
 counts['active_administrators']=c.execute(text("SELECT COUNT(*) FROM users WHERE type='admin' AND banned=0")).scalar_one()
root=Path(os.environ.get('UPLOAD_FOLDER','/var/uploads'));files={}
for p in sorted(root.rglob('*')) if os.environ.get('OTQ_BACKUP_SCAN_UPLOADS','1')=='1' else []:
 assert not p.is_symlink(),'Symlinks require operator review'
 if p.is_file():
  checksum=hashlib.sha256()
  with p.open('rb') as content:
   for block in iter(lambda:content.read(1024*1024),b''):checksum.update(block)
  files[str(p.relative_to(root))]=checksum.hexdigest()
print(json.dumps({'schema':revision,'ctfd_schema':native,'database_parameters':parameters,'schema_fingerprint':h([ddl,parameters]),'files':files,'counts':counts,
 'event_name':event['name'],'event_status':event['status'],'round':h([event.get('start_at'),epoch]),
 'ctfd_version':__version__,'plugin_version':os.environ.get('OTQ_PLUGIN_VERSION','unknown')}))
'''

ACTOR = r'''import hashlib,hmac,json,os,sys
from sqlalchemy import create_engine,text
actor=json.load(sys.stdin);valid=False
if type(actor.get('id')) is int and actor['id']>0:
 with create_engine(os.environ['DATABASE_URL']).connect() as c:
  row=c.execute(text('SELECT id,type,banned,created,password,secret FROM users WHERE id=:id'),{'id':actor['id']}).mappings().first()
  if row and row['type']=='admin' and not row['banned']:
   created=row['created'].isoformat(timespec='microseconds')+'Z' if row['created'] else None
   password=hashlib.sha256((row['password'] or '').encode()).hexdigest()
   secret=hashlib.sha256((row['secret'] or '').encode()).hexdigest()
   valid=created==actor.get('created_at') and isinstance(actor.get('password_sha256'),str) and isinstance(actor.get('secret_sha256'),str) and hmac.compare_digest(password,actor['password_sha256']) and hmac.compare_digest(secret,actor['secret_sha256'])
print(json.dumps({'valid':valid}))
'''


class OpenShiftAdapter:
    def __init__(self, config):
        self.config, self.options = config, config["adapter"]
        self.root = Path(config["state_root"])
        self.repository = Path(self.options.get("repository_root", self.root))
        self.namespace = self.options.get("namespace", "ctfd")
        self.ocbase = list(self.options["oc_command"]) + ["--request-timeout=45s", "-n", self.namespace]
        self.app = self.options.get("application_deployment", "ctfd")
        self.database = self.options.get("database_deployment", "database")
        self.cache = self.options.get("cache_deployment", "cache")
        self.secret = self.options.get("application_secret", "ctfd-secrets")
        self.route = self.options.get("route", "ctfd")
        self.helper = None
        self.journal = self.root / "recovery.json"

    def oc(self, args, data=None, output=None, timeout=180):
        return run(self.ocbase + list(args), data, output, timeout)

    def get(self, kind, name=None, namespace=None):
        args = (["-n", namespace] if namespace else []) + ["get", kind]
        return json.loads(self.oc(args + ([name] if name else []) + ["-o", "json"]))

    def target(self):
        view = json.loads(run(list(self.options["oc_command"]) + ["config", "view", "--minify", "-o", "json"]))
        if view["clusters"][0]["cluster"]["server"] != self.options["server"]:
            raise WorkerError("The Kubernetes API target changed; operator qualification is required")
        if self.get("route", self.route)["spec"]["host"] != self.options["route_host"]:
            raise WorkerError("The application route changed; operator qualification is required")
        pvcs = {p["metadata"]["name"]: p["metadata"]["uid"] for p in self.get("pvc")["items"]}
        if pvcs != self.options["pvc_uids"]:
            raise WorkerError("Persistent storage identity changed; operator qualification is required")

    def infrastructure(self):
        result = {}
        groups = [("deployment", [self.app, self.database, self.cache]), ("secret", [self.secret])]
        for kind, names in groups:
            for name in names:
                value = self.get(kind, name)
                item = {key: value[key] for key in ("apiVersion", "kind", "spec", "data", "type") if key in value}
                item["metadata"] = {"name": name, "namespace": self.namespace}
                if kind == "deployment":
                    for container in item["spec"]["template"]["spec"].get("containers", []):
                        for env in container.get("env", []):
                            if env["name"] == "OTQ_BACKUP_WORKER_TOKEN":
                                reference = env.get("valueFrom", {}).get("secretKeyRef", {})
                                if not reference.get("name") or reference["name"] == self.secret:
                                    raise WorkerError("Keep backup-worker credentials in a separate referenced Secret, outside the captured application credentials")
                result[kind + "/" + name] = item
        for kind in ("configmap", "service", "pvc"):
            for value in self.get(kind)["items"]:
                item = {key: value[key] for key in ("apiVersion", "kind", "spec", "data") if key in value}
                item["metadata"] = {"name": value["metadata"]["name"], "namespace": self.namespace}
                result[kind + "/" + value["metadata"]["name"]] = item
        return result

    def app_python(self, code, data=None):
        target = "pod/" + self.helper if self.helper else "deploy/" + self.app
        container = "helper" if self.helper else self.options.get("application_container", "ctfd")
        return self.oc(["exec", "-i", target, "-c", container, "--", "python", "-c", code], data=data)

    def probe(self, scan=True):
        prefix = "import os;os.environ['OTQ_BACKUP_SCAN_UPLOADS']='0'\n" if not scan else ""
        return json.loads(self.app_python(prefix + PROBE))

    def actor_valid(self, actor):
        return json.loads(self.app_python(ACTOR, contract.canonical(actor))).get("valid") is True

    def current(self):
        self.target()
        if not self.helper:
            self._require_ready()
        infra, probe = self.infrastructure(), self.probe(scan=False)
        if probe["database_parameters"]["name"] != self.options.get("database_name", "ctfd"):
            raise WorkerError("The application database differs from the configured recovery target")
        image = infra["deployment/" + self.app]["spec"]["template"]["spec"]["containers"][0]["image"]
        if "@sha256:" not in image:
            raise WorkerError("Automatic restore requires immutable image digest pins")
        source = {"ctfd_version": probe["ctfd_version"], "plugin_version": probe["plugin_version"],
                  "schema_revision": probe["schema"], "ctfd_schema_revision": probe["ctfd_schema"],
                  "image_digest": "sha256:" + image.rsplit("@sha256:", 1)[1]}
        secret_fingerprint = digest(infra["secret/" + self.secret]["data"])
        target = {"id": self.config["instance_id"], "name": self.config.get("instance_name", probe["event_name"]),
                  "origin": "https://" + self.options["route_host"], "current": source}
        generation = digest([target, probe["schema_fingerprint"], probe["round"], secret_fingerprint, self.options["pvc_uids"]])
        return {"target": target, "counts": probe["counts"], "generation": generation,
                "configuration": secret_fingerprint, "schema_fingerprint": probe["schema_fingerprint"],
                "database_parameters": probe["database_parameters"],
                "data_images": {name: infra["deployment/" + deployment]["spec"]["template"]["spec"]["containers"][0]["image"]
                                for name, deployment in (("database", self.database), ("cache", self.cache))},
                "_infrastructure": infra, "_probe": probe}

    def configuration_reason(self, manifest, current):
        if not self.options.get("restore_enabled", False):
            return "Automatic restore has not been enabled by this installation's operator"
        if manifest.get("configuration") != current["configuration"]:
            return "Runtime credentials/configuration differ; operator recovery is required"
        if manifest.get("schema_fingerprint") != current["schema_fingerprint"]:
            return "The complete database structure differs; operator qualification is required"
        if manifest.get("data_images") != current["data_images"]:
            return "Database/cache image versions differ; operator qualification is required"
        if manifest.get("database_parameters") != current["database_parameters"]:
            return "Database identity or character settings differ; operator recovery is required"
        if not manifest["counts"].get("active_administrators", 0):
            return "The backup has no active administrator; operator account recovery is required"
        return None

    def capture(self, stage):
        stage = Path(stage)
        stable = ("schema", "ctfd_schema", "schema_fingerprint", "files", "ctfd_version", "plugin_version")
        for attempt in range(3):
            for path in stage.iterdir():
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            self.target()
            if not self.helper:
                self._require_ready()
            before_infra, before = self.infrastructure(), self.probe()
            with (stage / "database.sql").open("xb") as output:
                self.oc(["exec", "deploy/" + self.database, "--", "sh", "-ec", DUMP], output=output)
            if (stage / "database.sql").stat().st_size < 1000:
                raise WorkerError("The database dump is empty")
            archive = stage / "capture.tar"
            target = "pod/" + self.helper if self.helper else "deploy/" + self.app
            container = "helper" if self.helper else self.options.get("application_container", "ctfd")
            with archive.open("xb") as output:
                self.oc(["exec", target, "-c", container, "--", "tar", "-C", "/var/uploads", "-cf", "-", "."], output=output)
            extract_uploads(archive, stage / "uploads", self.config.get("max_backup_bytes", contract.DEFAULT_MAX_BYTES))
            archive.unlink()
            after, after_infra = self.probe(), self.infrastructure()
            actual = {str(p.relative_to(stage / "uploads")): contract.file_digest(p)["sha256"]
                      for p in (stage / "uploads").rglob("*") if p.is_file()}
            if all(before[k] == after[k] for k in stable) and before["files"] == actual and before_infra == after_infra:
                break
            if attempt == 2:
                raise WorkerError("Uploads, schema or runtime configuration changed during capture. Retry after the upgrade or edit finishes.")
            time.sleep(2)
        write_json(stage / "infrastructure.json", before_infra)
        image = before_infra["deployment/" + self.app]["spec"]["template"]["spec"]["containers"][0]["image"]
        database_image = before_infra["deployment/" + self.database]["spec"]["template"]["spec"]["containers"][0]["image"]
        inspected = self.inspect_sql(stage / "database.sql", database_image)
        if inspected["schema_revision"] != before["schema"] or inspected["ctfd_schema_revision"] != before["ctfd_schema"]:
            raise WorkerError("The captured database schema does not match its producer")
        for location in inspected.get("files", []):
            path = Path(location)
            if path.is_absolute() or ".." in path.parts or not (stage / "uploads" / path).is_file():
                raise WorkerError("A database-referenced upload is missing or invalid. Repair it before publishing a recovery snapshot.")
        files = {str(p.relative_to(stage)): contract.file_digest(p) for p in stage.rglob("*") if p.is_file()}
        # Retain the established raw capture layout for existing operator drills.
        legacy = {"format": 1, "created": now(), "target": self.options["server"], "route": self.options["route_host"],
                  "schema": before["schema"], "files": files, "uploads": actual,
                  "raw_bytes": sum(p["bytes"] for p in files.values()),
                  "consistency": "InnoDB single transaction; verified stable uploads/schema/runtime configuration"}
        write_json(stage / "manifest.json", legacy)
        return {"source": {"ctfd_version": before["ctfd_version"], "plugin_version": before["plugin_version"],
                           "schema_revision": before["schema"], "ctfd_schema_revision": before["ctfd_schema"],
                           "image_digest": image.rsplit("@", 1)[-1]},
                "instance": {"id": self.config["instance_id"], "name": self.config.get("instance_name", before["event_name"])},
                "counts": inspected["counts"], "configuration": digest(before_infra["secret/" + self.secret]["data"]),
                "schema_fingerprint": before["schema_fingerprint"], "database_parameters": before["database_parameters"],
                "data_images": {name: before_infra["deployment/" + deployment]["spec"]["template"]["spec"]["containers"][0]["image"]
                                for name, deployment in (("database", self.database), ("cache", self.cache))}}

    def components(self, stage):
        stage = Path(stage)
        if not (stage / "uploads.tar.gz").exists():
            pack_uploads(stage / "uploads", stage / "uploads.tar.gz")
        return {name: stage / name for name in contract.COMPONENTS}

    def restic(self, args, stage=None, writable=True):
        command = ["podman", "run", "--rm", "--network=none", "--security-opt=label=disable",
                   "-v", str(self.repository / "repo") + ":/repo" + ("" if writable else ":ro"),
                   "-v", str(self.repository / "password") + ":/password:ro",
                   "-e", "RESTIC_REPOSITORY=/repo", "-e", "RESTIC_PASSWORD_FILE=/password"]
        if stage:
            command += ["-v", str(stage) + ":/source:ro"]
        return run(command + [RESTIC_IMAGE, "--no-cache"] + list(args), timeout=900)

    def publish(self, stage, manifest, signature):
        stage = Path(stage)
        write_json(stage / "portable-manifest.json", manifest)
        write_json(stage / "portable-signature.json", signature)
        # Restic sees raw SQL and individual uploads, preserving chunk deduplication.
        (stage / "portable.otqbackup").unlink(missing_ok=True)
        (stage / "uploads.tar.gz").unlink(missing_ok=True)
        lines = self.restic(["backup", "/source", "--host", self.options["restic_host"],
                             "--tag", self.options["restic_tag"], "--tag", "otq-signed-v1", "--json"], stage).splitlines()
        summary = next(json.loads(line) for line in lines if json.loads(line).get("message_type") == "summary")
        result = {"created": manifest["created_at"], "snapshot": summary["snapshot_id"],
                  "schema": manifest["source"]["schema_revision"], "raw_bytes": sum(c["bytes"] for c in manifest["components"].values())}
        write_json(self.repository / "latest-success.json", result)
        with (self.repository / "history.jsonl").open("a") as history:
            history.write(json.dumps(result) + "\n")
        (self.repository / "backup-failure.json").unlink(missing_ok=True)
        self.maintenance()
        return summary["snapshot_id"]

    def inspect_sql(self, sql, image):
        """Inspect the dump's actual snapshot, not changing live row counts."""
        if not isinstance(image, str) or "@sha256:" not in image:
            raise WorkerError("Database inspection requires a pinned image")
        private = Path(tempfile.mkdtemp(prefix="inspect-", dir=self.root / "temporary"))
        identity = "otq-backup-inspect-" + uuid.uuid4().hex[:12]
        database_name = self.options.get("database_name", "ctfd")
        if not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
            raise WorkerError("Configure a simple database identifier")
        environment = private / "database.env"
        environment.write_text("MARIADB_ROOT_PASSWORD=" + secrets.token_hex(24) + "\nMARIADB_DATABASE=" + database_name + "\n")
        environment.chmod(0o600)
        try:
            run(["podman", "run", "-d", "--name", identity, "--network=none", "--env-file", environment,
                 image, "--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci"])
            for _ in range(90):
                result = subprocess.run(["podman", "exec", identity, "healthcheck.sh", "--connect", "--innodb_initialized"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise WorkerError("The isolated backup database did not become ready")
            command = ["podman", "exec", "-i", identity, "sh", "-ec",
                       'exec mariadb --binary-mode --user=root --password="$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"']
            with Path(sql).open("rb") as source:
                result = subprocess.run(command, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
            if result.returncode:
                raise WorkerError("The backup database could not be imported in isolation")
            roundtrip = private / "roundtrip.sql"
            with roundtrip.open("xb") as output:
                run(["podman", "exec", identity, "sh", "-ec", DUMP], output=output)
            if contract.file_digest(roundtrip) != contract.file_digest(sql):
                raise WorkerError("The isolated database roundtrip differs from this backup")
            counts = ("users", "teams", "challenges", "solves", "submissions", "hints", "files")
            expressions = ["'" + name + "',(SELECT COUNT(*) FROM `" + name + "`)" for name in counts]
            expressions.append("'active_administrators',(SELECT COUNT(*) FROM users WHERE type='admin' AND banned=0)")
            query = ("SELECT JSON_OBJECT('schema_revision',(SELECT value FROM config WHERE `key`='open_tech_quest_alembic_version'),"
                     "'ctfd_schema_revision',(SELECT version_num FROM alembic_version),"
                     "'files',COALESCE((SELECT JSON_ARRAYAGG(location) FROM files),JSON_ARRAY()),"
                     "'counts',JSON_OBJECT(" + ",".join(expressions) + "));")
            result = run(["podman", "exec", "-i", identity, "sh", "-ec",
                          'exec mariadb --batch --raw --skip-column-names --user=root --password="$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'], data=query.encode())
            inspected = json.loads(result)
            if isinstance(inspected.get("files"), str):
                inspected["files"] = json.loads(inspected["files"])
            return inspected
        finally:
            subprocess.run(["podman", "rm", "-fv", identity], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            shutil.rmtree(private)

    def materialize(self, storage_id, target):
        if not isinstance(storage_id, str) or not re.fullmatch(r"[0-9a-f]{8,64}", storage_id):
            raise WorkerError("The stored backup identity is invalid")
        target = Path(target)
        raw = target / "raw"
        raw.mkdir(mode=0o700)
        command = ["podman", "run", "--rm", "--network=none", "--security-opt=label=disable",
                   "-v", str(self.repository / "repo") + ":/repo:ro",
                   "-v", str(self.repository / "password") + ":/password:ro", "-v", str(raw) + ":/restore",
                   "-e", "RESTIC_REPOSITORY=/repo", "-e", "RESTIC_PASSWORD_FILE=/password",
                   RESTIC_IMAGE, "--no-cache", "--no-lock", "restore", storage_id, "--target", "/restore", "--verify"]
        run(command, timeout=900)
        source = raw / "source"
        for path in source.iterdir():
            shutil.move(str(path), str(target / path.name))
        shutil.rmtree(raw)

    def publish_bundle(self, bundle, manifest, record):
        stage = Path(tempfile.mkdtemp(prefix="import-", dir=self.root / "temporary"))
        try:
            shutil.copyfile(bundle, stage / "portable.otqbackup")
            write_json(stage / "portable-record.json", record)
            lines = self.restic(["backup", "/source", "--host", self.options["restic_host"],
                                 "--tag", self.options["restic_tag"], "--tag", "otq-signed-v1", "--json"], stage).splitlines()
            return next(json.loads(line)["snapshot_id"] for line in lines if json.loads(line).get("message_type") == "summary")
        finally:
            shutil.rmtree(stage)

    def schedule(self):
        settings = self.options.get("schedule", {})
        timer = settings.get("timer")
        enabled = False
        if timer:
            check = subprocess.run(["systemctl", "is-active", timer], capture_output=True, timeout=10)
            enabled = check.returncode == 0
        interval = settings.get("interval_seconds", 900)
        latest = self.repository / "latest-success.json"
        success = json.loads(latest.read_text()).get("created") if latest.exists() else None
        next_time = datetime.fromtimestamp((int(time.time()) // interval + 1) * interval, timezone.utc).isoformat().replace("+00:00", "Z") if enabled else None
        return {"enabled": enabled, "interval_seconds": interval, "last_success_at": success, "next_run_at": next_time}

    def sync_inventory(self, directory):
        directory = Path(directory)
        snapshots = json.loads(self.restic(["snapshots", "--tag", self.options["restic_tag"], "--host", self.options["restic_host"], "--json"]))
        records = {}
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text())
                records.setdefault(record["storage_id"], []).append((path, record))
            except FileNotFoundError:
                continue
        existing = {snapshot["id"] for snapshot in snapshots}
        for storage_id, values in records.items():
            if not any(identifier.startswith(storage_id) for identifier in existing):
                for path, _ in values:
                    path.unlink(missing_ok=True)
        for snapshot in snapshots:
            identifier = snapshot["id"]
            known = [entry for key, values in records.items() if identifier.startswith(key) for entry in values]
            if known:
                continue
            if "otq-signed-v1" in snapshot.get("tags", []):
                # Recover the small index if capture completed just before a worker crash.
                raw = self.restic(["dump", identifier, "/source/portable-record.json"])
                record = json.loads(raw)
                contract.validate_manifest(record["manifest"])
                record["storage_id"] = identifier
            else:
                public_id = hashlib.sha256(("legacy:" + identifier).encode()).hexdigest()[:32]
                record = {"storage_id": identifier, "legacy_storage": True, "origin": "legacy", "bytes": 0, "sha256": "",
                          "signing_key_id": None,
                          "manifest": {"snapshot_id": public_id, "created_at": snapshot["time"], "legacy": True,
                                       "instance": {"id": self.config["instance_id"], "name": self.config["instance_name"]},
                                       "source": {"ctfd_version": "unknown", "plugin_version": "legacy", "schema_revision": "unknown",
                                                  "ctfd_schema_revision": "unknown", "image_digest": ""}, "counts": {}}}
            write_json(directory / (record["manifest"]["snapshot_id"] + ".json"), record)

    def legacy_manifest(self, stage, record):
        stage = Path(stage)
        infra = json.loads((stage / "infrastructure.json").read_text())
        app_image = infra["deployment/" + self.app]["spec"]["template"]["spec"]["containers"][0]["image"]
        db_image = infra["deployment/" + self.database]["spec"]["template"]["spec"]["containers"][0]["image"]
        inspected = self.inspect_sql(stage / "database.sql", db_image)
        match = re.search(r":([^:@/]+)@sha256:", app_image)
        return {"format": contract.FORMAT, "schema_version": contract.SCHEMA_VERSION,
                "snapshot_id": record["manifest"]["snapshot_id"], "created_at": record["manifest"]["created_at"],
                "instance": record["manifest"]["instance"], "legacy": True,
                "source": {"ctfd_version": "unknown", "plugin_version": match.group(1) if match else "unknown",
                           "schema_revision": inspected["schema_revision"], "ctfd_schema_revision": inspected["ctfd_schema_revision"],
                           "image_digest": app_image.rsplit("@", 1)[-1]}, "counts": inspected["counts"]}

    def maintenance(self):
        marker = self.repository / "maintenance.json"
        day = datetime.now(timezone.utc).date().isoformat()
        if marker.exists() and json.loads(marker.read_text()).get("day") == day:
            return
        self.restic(["check", "--read-data"])
        self.restic(["forget", "--tag", self.options["restic_tag"], "--group-by", "tags", "--keep-within", "10d", "--keep-last", "1", "--prune"])
        snapshots = json.loads(self.restic(["snapshots", "--tag", self.options["restic_tag"], "--json"]))
        ids = [snapshot["id"] for snapshot in snapshots]
        for path in (self.root / "snapshots").glob("*.json"):
            storage_id = json.loads(path.read_text())["storage_id"]
            if not any(identifier.startswith(storage_id) for identifier in ids):
                path.unlink()
        write_json(marker, {"day": day})

    def validate_uploaded_bundle(self, bundle, manifest):
        private = Path(tempfile.mkdtemp(prefix="validate-", dir=self.root / "temporary"))
        try:
            with zipfile.ZipFile(bundle) as archive:
                for name in contract.COMPONENTS:
                    with archive.open(name) as source, (private / name).open("xb") as target:
                        shutil.copyfileobj(source, target)
            extract_uploads(private / "uploads.tar.gz", private / "uploads", self.config.get("max_backup_bytes", contract.DEFAULT_MAX_BYTES))
            if manifest["instance"]["id"] != self.config["instance_id"] or manifest.get("legacy"):
                return  # Authenticated operator material, never an automatic importer.
            current = self.current()
            sources = [current["target"]["current"]] + [item["source"] for item in self.config.get("qualified_restores", []) if item.get("target") == current["target"]["current"]]
            if not contract.compatibility(manifest, sources, current["target"]["current"])["supported"]:
                return  # Store authenticated opaque data for an operator; do not run unknown SQL.
            infra = json.loads((private / "infrastructure.json").read_text())
            image = infra["deployment/" + self.database]["spec"]["template"]["spec"]["containers"][0]["image"]
            inspected = self.inspect_sql(private / "database.sql", image)
            if inspected["counts"] != manifest["counts"]:
                raise WorkerError("The database counts differ from the signed backup metadata")
            if any(inspected[key] != manifest["source"][key] for key in ("schema_revision", "ctfd_schema_revision")):
                raise WorkerError("The database revisions differ from the signed backup metadata")
            for location in inspected.get("files", []):
                path = Path(location)
                if path.is_absolute() or ".." in path.parts or not (private / "uploads" / path).is_file():
                    raise WorkerError("A database-referenced uploaded file is missing or invalid")
        finally:
            shutil.rmtree(private)

    def _save_recovery(self, value):
        write_json(self.journal, value)

    def _require_ready(self):
        deployment = self.get("deployment", self.app)
        status = deployment["status"]
        if (deployment["spec"].get("replicas") != 1 or status.get("readyReplicas") != 1
                or status.get("updatedReplicas") != 1
                or status.get("observedGeneration", 0) < deployment["metadata"]["generation"]):
            raise WorkerError("Wait for the current application rollout to become healthy before backing up or restoring")

    def _argo(self, annotation):
        patch = {"metadata": {"annotations": {"argocd.argoproj.io/skip-reconcile": annotation}}}
        self.oc(["-n", self.options.get("argo_namespace", "openshift-gitops"), "patch", "application",
                 self.options["argo_application"], "--type=merge", "-p", json.dumps(patch)])

    def _scale(self, replicas):
        self.oc(["scale", "deployment/" + self.app, "--replicas=" + str(replicas)])

    def _application_pods(self):
        return [pod for pod in self.get("pods")["items"]
                if pod["metadata"].get("labels", {}).get("app.kubernetes.io/name") == self.options.get("application_label", "ctfd")]

    def _helper(self, image, node, spec, job_id):
        # A predeclared zero-replica Deployment keeps recovery access scoped to
        # two named scale resources; the worker never creates arbitrary pods.
        deployment = self.options.get("helper_deployment", "ctfd-backup-helper")
        value = self.get("deployment", deployment)
        self._validate_helper(value["spec"]["template"]["spec"], spec)
        container = value["spec"]["template"]["spec"]["containers"][0]
        if container["image"] != image or container["name"] != "helper":
            raise WorkerError("The configured helper image differs from the application; operator qualification is required")
        self.oc(["scale", "deployment/" + deployment, "--replicas=1"])
        for _ in range(180):
            pods = [p for p in self.get("pods")["items"]
                    if p["metadata"].get("labels", {}).get("app.kubernetes.io/name") == deployment
                    and not p["metadata"].get("deletionTimestamp")]
            if len(pods) == 1 and pods[0]["status"].get("phase") == "Running":
                statuses = pods[0]["status"].get("containerStatuses", [])
                if statuses and all(c.get("ready") for c in statuses):
                    self.helper = pods[0]["metadata"]["name"]
                    self._validate_helper(pods[0]["spec"], spec)
                    return self.helper
            time.sleep(1)
        raise WorkerError("The restore helper did not start; recovery remains in maintenance")

    def _validate_helper(self, helper, application):
        volume_name = self.options.get("data_volume", "data")
        app_volume = next(v for v in application["volumes"] if v["name"] == volume_name)
        helper_volume = next(v for v in helper["volumes"] if v["name"] == volume_name)
        if app_volume.get("persistentVolumeClaim") != helper_volume.get("persistentVolumeClaim"):
            raise WorkerError("The recovery helper does not mount the application's data claim")
        app = next(c for c in application["containers"] if c["name"] == self.options.get("application_container", "ctfd"))
        other = next(c for c in helper["containers"] if c["name"] == "helper")
        if app["image"] != other["image"]:
            raise WorkerError("The recovery helper does not use the application's pinned image")
        for path in ("/var/uploads", "/var/quest-backup"):
            expected = next(m for m in app["volumeMounts"] if m["mountPath"] == path)
            actual = next(m for m in other["volumeMounts"] if m["mountPath"] == path)
            if expected != actual or actual["name"] != volume_name:
                raise WorkerError("The recovery helper storage paths differ from the application")
        app_env = {e["name"]: e for e in app.get("env", [])}
        helper_env = {e["name"]: e for e in other.get("env", [])}
        if app.get("envFrom", []) != other.get("envFrom", []):
            raise WorkerError("The recovery helper inherited configuration differs from the application")
        if any(app_env.get(name) != helper_env.get(name) for name in ("SECRET_KEY", "DATABASE_URL", "REDIS_URL")):
            raise WorkerError("The recovery helper database/session credentials differ from the application")

    def fence_and_stop(self, job_id, actor=None):
        if not self.options.get("restore_enabled", False):
            raise WorkerError("Automatic restoration is not enabled by this installation's operator")
        self.target()
        self._require_ready()
        deployment = self.get("deployment", self.app)
        pods = self._application_pods()
        if len(pods) != 1 or deployment["spec"].get("replicas") != 1:
            raise WorkerError("Automatic restore requires one healthy application replica")
        helper = self.get("deployment", self.options.get("helper_deployment", "ctfd-backup-helper"))
        if helper["spec"].get("replicas", 0) != 0:
            raise WorkerError("A recovery helper is already active; operator inspection is required")
        self._validate_helper(helper["spec"]["template"]["spec"], deployment["spec"]["template"]["spec"])
        argo = self.get("application", self.options["argo_application"], self.options.get("argo_namespace", "openshift-gitops"))
        journal = {"job_id": job_id, "stage": "fencing", "started_at": now(),
                   "replicas": deployment["spec"]["replicas"],
                   "argo_skip": argo["metadata"].get("annotations", {}).get("argocd.argoproj.io/skip-reconcile"),
                   "deployment_uid": deployment["metadata"]["uid"], "helper": None}
        self._save_recovery(journal)
        marker = {"active": True, "job_id": job_id, "started_at": now()}
        program = "import json,os,sys;from pathlib import Path;p=Path(os.environ.get('OTQ_BACKUP_MAINTENANCE_FILE','/var/quest-backup/maintenance.json'));p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(json.load(sys.stdin)));p.chmod(0o600)"
        self.app_python(program, contract.canonical(marker))
        drain = ACTOR.replace(".connect() as c:\n", ".begin() as c:\n  c.execute(text('SELECT id FROM quest_events WHERE id=1 FOR UPDATE'))\n")
        if not json.loads(self.app_python(drain, contract.canonical(actor))).get("valid"):
            raise WorkerError("Administrator authority changed before maintenance; operator inspection is required", 403)
        self._argo("true")
        self._scale(0)
        for _ in range(120):
            # Include terminating old containers; all writers must actually exit.
            remaining = [p for p in self.get("pods")["items"]
                         if p["metadata"].get("labels", {}).get("app.kubernetes.io/name") == self.options.get("application_label", "ctfd")]
            if not remaining:
                break
            time.sleep(1)
        else:
            raise WorkerError("Application writers did not stop; recovery remains in maintenance")
        template = deployment["spec"]["template"]["spec"]
        image = next(c["image"] for c in template["containers"] if c["name"] == self.options.get("application_container", "ctfd"))
        journal["helper"] = self._helper(image, pods[0]["spec"]["nodeName"], template, job_id)
        journal["stage"] = "stopped"
        self._save_recovery(journal)

    def restore(self, components, manifest):
        self.target()
        journal = json.loads(self.journal.read_text())
        if journal["stage"] != "stopped" or not self.helper or self._application_pods():
            raise WorkerError("Recovery writers are not stopped; operator inspection is required")
        with tarfile.open(components["uploads.tar.gz"], "r:gz") as uploads:
            required = sum(entry.size for entry in uploads if entry.isfile())
        capacity = json.loads(self.app_python("import json,shutil;print(json.dumps({'free':shutil.disk_usage('/var/quest-backup').free}))"))
        if capacity["free"] < required + 1024 * 1024:
            raise WorkerError("Insufficient application storage to stage restored uploads. No database replacement was attempted; operator recovery or additional space is required.")
        database = self.options.get("database_name", "ctfd")
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise WorkerError("The configured database identity is invalid")
        command = ["exec", "-i", "deploy/" + self.database, "--", "sh", "-ec",
                   'test "$MARIADB_DATABASE" = "$1"; exec mariadb --binary-mode --user=root --password="$MARIADB_ROOT_PASSWORD"', "sh", database]
        settings = manifest["database_parameters"]
        if settings["name"] != database or not all(re.fullmatch(r"[A-Za-z0-9_]+", settings[key]) for key in ("charset", "collation")):
            raise WorkerError("The backup database identity or character settings need operator recovery")
        reset = ("DROP DATABASE `" + database + "`; CREATE DATABASE `" + database + "` CHARACTER SET " + settings["charset"] + " COLLATE " + settings["collation"] + ";\n").encode()
        self.oc(command, reset)
        journal["stage"] = "replacing_database"
        self._save_recovery(journal)
        with Path(components["database.sql"]).open("rb") as source:
            result = subprocess.run(self.ocbase + ["exec", "-i", "deploy/" + self.database, "--", "sh", "-ec",
                                    'exec mariadb --binary-mode --user=root --password="$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'],
                                    stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise WorkerError("The database replacement failed. Keep maintenance enabled and use the safety backup for operator recovery.")
        private = Path(tempfile.mkdtemp(prefix="verify-sql-", dir=self.root / "temporary"))
        try:
            roundtrip = private / "database.sql"
            with roundtrip.open("xb") as output:
                self.oc(["exec", "deploy/" + self.database, "--", "sh", "-ec", DUMP], output=output)
            if contract.file_digest(roundtrip) != manifest["components"]["database.sql"]:
                raise WorkerError("Restored database bytes differ from the verified backup; keep maintenance enabled")
        finally:
            shutil.rmtree(private)
        journal["stage"] = "replacing_uploads"
        self._save_recovery(journal)
        program = r'''import os,shutil,sys,tarfile,tempfile
from pathlib import Path
root=Path('/var/uploads');control=Path('/var/quest-backup')
assert root.is_dir() and not root.is_symlink() and control.is_dir()
stage=Path(tempfile.mkdtemp(prefix='restore-',dir=control))
try:
 with tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz') as archive:
  seen=set()
  for item in archive:
   parts=Path(item.name).parts
   assert parts and not Path(item.name).is_absolute() and '..' not in parts
   target=stage.joinpath(*parts);key=str(target)
   assert key not in seen;seen.add(key)
   assert item.isdir() or item.isfile()
   if item.isdir():target.mkdir(parents=True,exist_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True)
    with archive.extractfile(item) as source,target.open('xb') as output:shutil.copyfileobj(source,output)
    target.chmod(0o644)
 for child in root.iterdir():
  if child.is_dir() and not child.is_symlink():shutil.rmtree(child)
  else:child.unlink()
 for child in stage.iterdir():shutil.move(str(child),str(root/child.name))
finally:shutil.rmtree(stage)
'''
        with Path(components["uploads.tar.gz"]).open("rb") as source:
            result = subprocess.run(self.ocbase + ["exec", "-i", "pod/" + self.helper, "-c", "helper", "--", "python", "-c", program],
                                    stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise WorkerError("Upload replacement failed. Keep maintenance enabled and use operator recovery.")
        actual = self.probe()
        if actual["counts"] != manifest["counts"] or actual["schema_fingerprint"] != manifest["schema_fingerprint"]:
            raise WorkerError("Restored data verification failed; the application remains closed")
        expected_files = {}
        with tarfile.open(components["uploads.tar.gz"], "r:gz") as uploads:
            for entry in uploads:
                if entry.isfile():
                    checksum = hashlib.sha256()
                    with uploads.extractfile(entry) as content:
                        for block in iter(lambda: content.read(1024 * 1024), b""):
                            checksum.update(block)
                    expected_files[str(Path(entry.name))] = checksum.hexdigest()
        if actual["files"] != expected_files:
            raise WorkerError("Restored upload bytes differ from the signed backup; the application remains closed")
        journal["stage"] = "data_verified"
        self._save_recovery(journal)

    def _stop_helper(self):
        deployment = self.options.get("helper_deployment", "ctfd-backup-helper")
        self.oc(["scale", "deployment/" + deployment, "--replicas=0"])
        for _ in range(120):
            pods = [p for p in self.get("pods")["items"]
                    if p["metadata"].get("labels", {}).get("app.kubernetes.io/name") == deployment]
            if not pods:
                self.helper = None
                return
            time.sleep(1)
        raise WorkerError("The restore helper did not release storage; recovery remains in maintenance")

    def verify_and_reopen(self, manifest):
        self.target()
        journal = json.loads(self.journal.read_text())
        if journal["stage"] != "data_verified":
            raise WorkerError("Restored data has not passed verification")
        # Dedicated Redis is disposable. This also invalidates old browser sessions.
        self.app_python("import os;import redis;redis.Redis.from_url(os.environ['REDIS_URL']).flushdb()")
        self._stop_helper()
        self._scale(journal["replicas"])
        for _ in range(180):
            pods = self._application_pods()
            statuses = pods[0]["status"].get("containerStatuses", []) if len(pods) == 1 else []
            if statuses and all(c.get("ready") for c in statuses):
                try:
                    proof = self.probe()
                    if proof["counts"] != manifest["counts"] or proof["schema_fingerprint"] != manifest["schema_fingerprint"]:
                        raise WorkerError("Application startup changed restored data; operator inspection is required")
                    break
                except WorkerError as error:
                    if error.status != 503:
                        raise
                except Exception:
                    pass
            time.sleep(1)
        else:
            raise WorkerError("The restored application did not become healthy; recovery remains in maintenance")
        self._argo(journal["argo_skip"])
        journal.update(stage="verified_ready")
        self._save_recovery(journal)
        self.app_python("import os;from pathlib import Path;Path(os.environ.get('OTQ_BACKUP_MAINTENANCE_FILE','/var/quest-backup/maintenance.json')).unlink(missing_ok=True)")
        journal.update(stage="complete", completed_at=now())
        self._save_recovery(journal)

    def keep_closed(self, job_id):
        if not self.journal.exists():
            return
        journal = json.loads(self.journal.read_text())
        if journal["job_id"] != job_id:
            return
        self.target()
        try:
            marker = {"active": True, "job_id": job_id, "started_at": journal["started_at"]}
            code = "import json,os,sys;from pathlib import Path;p=Path(os.environ.get('OTQ_BACKUP_MAINTENANCE_FILE','/var/quest-backup/maintenance.json'));p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(json.load(sys.stdin)))"
            self.app_python(code, contract.canonical(marker))
        except Exception:
            pass  # Existing persistent fence stays; scale below is independent.
        try:
            self._argo("true")
        finally:
            self._scale(0)
        journal.update(stage="operator_required", failed_at=now())
        self._save_recovery(journal)
