"""Provider preflights and deterministic archives without Kubernetes access."""
import copy
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

MODULES = Path(__file__).resolve().parents[1] / "plugin/open_tech_quest"
if not MODULES.exists():
    MODULES = Path("/opt/CTFd/CTFd/plugins/open_tech_quest")
import sys
sys.path.insert(0, str(MODULES))
import backup_openshift as provider

ACTOR = {"id": 1, "created_at": "2026-11-12T00:00:00.123456Z", "password_sha256": "a" * 64, "secret_sha256": "b" * 64}


def application_spec():
    return {"containers": [{"name": "ctfd", "image": "ghcr.io/example/plugin@sha256:" + "c" * 64,
        "env": [{"name": name, "valueFrom": {"secretKeyRef": {"name": "ctfd-secrets", "key": name}}}
                for name in ("SECRET_KEY", "DATABASE_URL", "REDIS_URL")],
        "envFrom": [{"configMapRef": {"name": "ctfd-config"}}],
        "volumeMounts": [{"name": "data", "mountPath": "/var/uploads", "subPath": "uploads"},
                         {"name": "data", "mountPath": "/var/quest-backup", "subPath": "backup-control"}]}],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "ctfd-data"}}]}


class ProviderFixture:
    def __init__(self, root):
        self.calls, self.actor_valid, self.marker = [], True, False
        self.config = {"state_root": str(root), "instance_id": "fixture", "adapter": {
            "oc_command": ["unreachable-test-oc"], "server": "https://api.example.test:6443", "route_host": "event.example.test",
            "pvc_uids": {"ctfd-data": "test-pvc"}, "restore_enabled": True, "argo_application": "ctfd-app"}}
        self.adapter = provider.OpenShiftAdapter(self.config)
        self.app = {"metadata": {"uid": "test-app-uid", "generation": 1},
                    "spec": {"replicas": 1, "template": {"spec": application_spec()}},
                    "status": {"readyReplicas": 1, "updatedReplicas": 1, "observedGeneration": 1}}
        helper_spec = copy.deepcopy(application_spec())
        helper_spec["containers"][0]["name"] = "helper"
        self.helper = {"spec": {"replicas": 0, "template": {"spec": helper_spec}}}
        self.pods = [{"metadata": {"name": "ctfd-app-pod", "labels": {"app.kubernetes.io/name": "ctfd"}},
                      "spec": dict(copy.deepcopy(application_spec()), nodeName="test-original-node"),
                      "status": {"phase": "Running", "containerStatuses": [{"name": "ctfd", "ready": True}]}}]
        self.adapter.target = Mock(side_effect=lambda: self.calls.append("target"))
        self.adapter.get = Mock(side_effect=self.get)
        self.adapter.oc = Mock(side_effect=self.oc)
        self.adapter.app_python = Mock(side_effect=self.app_python)

    def get(self, kind, name=None, namespace=None):
        if kind == "deployment":
            return self.app if name == "ctfd" else self.helper
        if kind == "pods":
            return {"items": copy.deepcopy(self.pods)}
        if kind == "application":
            return {"metadata": {"annotations": {}}}
        raise AssertionError("Unexpected provider fixture read")

    def oc(self, args, data=None, **kwargs):
        if "patch" in args:
            annotation = json.loads(args[args.index("-p") + 1])["metadata"]["annotations"]["argocd.argoproj.io/skip-reconcile"]
            self.calls.append("argo:" + str(annotation))
        elif args[0] == "scale":
            name = args[1].split("/", 1)[1]
            replicas = int(args[2].split("=", 1)[1])
            self.calls.append("scale:" + name + ":" + str(replicas))
            label = name
            self.pods = [p for p in self.pods if p["metadata"]["labels"]["app.kubernetes.io/name"] != label]
            if replicas:
                spec = self.helper["spec"]["template"]["spec"] if name == "ctfd-backup-helper" else self.app["spec"]["template"]["spec"]
                self.pods.append({"metadata": {"name": name + "-pod", "labels": {"app.kubernetes.io/name": label}},
                    "spec": dict(copy.deepcopy(spec), nodeName="test-original-node"),
                    "status": {"phase": "Running", "containerStatuses": [{"name": "helper" if name.endswith("helper") else "ctfd", "ready": True}]}})
        else:
            raise AssertionError("Unexpected provider fixture mutation")
        return b"{}"

    def app_python(self, program, data=None):
        if "FOR UPDATE" in program:
            self.calls.append("drain-actor")
            return json.dumps({"valid": self.actor_valid}).encode()
        if "p.write_text" in program:
            self.calls.append("marker")
            self.marker = True
            return b""
        if "flushdb" in program:
            self.calls.append("clear-sessions")
            return b""
        if "unlink(missing_ok=True)" in program:
            self.calls.append("remove-marker")
            self.marker = False
            return b""
        raise AssertionError("Unexpected helper program")


