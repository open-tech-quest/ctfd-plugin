"""Release identity and failure tests; every Git operation uses a temporary repo."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("prepare_release", Path(__file__).with_name("prepare-release.py"))
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class PrepareReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quest-release-tests-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        release.git(self.repo, "init", "--initial-branch=main")
        release.git(self.repo, "config", "user.name", "Release test")
        release.git(self.repo, "config", "user.email", "release@example.test")
        release.git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / release.KUSTOMIZATION).parent.mkdir(parents=True)
        self.original_document = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ctfd.yaml
images:
  - name: ghcr.io/open-tech-quest/ctfd-plugin
    newTag: dev
  - name: redis
    newTag: 7.4.12
"""
        (self.repo / release.KUSTOMIZATION).write_text(self.original_document)
        (self.repo / "README.md").write_text("Release test fixture.\n")
        self.commit("Source implementation")
        self.source = release.git(self.repo, "rev-parse", "HEAD")
        release.git(self.repo, "tag", "v0.1.21")

    def commit(self, message):
        release.git(self.repo, "add", ".")
        release.git(self.repo, "commit", "-m", message)

    def check_source_unchanged(self):
        self.assertEqual(release.git(self.repo, "rev-parse", "HEAD"), self.source)
        self.assertEqual((self.repo / release.KUSTOMIZATION).read_text(), self.original_document)

    def test_next_version_sorts_semantically_and_ignores_non_release_tags(self):
        for tag in ("v0.1.9", "v0.1.22-rc1", "v0.01.90", "other-v99.0.0"):
            release.git(self.repo, "tag", tag)
        self.assertEqual(release.next_version(self.repo), {"previous": "0.1.21", "version": "0.1.22"})
        self.assertEqual(release.next_version(self.repo, bump="minor")["version"], "0.2.0")
        self.assertEqual(release.next_version(self.repo, bump="major")["version"], "1.0.0")

    def test_next_version_requires_a_matching_seed(self):
        with self.assertRaises(release.ReleaseError):
            release.next_version(self.repo, prefix="another-v")

    def test_prepared_commit_contains_only_version_metadata_and_leaves_main_untouched(self):
        result = release.prepare(self.repo, "0.1.22")
        self.assertNotEqual(result["source_commit"], self.source)
        self.assertEqual(result["source_parent"], self.source)
        self.assertEqual(release.git(self.repo, "rev-parse", "main"), self.source)
        self.assertEqual(release.git(self.repo, "branch", "--show-current"), "")
        self.assertEqual(release.git(self.repo, "tag", "--list"), "v0.1.21")
        document = (self.repo / release.KUSTOMIZATION).read_text()
        self.assertEqual(document, self.original_document.replace("newTag: dev", "newTag: 0.1.22"))
        changed = set(release.git(self.repo, "diff", "--name-only", self.source, "HEAD").splitlines())
        self.assertEqual(changed, {str(release.KUSTOMIZATION)})
        self.assertEqual(release.git(self.repo, "status", "--porcelain"), "")

    def test_consecutive_releases_need_no_version_edits_on_main(self):
        first = release.prepare(self.repo, "0.1.22")
        release.git(self.repo, "tag", "v0.1.22", first["source_commit"])
        release.git(self.repo, "checkout", "main")
        self.check_source_unchanged()
        self.assertEqual(release.next_version(self.repo)["version"], "0.1.23")
        second = release.prepare(self.repo, "0.1.23")
        self.assertEqual(second["source_parent"], self.source)
        self.assertEqual(release.image_version_document((self.repo / release.KUSTOMIZATION).read_text()), "0.1.23")
        self.assertEqual(release.git(self.repo, "show", "main:" + str(release.KUSTOMIZATION)), self.original_document.strip())

    def test_manifest_rejects_a_changed_pin_even_in_a_prepared_commit(self):
        release.prepare(self.repo, "0.1.22")
        path = self.repo / release.KUSTOMIZATION
        path.write_text(path.read_text().replace("newTag: 0.1.22", "newTag: 0.1.23"))
        release.git(self.repo, "add", str(release.KUSTOMIZATION))
        release.git(self.repo, "commit", "--amend", "--no-edit")
        with self.assertRaises(release.ReleaseError):
            release.release_manifest(self.repo, "0.1.22", "v", release.IMAGE + ":0.1.22", "sha256:" + "a" * 64)

    def test_repeated_preparation_reuses_exact_commit(self):
        first = release.prepare(self.repo, "0.1.22")
        second = release.prepare(self.repo, "0.1.22")
        self.assertEqual(first, second)

    def test_existing_tag_is_never_reused(self):
        release.git(self.repo, "tag", "v0.1.22")
        with self.assertRaises(release.ReleaseError):
            release.prepare(self.repo, "0.1.22")
        self.check_source_unchanged()

    def test_dirty_checkout_is_rejected_before_metadata_changes(self):
        for path in ("README.md", "untracked.txt"):
            with self.subTest(path=path):
                target = self.repo / path
                original = target.read_text() if target.exists() else None
                target.write_text("Unrelated work.\n")
                with self.assertRaises(release.ReleaseError):
                    release.prepare(self.repo, "0.1.22")
                self.check_source_unchanged()
                if original is None:
                    target.unlink()
                else:
                    target.write_text(original)

    def test_invalid_versions_and_prefix_do_not_modify_checkout(self):
        for version in ("v0.1.22", "0.01.22", "0.1.22\n", "0.1.22-rc1", "$(id)"):
            with self.subTest(version=version), self.assertRaises(release.ReleaseError):
                release.prepare(self.repo, version)
            self.check_source_unchanged()
        for prefix in ("bad prefix", "-v"):
            with self.subTest(prefix=prefix), self.assertRaises(release.ReleaseError):
                release.prepare(self.repo, "0.1.22", prefix=prefix)
            self.check_source_unchanged()

    def test_same_or_older_release_version_is_rejected(self):
        for version in ("0.1.21", "0.1.20"):
            with self.subTest(version=version), self.assertRaises(release.ReleaseError):
                release.prepare(self.repo, version)
            self.check_source_unchanged()

    def test_invalid_or_ambiguous_image_entries_fail_without_writing(self):
        cases = [
            self.original_document.replace("newTag: dev", "newTag: latest"),
            self.original_document.replace("newTag: dev", "digest: sha256:" + "0" * 64),
            self.original_document.replace("newTag: dev", "newTag: dev\n    newName: unrelated/image"),
            self.original_document.replace("newTag: dev", "newTag: dev\n    newTag: dev"),
            self.original_document.replace("  - name: redis", "  - name: ghcr.io/open-tech-quest/ctfd-plugin"),
        ]
        for document in cases:
            with self.subTest(document=document):
                (self.repo / release.KUSTOMIZATION).write_text(document)
                self.commit("Invalid metadata fixture")
                head = release.git(self.repo, "rev-parse", "HEAD")
                with self.assertRaises(release.ReleaseError):
                    release.prepare(self.repo, "0.1.22")
                self.assertEqual(release.git(self.repo, "rev-parse", "HEAD"), head)
                self.assertEqual((self.repo / release.KUSTOMIZATION).read_text(), document)

    def test_release_manifest_identifies_the_prepared_commit_and_published_digest(self):
        prepared = release.prepare(self.repo, "0.1.22")
        digest = "sha256:" + "a" * 64
        result = release.release_manifest(self.repo, "0.1.22", "v", release.IMAGE + ":0.1.22", digest)
        self.assertEqual(result, {**prepared, "digest": digest})
        self.assertEqual(result["source_commit"], release.git(self.repo, "rev-parse", "HEAD"))

    def test_manifest_rejects_wrong_digest_image_version_or_unprepared_source(self):
        with self.assertRaises(release.ReleaseError):
            release.release_manifest(self.repo, "0.1.21", "v", release.IMAGE + ":0.1.21", "sha256:" + "a" * 64)
        release.prepare(self.repo, "0.1.22")
        for image, digest in ((release.IMAGE + ":0.1.22", ""), (release.IMAGE + ":0.1.22", "sha256:bad"), (release.IMAGE + ":0.1.21", "sha256:" + "b" * 64)):
            with self.subTest(image=image, digest=digest), self.assertRaises(release.ReleaseError):
                release.release_manifest(self.repo, "0.1.22", "v", image, digest)

    def test_cli_outputs_are_metadata_only_and_manifest_json_round_trips(self):
        output = Path(self.temp.name) / "github-output"
        script = Path(__file__).with_name("prepare-release.py")
        result = subprocess.run([sys.executable, str(script), "--repo", str(self.repo), "--github-output", str(output), "prepare", "--version", "0.1.22"], text=True, capture_output=True, check=True)
        prepared = json.loads(result.stdout)
        self.assertIn("source_commit=" + prepared["source_commit"], output.read_text())
        asset = Path(self.temp.name) / "release" / "release.json"
        result = subprocess.run([sys.executable, str(script), "--repo", str(self.repo), "manifest", "--version", "0.1.22", "--image", release.IMAGE + ":0.1.22", "--digest", "sha256:" + "c" * 64, "--output", str(asset)], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), json.loads(asset.read_text()))
        self.assertEqual(release.git(self.repo, "rev-parse", "main"), self.source)

    def test_yaml_editor_preserves_quotes_comments_and_other_images(self):
        original = self.original_document.replace("newTag: dev", 'newTag: "dev" # release')
        actual = release.image_version_document(original, "0.1.22")
        self.assertEqual(actual, original.replace('"dev"', '"0.1.22"'))


if __name__ == "__main__":
    unittest.main()
