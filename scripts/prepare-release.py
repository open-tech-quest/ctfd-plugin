#!/usr/bin/env python3
"""Prepare versioned deployment metadata in a local, release-only Git commit.

This helper never pushes a branch or tag. The publication workflow tests the
prepared commit, publishes its image, then creates its immutable release tag.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


IMAGE = "ghcr.io/open-tech-quest/ctfd-plugin"
KUSTOMIZATION = Path("deploy/base/kustomization.yaml")
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ReleaseError(ValueError):
    pass


def git(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True)
    if result.returncode:
        raise ReleaseError(result.stderr.strip() or f"git {arguments[0]} failed")
    return result.stdout.strip()


def version_tuple(value):
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ReleaseError("Version must be an X.Y.Z release without leading zeroes or a prefix")
    return tuple(int(part) for part in match.groups())


def validate_prefix(repo, prefix):
    if prefix.startswith("-"):
        raise ReleaseError("Tag prefix must not begin with a hyphen")
    git(repo, "check-ref-format", "refs/tags/" + prefix + "0.0.0")


def next_version(repo, prefix="v", bump="patch"):
    validate_prefix(repo, prefix)
    candidates = []
    for tag in git(repo, "tag", "--list").splitlines():
        if tag.startswith(prefix) and VERSION_PATTERN.fullmatch(tag[len(prefix):]):
            candidates.append(version_tuple(tag[len(prefix):]))
    if not candidates:
        raise ReleaseError(f"No {prefix}X.Y.Z seed tag exists")
    previous = max(candidates)
    major, minor, patch = previous
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    elif bump == "patch":
        patch += 1
    else:
        raise ReleaseError("Version bump must be patch, minor or major")
    return {"previous": ".".join(map(str, previous)), "version": f"{major}.{minor}.{patch}"}


def image_version_document(document, desired=None):
    """Read or stamp the single release image pin without rewriting other YAML."""
    lines = document.splitlines(keepends=True)
    roots = [i for i, line in enumerate(lines) if re.fullmatch(r"images:\s*(?:#.*)?\n?", line)]
    if len(roots) != 1:
        raise ReleaseError("Deployment Kustomization must have exactly one top-level images list")
    start = roots[0] + 1
    end = len(lines)
    for i in range(start, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "-", "#")):
            end = i
            break
    item_starts = [i for i in range(start, end) if re.match(r"^\s*-\s+", lines[i])]
    entries = []
    for number, first in enumerate(item_starts):
        last = item_starts[number + 1] if number + 1 < len(item_starts) else end
        fields = {}
        for i in range(first, last):
            match = re.match(r"^\s*(?:-\s+)?([A-Za-z][A-Za-z0-9]*):\s*(.*?)\s*$", lines[i])
            if match:
                key, raw = match.groups()
                if key in fields:
                    raise ReleaseError("Duplicate field in deployment image entry")
                value = raw.split(" #", 1)[0].strip().strip("\"'")
                fields[key] = (value, i)
        if fields.get("name", (None,))[0] == IMAGE:
            entries.append(fields)
    if len(entries) != 1:
        raise ReleaseError("Deployment Kustomization must name the CTFd image exactly once")
    fields = entries[0]
    if "digest" in fields or "newName" in fields or "newTag" not in fields:
        raise ReleaseError("Release image entry must use name and newTag without digest or newName")
    current = fields["newTag"][0]
    if current != "dev":
        version_tuple(current)
    index = fields["newTag"][1]
    match = re.fullmatch(r"(\s*newTag:\s*)([\"']?)(dev|[0-9]+\.[0-9]+\.[0-9]+)\2(\s*(?:#.*)?)(\n?)", lines[index])
    if not match:
        raise ReleaseError("Deployment image newTag must be dev or a semantic version")
    if desired is None:
        return current
    prefix, quote, _, suffix, newline = match.groups()
    lines[index] = prefix + quote + desired + quote + suffix + newline
    return "".join(lines)


def check_clean(repo):
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError("Release preparation requires a clean checkout")


def prepared_identity(repo, version, prefix):
    subject = f"Release {prefix}{version} deployment metadata"
    if git(repo, "log", "-1", "--format=%s") != subject:
        raise ReleaseError("Current commit is not the prepared release commit")
    changed = set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
    if changed != {str(KUSTOMIZATION)}:
        raise ReleaseError("Prepared release commit must change only the deployment image pin")
    return {"source_commit": git(repo, "rev-parse", "HEAD"), "source_parent": git(repo, "rev-parse", "HEAD^")}


def prepare(repo, version, prefix="v"):
    requested = version_tuple(version)
    validate_prefix(repo, prefix)
    check_clean(repo)
    tag = prefix + version
    if tag in git(repo, "tag", "--list").splitlines():
        raise ReleaseError(f"Release tag {tag} already exists")
    document = (repo / KUSTOMIZATION).read_text()
    current = image_version_document(document)
    updated = image_version_document(document, version)
    subject = f"Release {tag} deployment metadata"
    if current == version:
        # A repeated invocation after preparation reuses the same exact commit.
        # Matching version metadata in an ordinary source commit is not enough.
        prepared_identity(repo, version, prefix)
    else:
        previous = version_tuple(next_version(repo, prefix)["previous"])
        if requested <= previous or (current != "dev" and requested <= version_tuple(current)):
            raise ReleaseError("Release version must be newer than existing releases")
        git(repo, "ls-files", "--error-unmatch", str(KUSTOMIZATION))
        # Leave the local main branch pointing at its source commit, too. Only
        # the detached HEAD advances; a later publication may tag that commit.
        git(repo, "checkout", "--detach", "HEAD")
        (repo / KUSTOMIZATION).write_text(updated)
        git(repo, "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "--only", "-m", subject, "--", str(KUSTOMIZATION))
    check_clean(repo)
    return {"version": version, "tag": tag, **prepared_identity(repo, version, prefix), "image": IMAGE + ":" + version}


def release_manifest(repo, version, prefix, image, digest):
    version_tuple(version)
    validate_prefix(repo, prefix)
    check_clean(repo)
    identity = prepared_identity(repo, version, prefix)
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError("Published image digest must be sha256 followed by 64 lowercase hex digits")
    if image != IMAGE + ":" + version:
        raise ReleaseError("Published image must match the release version and deployment image")
    if image_version_document((repo / KUSTOMIZATION).read_text()) != version:
        raise ReleaseError("Prepared deployment image pin does not match the release")
    return {"version": version, "tag": prefix + version, **identity, "image": image, "digest": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("next-version")
    select.add_argument("--prefix", default="v")
    select.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    stage = commands.add_parser("prepare")
    stage.add_argument("--version", required=True)
    stage.add_argument("--prefix", default="v")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--prefix", default="v")
    manifest.add_argument("--image", required=True)
    manifest.add_argument("--digest", required=True)
    manifest.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "next-version":
            result = next_version(arguments.repo, arguments.prefix, arguments.bump)
        elif arguments.command == "prepare":
            result = prepare(arguments.repo, arguments.version, arguments.prefix)
        else:
            result = release_manifest(arguments.repo, arguments.version, arguments.prefix, arguments.image, arguments.digest)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(json.dumps(result, indent=2) + "\n")
        if arguments.github_output:
            with arguments.github_output.open("a") as output:
                for key, value in result.items():
                    if "\n" in value or "\r" in value:
                        raise ReleaseError("Release outputs must be single-line strings")
                    output.write(key + "=" + value + "\n")
        print(json.dumps(result))
    except (ReleaseError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
