"""Portable authenticated bundle tests; no database or worker required."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location("otq_backup_contract", Path(__file__).resolve().parents[1] / "plugin/open_tech_quest/backup_contract.py")
if not spec.origin or not Path(spec.origin).exists():
    spec = importlib.util.spec_from_file_location("otq_backup_contract", "/opt/CTFd/CTFd/plugins/open_tech_quest/backup_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class BackupContract(unittest.TestCase):
    def test_legacy_nanosecond_manifest_keeps_signed_bytes_on_older_runtimes(self):
        real_datetime = contract.datetime
        def strict_timestamp(value):
            import re
            match = re.search(r"\.(\d+)", value)
            if match and len(match.group(1)) not in (3, 6):
                raise ValueError("older datetime fractional precision")
            return real_datetime.fromisoformat(value)
        original = "2026-11-12T00:00:00.123456789Z"
        metadata = copy.deepcopy(self.manifest)
        metadata.update(created_at=original, legacy=True)
        with patch.object(contract, "datetime", wraps=real_datetime) as clock:
            clock.fromisoformat.side_effect = strict_timestamp
            bundle = self.root / "legacy-nanoseconds.otqbackup"
            signed = contract.build_bundle(bundle, metadata, self.parts, "test-key", self.key)
            verified = contract.verify_bundle(bundle, {"test-key": self.key})
            self.assertEqual(verified["created_at"], original)
            self.assertTrue(verified["legacy"])
            repacked = self.root / "legacy-repacked.otqbackup"
            contract.build_bundle(repacked, signed, self.parts, "test-key", self.key)
            self.assertEqual(bundle.read_bytes(), repacked.read_bytes())
            metadata["created_at"] = "2026-11-12T00:00:00.123456789+01:00"
            with self.assertRaisesRegex(contract.BackupError, "UTC"):
                contract.validate_manifest(dict(signed, created_at=metadata["created_at"]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quest-backup-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = b"test-only-key-never-use-in-deployment" * 2
        self.parts = {}
        for name, raw in (("database.sql", b"-- synthetic disposable SQL\n"),
                          ("uploads.tar.gz", b"synthetic uploads bytes"),
                          ("infrastructure.json", b'{"image":"synthetic","config":"test-only"}')):
            path = self.root / name
            path.write_bytes(raw)
            self.parts[name] = path
        self.manifest = {"format": contract.FORMAT, "schema_version": 1,
            "snapshot_id": "a" * 32, "created_at": "2026-11-12T00:01:02.123456Z",
            "source": {"ctfd_version": "3.8.9", "plugin_version": "0.1.41",
                "schema_revision": "quest_0011", "ctfd_schema_revision": "native-test-revision", "image_digest": "sha256:" + "b" * 64},
            "instance": {"id": "fixture-instance", "name": "Synthetic event"},
            "counts": {"users": 4, "teams": 1, "challenges": 2, "solves": 1}}
        self.bundle = self.root / "signed.otqbackup"
        self.signed = contract.build_bundle(self.bundle, self.manifest, self.parts, "test-key", self.key)

    def changed(self, change):
        with zipfile.ZipFile(self.bundle) as original:
            contents = {name: original.read(name) for name in original.namelist()}
        change(contents)
        path = self.root / "changed.otqbackup"
        with zipfile.ZipFile(path, "w") as output:
            for name, raw in contents.items():
                output.writestr(name, raw)
        return path

    def test_roundtrip_private_immutable_and_all_component_bytes_authenticated(self):
        verified = contract.verify_bundle(self.bundle, {"test-key": self.key})
        self.assertEqual(verified, self.signed)
        self.assertEqual(self.bundle.stat().st_mode & 0o777, 0o600)
        self.assertEqual(verified["created_at"], self.manifest["created_at"])
        self.assertEqual(set(verified["components"]), set(contract.COMPONENTS))
        with self.assertRaises(FileExistsError):
            contract.build_bundle(self.bundle, self.manifest, self.parts, "test-key", self.key)
        for name in contract.COMPONENTS:
            with self.subTest(component=name):
                changed = self.changed(lambda values: values.__setitem__(name, b"untrusted replacement"))
                with self.assertRaises(contract.BackupError):
                    contract.verify_bundle(changed, {"test-key": self.key})

    def test_unknown_or_forged_signatures_cannot_authorize_sql(self):
        with self.assertRaisesRegex(contract.BackupError, "trusted"):
            contract.verify_bundle(self.bundle, {"other-key": self.key})
        with self.assertRaisesRegex(contract.BackupError, "signature"):
            contract.verify_bundle(self.bundle, {"test-key": b"different-test-key" * 3})
        changed = self.changed(lambda values: values.__setitem__("manifest.json", values["manifest.json"].replace(b'"users":4', b'"users":9')))
        with self.assertRaisesRegex(contract.BackupError, "signature"):
            contract.verify_bundle(changed, {"test-key": self.key})

    def test_repacking_restored_components_keeps_immutable_bundle_identity(self):
        before = self.bundle.read_bytes()
        for index, path in enumerate(self.parts.values()):
            os.utime(path, (1700000000 + index * 86400, 1700000000 + index * 86400))
            path.chmod(0o640)
        reconstructed = self.root / "restic-reconstructed.otqbackup"
        contract.build_bundle(reconstructed, self.signed, self.parts, "test-key", self.key)
        self.assertEqual(before, reconstructed.read_bytes())
        self.assertEqual(contract.file_digest(self.bundle), contract.file_digest(reconstructed))
        self.assertEqual(contract.verify_bundle(reconstructed, {"test-key": self.key}), self.signed)

    def test_member_paths_duplicate_json_and_size_limits_refuse_before_extraction(self):
        changed = self.changed(lambda values: values.__setitem__("../outside.sql", b"not extracted"))
        with self.assertRaisesRegex(contract.BackupError, "unexpected"):
            contract.verify_bundle(changed, {"test-key": self.key})
        changed = self.changed(lambda values: values.__setitem__("manifest.json", values["manifest.json"].replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')))
        with self.assertRaisesRegex(contract.BackupError, "duplicate"):
            contract.verify_bundle(changed, {"test-key": self.key})
        with self.assertRaisesRegex(contract.BackupError, "size"):
            contract.verify_bundle(self.bundle, {"test-key": self.key}, max_bytes=10)
        self.assertFalse((self.root.parent / "outside.sql").exists())

    def test_same_schema_is_insufficient_and_schema_mismatch_never_implicitly_migrates(self):
        current = self.signed["source"]
        self.assertTrue(contract.compatibility(self.signed, [current], current)["supported"])
        unknown = copy.deepcopy(self.signed)
        unknown["source"]["plugin_version"] = "future-custom-build"
        self.assertFalse(contract.compatibility(unknown, [current], current)["supported"])
        unknown["source"]["plugin_version"] = current["plugin_version"]
        unknown["source"]["image_digest"] = "sha256:" + "c" * 64
        self.assertFalse(contract.compatibility(unknown, [current], current)["supported"])
        mismatched_target = dict(current, schema_revision="different-plugin-schema")
        self.assertFalse(contract.compatibility(self.signed, [current], mismatched_target)["supported"])
        mismatched_target = dict(current, ctfd_schema_revision="different-native-schema")
        self.assertFalse(contract.compatibility(self.signed, [current], mismatched_target)["supported"])