class BackupOpenShift(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quest-provider-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = ProviderFixture(self.root)
        self.adapter = self.fixture.adapter

    def test_nested_upload_archive_is_deterministic_and_roundtrips_all_bytes(self):
        uploads = self.root / "uploads"
        (uploads / "nested/empty").mkdir(parents=True)
        (uploads / "nested/space name.txt").write_bytes(b"test uploaded bytes\x00\xff")
        (uploads / ("long" * 35)).mkdir()
        (uploads / ("long" * 35) / "unicode-\u00e9.txt").write_text("synthetic unicode")
        first, second = self.root / "first.tar.gz", self.root / "second.tar.gz"
        provider.pack_uploads(uploads, first)
        for index, path in enumerate(uploads.rglob("*")):
            os.utime(path, (1700000000 + index, 1700000000 + index))
            path.chmod(0o700 if path.is_dir() else 0o600)
        provider.pack_uploads(uploads, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        extracted = self.root / "restored"
        provider.extract_uploads(first, extracted)
        self.assertTrue((extracted / "nested/empty").is_dir())
        expected = {str(p.relative_to(uploads)): p.read_bytes() for p in uploads.rglob("*") if p.is_file()}
        actual = {str(p.relative_to(extracted)): p.read_bytes() for p in extracted.rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)

    def test_upload_extraction_refuses_traversal_links_duplicates_and_expanded_size(self):
        for index, (name, kind, data, limit) in enumerate((
                ("../outside", tarfile.REGTYPE, b"bad", 100), ("/outside", tarfile.REGTYPE, b"bad", 100),
                ("link", tarfile.SYMTYPE, b"", 100), ("link", tarfile.LNKTYPE, b"", 100),
                ("large", tarfile.REGTYPE, b"larger-than-limit", 2))):
            with self.subTest(name=name, kind=kind):
                path = self.root / (str(index) + ".tar.gz")
                with tarfile.open(path, "w:gz") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.size = kind, len(data)
                    entry.linkname = "../outside" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
                    archive.addfile(entry, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
                with self.assertRaises(provider.WorkerError):
                    provider.extract_uploads(path, self.root / ("output-" + str(index)), maximum=limit)
        path = self.root / "duplicate.tar"
        with tarfile.open(path, "w") as archive:
            for _ in range(2):
                entry = tarfile.TarInfo("same.txt")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
        with self.assertRaisesRegex(provider.WorkerError, "duplicate"):
            provider.extract_uploads(path, self.root / "duplicates")

    def test_helper_preflight_rejects_wrong_claim_image_credentials_and_mount(self):
        application = application_spec()
        original = copy.deepcopy(self.fixture.helper["spec"]["template"]["spec"])
        self.adapter._validate_helper(original, application)
        mutations = [lambda h: h["volumes"][0]["persistentVolumeClaim"].update(claimName="other-claim"),
            lambda h: h["containers"][0].update(image="wrong-image"),
            lambda h: h["containers"][0]["env"][1]["valueFrom"]["secretKeyRef"].update(name="other-secret"),
            lambda h: h["containers"][0]["volumeMounts"][0].update(subPath="other-upload-subpath")]
        for mutate in mutations:
            candidate = copy.deepcopy(original)
            mutate(candidate)
            with self.assertRaises(provider.WorkerError):
                self.adapter._validate_helper(candidate, application)
            self.fixture.helper["spec"]["template"]["spec"] = candidate
            with self.assertRaises(provider.WorkerError):
                self.adapter.fence_and_stop("synthetic-job", ACTOR)
            self.assertFalse(self.fixture.marker)
            self.assertNotIn("scale:ctfd:0", self.fixture.calls)

    def test_fence_drains_fresh_actor_before_stopping_app_and_starting_helper(self):
        self.adapter.fence_and_stop("synthetic-job", ACTOR)
        calls = self.fixture.calls
        for first, second in (("marker", "drain-actor"), ("drain-actor", "argo:true"),
                              ("argo:true", "scale:ctfd:0"), ("scale:ctfd:0", "scale:ctfd-backup-helper:1")):
            self.assertLess(calls.index(first), calls.index(second))
        self.assertTrue(self.fixture.marker)
        self.assertEqual(json.loads(self.adapter.journal.read_text())["stage"], "stopped")
        self.assertEqual(self.adapter.helper, "ctfd-backup-helper-pod")

    def test_revocation_after_fence_refuses_and_controller_keep_closed_stops_writers(self):
        self.fixture.actor_valid = False
        with self.assertRaises(provider.WorkerError):
            self.adapter.fence_and_stop("synthetic-job", ACTOR)
        self.assertTrue(self.fixture.marker)
        self.assertNotIn("scale:ctfd-backup-helper:1", self.fixture.calls)
        self.adapter.keep_closed("synthetic-job")
        self.assertIn("scale:ctfd:0", self.fixture.calls)
        self.assertTrue(self.fixture.marker)
        self.assertEqual(json.loads(self.adapter.journal.read_text())["stage"], "operator_required")

    def test_reopen_waits_for_nonempty_container_readiness_and_removes_marker_last(self):
        self.fixture.marker = True
        self.adapter._save_recovery({"job_id": "synthetic-job", "stage": "data_verified", "replicas": 1, "argo_skip": None})
        self.adapter._stop_helper = Mock(side_effect=lambda: self.fixture.calls.append("stop-helper"))
        self.adapter.probe = Mock(return_value={"counts": {"users": 4}, "schema_fingerprint": "test-schema"})
        original = self.adapter._application_pods
        reads = []
        def pods():
            value = original()
            reads.append(True)
            if len(reads) == 1:
                value[0]["status"]["containerStatuses"] = []
            return value
        with patch.object(self.adapter, "_application_pods", side_effect=pods), patch.object(provider.time, "sleep") as sleep:
            self.adapter.verify_and_reopen({"counts": {"users": 4}, "schema_fingerprint": "test-schema"})
        self.assertEqual(len(reads), 2)
        sleep.assert_called_once()
        self.assertEqual(self.adapter.probe.call_count, 1)
        self.assertEqual(self.fixture.calls[-1], "remove-marker")
        self.assertFalse(self.fixture.marker)

    def test_helper_itself_waits_for_nonempty_container_readiness(self):
        reads = []
        original = self.fixture.get
        def get(kind, name=None, namespace=None):
            value = original(kind, name, namespace)
            if kind == "pods":
                reads.append(True)
                if len(reads) == 1:
                    for pod in value["items"]:
                        if pod["metadata"]["labels"]["app.kubernetes.io/name"] == "ctfd-backup-helper":
                            pod["status"]["containerStatuses"] = []
            return value
        with patch.object(self.adapter, "get", side_effect=get), patch.object(provider.time, "sleep") as sleep:
            self.adapter._helper(application_spec()["containers"][0]["image"], "test-original-node", application_spec(), "synthetic-job")
        self.assertEqual(len(reads), 2)
        sleep.assert_called_once()

    def test_helper_environment_from_must_not_point_to_different_database_or_session_secret(self):
        app = application_spec()
        helper = copy.deepcopy(self.fixture.helper["spec"]["template"]["spec"])
        for spec in (app, helper):
            spec["containers"][0].pop("env")
        app["containers"][0]["envFrom"] = [{"secretRef": {"name": "actual-app-credentials"}}]
        helper["containers"][0]["envFrom"] = [{"secretRef": {"name": "different-db-and-redis"}}]
        with self.assertRaises(provider.WorkerError):
            self.adapter._validate_helper(helper, app)

    def test_failed_startup_verification_keeps_fence_and_stops_restarted_writers(self):
        self.adapter.fence_and_stop("synthetic-job", ACTOR)
        journal = json.loads(self.adapter.journal.read_text())
        journal["stage"] = "data_verified"
        self.adapter._save_recovery(journal)
        self.adapter.probe = Mock(return_value={"counts": {"users": 99}, "schema_fingerprint": "test-schema"})
        with self.assertRaises(provider.WorkerError):
            self.adapter.verify_and_reopen({"counts": {"users": 4}, "schema_fingerprint": "test-schema"})
        self.assertTrue(self.fixture.marker)
        self.assertNotIn("remove-marker", self.fixture.calls)
        self.adapter.keep_closed("synthetic-job")
        self.assertEqual(self.fixture.calls[-1], "scale:ctfd:0")
        self.assertFalse(any(p["metadata"]["labels"]["app.kubernetes.io/name"] == "ctfd" for p in self.fixture.pods))
