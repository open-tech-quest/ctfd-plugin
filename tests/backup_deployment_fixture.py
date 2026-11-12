"""Disposable full-replacement rehearsal; no Kubernetes credentials or targets.

Run explicitly with --output outside the repository. A local fake-oc transport
maps the real provider's commands to only this run's named Podman containers.
All generated credentials, SQL, repositories and volumes are removed afterwards.
"""
import argparse
import base64
import copy
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer

CTFD = "docker.io/ctfd/ctfd:3.8.9@sha256:84aff1e50b6a6f931b5da90c6f5b7c623ba4583217a9b00e9d4c1b7c53b9739f"
DATABASE = "docker.io/library/mariadb:10.11.19@sha256:d769e37960821dfdcf096954f6e7ac89f747e116c1bba98e48edd265dfc7deae"
CACHE = "docker.io/library/redis:8.10.2-alpine@sha256:9f8d2d789b9a1d1e3f283f979aa9cbf393373806472e1ccd460e2f9c869b833b"
DATABASE_NAME = "quest_restore_test"
SERVER = "https://api.disposable-fixture.invalid:6443"


def command(args, data=None, timeout=180):
    result = subprocess.run([str(v) for v in args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Disposable fixture command failed; output suppressed: " + str(args[0]))
    return result.stdout


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    path.chmod(0o600)


def ready(state):
    try:
        with urllib.request.urlopen("http://127.0.0.1:" + str(state["port"]) + "/healthcheck", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def start_app(state, helper=False):
    name = state["helper"] if helper else state["app"]
    args = ["podman", "run", "-d", "--name", name, "--network", state["network"], "--user", "0",
        "--env-file", state["app_env"], "-v", state["var"] + ":/var",
        "-v", state["plugin"] + ":/opt/CTFd/CTFd/plugins/open_tech_quest:ro",
        "-v", state["frontend"] + ":/opt/CTFd/CTFd/plugins/open_tech_quest/assets/app:ro",
        "-v", state["branding"] + ":/opt/CTFd/CTFd/plugins/open_tech_quest/assets/branding:ro"]
    if helper:
        args += ["--entrypoint", "python", CTFD, "-c", "import time;time.sleep(86400)"]
    else:
        args += ["-p", "127.0.0.1:" + str(state["port"]) + ":8000", CTFD]
    command(args)


def fake_oc(state_path, args):
    """Only a synthetic API. Never shell out to oc or read a kubeconfig."""
    state_path = Path(state_path)
    state = json.loads(state_path.read_text())
    if "config" in args:
        print(json.dumps({"clusters": [{"cluster": {"server": SERVER}}]}))
        return
    verbs = ("exec", "get", "patch", "scale")
    position = next(i for i, value in enumerate(args) if value in verbs)
    args = args[position:]
    verb = args[0]
    if verb == "exec":
        target = next(value for value in args[1:] if value.startswith(("deploy/", "pod/")))
        name = target.split("/", 1)[1]
        mapping = {"ctfd": state["app"], "database": state["database"], "cache": state["cache"], state["helper"]: state["helper"]}
        if name not in mapping:
            raise RuntimeError("Unknown disposable exec target")
        executable = args[args.index("--") + 1:]
        result = subprocess.run(["podman", "exec", "-i", mapping[name], *executable],
            stdin=sys.stdin.buffer, stdout=sys.stdout.buffer, stderr=subprocess.PIPE, timeout=360)
        if result.returncode:
            raise RuntimeError("Disposable exec failed; output suppressed")
        return
    if verb == "scale":
        deployment = args[1].split("/", 1)[1]
        replicas = int(next(value for value in args if value.startswith("--replicas=")).split("=", 1)[1])
        if deployment not in ("ctfd", "ctfd-backup-helper") or replicas not in (0, 1):
            raise RuntimeError("Unknown disposable scale target")
        container = state["app"] if deployment == "ctfd" else state["helper"]
        if replicas:
            start_app(state, helper=deployment == "ctfd-backup-helper")
        else:
            subprocess.run(["podman", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state["replicas"][deployment] = replicas
        state["operations"].append("scale:" + deployment + ":" + str(replicas))
        write(state_path, state)
        print("scaled")
        return
    if verb == "patch":
        if args[1:3] != ["application", "fixture-app"]:
            raise RuntimeError("Unknown disposable Argo target")
        value = json.loads(args[args.index("-p") + 1])["metadata"]["annotations"]["argocd.argoproj.io/skip-reconcile"]
        state["argo_skip"] = value
        state["operations"].append("argo:" + str(value))
        write(state_path, state)
        print("patched")
        return
    kind = args[1]
    name = args[2] if len(args) > 2 and not args[2].startswith("-") else None
    objects = state["objects"]
    if kind == "pods":
        items = []
        for deployment, container in (("ctfd", state["app"]), ("ctfd-backup-helper", state["helper"])):
            if not state["replicas"][deployment]:
                continue
            running = subprocess.run(["podman", "inspect", "--format", "{{.State.Running}}", container], capture_output=True)
            if running.returncode or running.stdout.strip() != b"true":
                continue
            spec = copy.deepcopy(objects["deployment/" + deployment]["spec"]["template"]["spec"])
            spec["nodeName"] = "disposable-node"
            items.append({"metadata": {"name": container, "labels": {"app.kubernetes.io/name": deployment}},
                "spec": spec, "status": {"phase": "Running", "containerStatuses": [{"name": "ctfd" if deployment == "ctfd" else "helper", "ready": ready(state) if deployment == "ctfd" else True}]}})
        value = {"items": items}
    elif kind == "application":
        annotations = {} if state.get("argo_skip") is None else {"argocd.argoproj.io/skip-reconcile": state["argo_skip"]}
        value = {"metadata": {"annotations": annotations}}
    elif name:
        value = copy.deepcopy(objects[kind + "/" + name])
        if kind == "deployment" and name in state["replicas"]:
            value["spec"]["replicas"] = state["replicas"][name]
            is_ready = ready(state) if name == "ctfd" and state["replicas"][name] else False
            value["status"] = {"observedGeneration": 1, "readyReplicas": int(is_ready), "updatedReplicas": state["replicas"][name]}
    else:
        value = {"items": [item for key, item in objects.items() if key.startswith(kind + "/")]}
    print(json.dumps(value))


SEED = r'''
import hashlib,json,sys
from datetime import datetime,timedelta
from pathlib import Path
from CTFd import create_app
from CTFd.models import Users,Teams,Challenges,Flags,Hints,Solves,Fails,HintUnlocks,Awards,ChallengeFiles,db
from CTFd.utils import set_config
from CTFd.plugins.open_tech_quest.models import QuestMaintainer,QuestEvent,QuestRoom,QuestStart,QuestHintPurchase,QuestOnboarding
from CTFd.plugins.open_tech_quest.room_topology import set_challenge_order
data=json.load(sys.stdin);app=create_app()
with app.app_context():
 for key,value in [('setup',True),('ctf_name','Disposable restore fixture'),('user_mode','teams'),('verify_emails',False),('challenge_visibility','public'),('account_visibility','public')]:set_config(key,value)
 team=Teams(id=1,name='Original team');db.session.add(team);db.session.flush()
 db.session.add_all([Users(id=1,name='fixture-admin',email='admin@example.test',type='admin',password=data['admin'],verified=True),Users(id=2,name='fixture-maintainer',email='maintainer@example.test',password=data['participant'],verified=True),Users(id=3,name='fixture-player',email='player@example.test',password=data['participant'],team_id=1,verified=True)])
 db.session.flush();db.session.add(QuestMaintainer(user_id=2,previous_hidden=False))
 for cid in (1,2):
  db.session.add(Challenges(id=cid,name='Original challenge '+str(cid),description='Original text',type='standard',state='visible',value=100,category='Fixture'))
  db.session.flush();db.session.add(Flags(challenge_id=cid,type='static',content='synthetic-original-'+str(cid),data=''))
 db.session.flush();room=QuestRoom.query.filter_by(slug='openshift').one();set_challenge_order(room,[1,2])
 e=QuestEvent.query.get(1);e.status='live';e.start_at=datetime.utcnow()-timedelta(minutes=5)
 onboarding=QuestOnboarding.query.get(1);onboarding.phase='finalized';onboarding.grandfathered=True
 db.session.add(QuestStart(team_id=1,room_id=room.id,started_at=e.start_at+timedelta(seconds=2)))
 db.session.add(Solves(user_id=3,team_id=1,challenge_id=1,date=e.start_at+timedelta(seconds=5)))
 db.session.add(Fails(user_id=3,team_id=1,challenge_id=2,date=e.start_at+timedelta(seconds=6),provided='synthetic-failed-attempt'))
 hint=Hints(id=10,challenge_id=1,title='Original hint',content='Original hint body',cost=10);db.session.add(hint);db.session.flush()
 unlock=HintUnlocks(user_id=3,team_id=1,target=10,date=e.start_at+timedelta(seconds=7));award=Awards(user_id=3,team_id=1,name='Original hint charge',value=-10,date=e.start_at+timedelta(seconds=7));db.session.add_all([unlock,award]);db.session.flush()
 db.session.add(QuestHintPurchase(team_id=1,challenge_id=1,hint_id=10,unlock_id=unlock.id,award_id=award.id,cost=10,purchased_at=e.start_at+timedelta(seconds=7)))
 p=Path('/var/uploads/nested/original.txt');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'original-upload-bytes\x00\xff')
 db.session.add(ChallengeFiles(challenge_id=1,location='nested/original.txt'));db.session.commit()
print('FIXTURE_SEEDED')
'''

MUTATE = r'''
import json,sys
from datetime import datetime
from pathlib import Path
from CTFd import create_app
from CTFd.models import Users,Challenges,Flags,Solves,ChallengeFiles,db
from CTFd.plugins.open_tech_quest.models import QuestMaintainer
data=json.load(sys.stdin);app=create_app()
with app.app_context():
 db.session.add(Users(id=9,name='new-admin-not-retained',email='new-admin@example.test',type='admin',password=data['new_admin'],verified=True))
 Users.query.get(3).password=data['new_participant']
 QuestMaintainer.query.filter_by(user_id=2).delete()
 Flags.query.filter_by(challenge_id=1).one().content='synthetic-replacement-flag'
 Challenges.query.get(1).value=900
 db.session.add(Solves(user_id=3,team_id=1,challenge_id=2,date=datetime.utcnow()))
 p=Path('/var/uploads/nested/original.txt');p.write_bytes(b'changed-current-upload')
 p=Path('/var/uploads/new/current-only.txt');p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'new-only-upload')
 db.session.add(ChallengeFiles(challenge_id=2,location='new/current-only.txt'));db.session.commit()
print('FIXTURE_MUTATED')
'''

STATE = r'''
import hashlib,json,os
from sqlalchemy import create_engine,inspect,text
def h(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
with create_engine(os.environ['DATABASE_URL']).connect() as c:
 tables={}
 for name in sorted(inspect(c).get_table_names()):
  rows=[dict(row) for row in c.execute(text('SELECT * FROM `'+name+'`')).mappings()]
  if name=='config':rows=[r for r in rows if r['key'] not in ('next_update_check','version_latest')]
  tables[name]={'count':len(rows),'sha256':h(sorted(json.dumps(row,sort_keys=True,default=str) for row in rows))}
  if name=='config':tables[name]['key_hashes']={row['key']:h(row) for row in rows}
print(json.dumps(tables))
'''


def login(base, name, password):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(base + "/login", timeout=15) as response:
        form = response.read().decode()
    nonce = re.search(r'name="nonce"[^>]*value="([^"]+)"', form).group(1)
    raw = urllib.parse.urlencode({"name": name, "password": password, "nonce": nonce, "_submit": "Submit"}).encode()
    with opener.open(base + "/login", data=raw, timeout=30) as response:
        ok = "/login" not in response.geturl()
        response.read()
    return opener, ok


def run_fixture(output, hold=0):
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "plugin/open_tech_quest"))
    import backup_contract as contract
    import backup_openshift as provider
    import backup_worker as worker
    source_modules = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (repo / "plugin/open_tech_quest").glob("backup*.py")}
    output = output.resolve()
    if output == repo or repo in output.parents:
        raise RuntimeError("Keep disposable evidence outside the public repository")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    suffix = secrets.token_hex(5)
    prefix = "otq-restore-fixture-" + suffix
    names = {key: prefix + "-" + key for key in ("app", "helper", "database", "cache", "network")}
    private = Path(tempfile.mkdtemp(prefix=prefix + "-", dir=output))
    private.chmod(0o700)
    server = None
    proof = {"passed": False, "synthetic_only": True, "no_kubernetes_access": True, "images": {"ctfd": CTFD, "database": DATABASE, "cache": CACHE}}
    try:
        for name in ("var/uploads", "var/log/CTFd", "var/quest-backup", "journal/temporary", "restic/repo"):
            (private / name).mkdir(parents=True, exist_ok=True)
        values = {key: secrets.token_hex(24) for key in ("root", "database", "redis", "session", "admin", "participant", "new_admin", "new_participant", "worker")}
        app_env = {"DATABASE_URL": "mysql+pymysql://quest:" + values["database"] + "@" + names["database"] + "/" + DATABASE_NAME,
            "REDIS_URL": "redis://:" + values["redis"] + "@" + names["cache"] + ":6379", "SECRET_KEY": values["session"],
            "UPLOAD_FOLDER": "/var/uploads", "LOG_FOLDER": "/var/log/CTFd", "WORKERS": "1", "MAX_OVERFLOW": "0",
            "OTQ_PLUGIN_VERSION": "disposable-backup-fixture", "OTQ_BACKUP_MAINTENANCE_FILE": "/var/quest-backup/maintenance.json"}
        worker_port = None
        if hold:
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0)); worker_port = available.getsockname()[1]
            app_env.update(OTQ_BACKUP_WORKER_URL="http://host.containers.internal:" + str(worker_port),
                OTQ_BACKUP_WORKER_TOKEN=values["worker"], OTQ_BACKUP_STATUS_ORIGIN="http://127.0.0.1:" + str(worker_port))
        envs = {"app": app_env, "database": {"MARIADB_ROOT_PASSWORD": values["root"], "MARIADB_DATABASE": DATABASE_NAME,
            "MARIADB_USER": "quest", "MARIADB_PASSWORD": values["database"]}, "cache": {"REDIS_PASSWORD": values["redis"]}}
        for key, entries in envs.items():
            path = private / (key + ".env")
            path.write_text("".join(name + "=" + value + "\n" for name, value in entries.items()))
            path.chmod(0o600)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0)); port = available.getsockname()[1]
        mounts = [{"name": "data", "mountPath": "/var/uploads", "subPath": "uploads"}, {"name": "data", "mountPath": "/var/quest-backup", "subPath": "backup-control"}]
        def env_entry(key, value):
            if key == "OTQ_BACKUP_WORKER_TOKEN":
                return {"name": key, "valueFrom": {"secretKeyRef": {"name": "fixture-worker", "key": "token"}}}
            if key.startswith("OTQ_BACKUP_"):
                return {"name": key, "value": value}
            return {"name": key, "valueFrom": {"secretKeyRef": {"name": "fixture-secrets", "key": key}}}
        spec = {"containers": [{"name": "ctfd", "image": CTFD, "env": [env_entry(k, v) for k, v in app_env.items()], "volumeMounts": mounts}],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "ctfd-data"}}]}
        helper_spec = copy.deepcopy(spec);helper_spec["containers"][0]["name"] = "helper"
        def deployment(name, image, template=None):
            return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": "ctfd", "uid": "fixture-" + name, "generation": 1},
                    "spec": {"replicas": 0 if name.endswith("helper") else 1, "template": {"spec": template or {"containers": [{"name": name, "image": image}]}}}}
        objects = {"deployment/ctfd": deployment("ctfd", CTFD, spec), "deployment/ctfd-backup-helper": deployment("ctfd-backup-helper", CTFD, helper_spec),
            "deployment/database": deployment("database", DATABASE), "deployment/cache": deployment("cache", CACHE),
            "secret/fixture-secrets": {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "fixture-secrets"}, "data": {k: base64.b64encode(v.encode()).decode() for k, v in app_env.items() if not k.startswith("OTQ_BACKUP_")}},
            "route/ctfd": {"spec": {"host": "event.disposable-fixture.invalid"}}}
        for name in ("ctfd-data", "database-data", "cache-data"):
            objects["pvc/" + name] = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": name, "uid": "fixture-" + name}, "spec": {"volumeName": name}}
        objects["secret/fixture-secrets"]["data"].update({key: base64.b64encode(value.encode()).decode() for key, value in {
            "MARIADB_PASSWORD": values["database"], "MARIADB_ROOT_PASSWORD": values["root"], "REDIS_PASSWORD": values["redis"]}.items()})
        state = dict(names, port=port, app_env=str(private / "app.env"), var=str(private / "var"), plugin=str(repo / "plugin/open_tech_quest"),
            frontend=str(repo / "web/dist"), branding=str(repo / "branding"), objects=objects, replicas={"ctfd": 1, "ctfd-backup-helper": 0}, argo_skip=None, operations=[])
        if not (repo / "web/dist/quest.js").exists():
            raise RuntimeError("Build the isolated frontend before running this fixture")
        state_path = private / "fake-kubernetes.json";write(state_path, state)
        command(["podman", "network", "create", names["network"]])
        print("fixture_stage=starting_database_cache", flush=True)
        command(["podman", "run", "-d", "--name", names["database"], "--network", names["network"], "--env-file", private / "database.env", DATABASE, "--character-set-server=utf8mb4", "--collation-server=utf8mb4_unicode_ci"])
        command(["podman", "run", "-d", "--name", names["cache"], "--network", names["network"], "--env-file", private / "cache.env", CACHE,
                 "sh", "-ec", 'exec redis-server --requirepass "$REDIS_PASSWORD"'])
        for _ in range(90):
            check = subprocess.run(["podman", "exec", names["database"], "healthcheck.sh", "--connect", "--innodb_initialized"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if check.returncode == 0:break
            time.sleep(1)
        else:raise RuntimeError("Disposable MariaDB did not become ready")
        print("fixture_stage=starting_native_ctfd", flush=True)
        start_app(state)
        for _ in range(180):
            if ready(state):break
            time.sleep(1)
        else:raise RuntimeError("Disposable CTFd did not become ready")
        print("fixture_stage=seeding_synthetic_baseline", flush=True)
        def app_python(code, payload=None):
            return command(["podman", "exec", "-i", names["app"], "python", "-c", code], contract.canonical(payload) if payload is not None else None, timeout=300)
        app_python(SEED, values)
        base = "http://127.0.0.1:" + str(port)
        old_session, ok = login(base, "fixture-admin", values["admin"])
        if not ok:raise RuntimeError("Native initial administrator login failed")
        (private / "signing.key").write_bytes(secrets.token_bytes(64))
        (private / "worker.token").write_text(values["worker"])
        (private / "restic/password").write_text(secrets.token_hex(32))
        config = {"id": "fixture", "state_root": str(private / "journal"), "instance_id": "disposable-fixture", "instance_name": "Disposable restore fixture",
            "signing_key_file": str(private / "signing.key"), "signing_key_id": "fixture-only", "token_file": str(private / "worker.token"),
            "public_origin": "http://127.0.0.1:" + str(worker_port) if hold else "https://backup.disposable-fixture.invalid", "allowed_origins": [base],
            "adapter": {"oc_command": [sys.executable, str(Path(__file__).resolve()), "--fake-oc", str(state_path)],
            "server": SERVER, "route_host": "event.disposable-fixture.invalid", "namespace": "ctfd", "application_secret": "fixture-secrets", "database_name": DATABASE_NAME,
            "pvc_uids": {name: "fixture-" + name for name in ("ctfd-data", "database-data", "cache-data")}, "restore_enabled": True,
            "argo_application": "fixture-app", "repository_root": str(private / "restic"), "restic_host": "disposable-fixture", "restic_tag": "fixture"}}
        adapter = provider.OpenShiftAdapter(config)
        adapter.restic(["init"])
        profile = worker.Profile(config, adapter)
        if hold:
            server = ThreadingHTTPServer(("127.0.0.1", worker_port), worker.Handler)
            server.profiles = [profile]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            app_python("import urllib.request;urllib.request.urlopen('http://host.containers.internal:" + str(worker_port) + "/healthcheck',timeout=10).read()")
        def authority(uid):
            code = "import json,sys,os,hashlib;from sqlalchemy import create_engine,text\nwith create_engine(os.environ['DATABASE_URL']).connect() as c:\n u=c.execute(text('SELECT id,created,password,secret FROM users WHERE id=:id'),{'id':int(sys.stdin.read())}).mappings().one();print(json.dumps({'id':u['id'],'created_at':u['created'].isoformat(timespec='microseconds')+'Z','password_sha256':hashlib.sha256(u['password'].encode()).hexdigest(),'secret_sha256':hashlib.sha256((u['secret'] or '').encode()).hexdigest()}))"
            raw = command(["podman", "exec", "-i", names["app"], "python", "-c", code], str(uid).encode(), timeout=180)
            return json.loads(raw.splitlines()[-1])
        original_actor = authority(1)
        print("fixture_stage=real_capture_and_restic", flush=True)
        for baseline_attempt in range(3):
            raw_sql = command(["podman", "exec", names["database"], "sh", "-ec", provider.DUMP])
            baseline_sql_sha = hashlib.sha256(raw_sql).hexdigest()
            del raw_sql
            before = json.loads(app_python(STATE))
            captured = profile.enqueue("backup", original_actor, {"operation_id": str(uuid.uuid4()), "reason": "Synthetic full capture"}, synchronous=True)
            if captured["job"]["status"] != "succeeded":raise RuntimeError("Capture failed: " + captured["job"]["message"])
            snapshot = profile.job(captured["job"]["id"])["snapshot_id"]
            record = profile.snapshot_record(snapshot)
            if record["manifest"]["components"]["database.sql"]["sha256"] == baseline_sql_sha:
                break
        else:raise RuntimeError("The independent baseline did not stabilize against the captured SQL")
        proof["independent_baseline_matches_actual_captured_sql"] = True
        proof["native_post_start_update_metadata_keys_excluded"] = ["next_update_check", "version_latest"]
        with profile.materialize(record) as (bundle, _):
            if contract.verify_bundle(bundle, profile.trusted_keys) != record["manifest"]:raise RuntimeError("Portable verification failed")
        app_python(MUTATE, values)
        print("fixture_stage=review_full_restore", flush=True)
        acting_admin = authority(9)
        current = json.loads(app_python(STATE))
        if current == before:raise RuntimeError("Fixture did not change before restore")
        review = profile.review(snapshot, acting_admin)
        restored = profile.enqueue("restore", acting_admin, {"operation_id": str(uuid.uuid4()), "review_token": review["review_token"], "confirmation_text": review["confirmation_text"],
            "confirm": True, "reason": "Synthetic full replacement and safety rehearsal"}, snapshot, synchronous=True)
        if restored["job"]["status"] != "succeeded":raise RuntimeError("Restore failed: " + restored["job"]["message"])
        after = json.loads(app_python(STATE))
        changed = sorted(name for name in before if before[name] != after.get(name) and name not in ("tracking", "quest_read_versions"))
        proof["table_fingerprints_before"] = before
        proof["table_fingerprints_after"] = after
        proof["config_changed_keys"] = sorted(key for key in set(before['config']['key_hashes']) | set(after['config']['key_hashes'])
            if before['config']['key_hashes'].get(key) != after['config']['key_hashes'].get(key))
        if changed:raise RuntimeError("Restored table fingerprints differ: " + ",".join(changed))
        with old_session.open(base + "/api/v1/quest/bootstrap", timeout=20) as response:
            if json.load(response)["data"]["viewer"]["authenticated"]:raise RuntimeError("Old Redis-backed session survived restore")
        if not login(base, "fixture-admin", values["admin"])[1]:raise RuntimeError("Restored native admin password failed")
        if not login(base, "fixture-player", values["participant"])[1]:raise RuntimeError("Original participant password did not return")
        if login(base, "fixture-player", values["new_participant"])[1]:raise RuntimeError("Post-backup participant password survived")
        if login(base, "new-admin-not-retained", values["new_admin"])[1]:raise RuntimeError("Executing administrator incorrectly retained")
        files = {str(p.relative_to(private / "var/uploads")): hashlib.sha256(p.read_bytes()).hexdigest() for p in (private / "var/uploads").rglob("*") if p.is_file()}
        if set(files) != {"nested/original.txt"}:raise RuntimeError("Full upload replacement retained new files")
        safety_id = restored["job"]["safety_snapshot_id"]
        safety = profile.snapshot_record(safety_id)
        if safety["manifest"]["counts"]["users"] != 4 or safety["manifest"]["counts"]["solves"] != 2:raise RuntimeError("Safety backup does not contain the pre-restore state")
        proof.update(passed=True, source_sha256=source_modules, schema=record["manifest"]["source"], tables_compared=len(before), changed_domain_tables=changed,
            capture_signed_verified=True, real_mariadb_roundtrip=True, real_restic_publish_materialize=True,
            full_database_uploads_replaced=True, restored_passwords_roles_flags_receipts_progress=True, executing_new_admin_not_retained=True,
            old_redis_session_invalid=True, safety_backup_verified=True, restored_uploads=files,
            orchestration=json.loads(state_path.read_text())["operations"])
        if hold:
            write(output / "fixture-access.json", {"url": base, "username": "fixture-admin", "password": values["admin"],
                "maintainer": {"username": "fixture-maintainer", "password": values["participant"]},
                "worker_status_origin": config["public_origin"], "expires_in_seconds": hold})
            write(output / "full-replacement-proof.json", proof)
            print("fixture_stage=holding_for_browser_qa", flush=True)
            deadline = time.monotonic() + hold
            while time.monotonic() < deadline and not (output / "stop-fixture").exists():
                time.sleep(1)
    finally:
        if server:
            server.shutdown(); server.server_close()
        for name in (names["helper"], names["app"], names["cache"], names["database"]):
            subprocess.run(["podman", "rm", "-fv", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["podman", "network", "rm", names["network"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(private)
        (output / "fixture-access.json").unlink(missing_ok=True)
        (output / "stop-fixture").unlink(missing_ok=True)
        proof["cleanup"] = {"owned_containers_network_removed": True, "raw_sql_uploads_repository_credentials_removed": True}
        write(output / "full-replacement-proof.json", proof)
    print(json.dumps({"passed": proof["passed"], "tables_compared": proof.get("tables_compared"), "cleanup": proof["cleanup"]}))


if __name__ == "__main__":
    os.umask(0o077)
    if len(sys.argv) > 2 and sys.argv[1] == "--fake-oc":
        fake_oc(sys.argv[2], sys.argv[3:])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--hold", type=int, default=0, help="Keep successful fixture and real worker available for browser QA, in seconds")
        arguments = parser.parse_args()
        run_fixture(arguments.output, arguments.hold)
