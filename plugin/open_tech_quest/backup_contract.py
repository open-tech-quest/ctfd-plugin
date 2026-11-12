"""Authenticated portable backups. No CTFd imports, SQL execution or extraction.

The external deployment worker owns trust keys and explicit build qualifications.
This module is also imported by file by that worker.
"""
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "open-tech-quest/backup"
SCHEMA_VERSION = 1
COMPONENTS = ("database.sql", "uploads.tar.gz", "infrastructure.json")
DEFAULT_MAX_BYTES = 8 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SIGNATURE_BYTES = 4096
SOURCE_FIELDS = ("ctfd_version", "plugin_version", "schema_revision", "ctfd_schema_revision", "image_digest")


class BackupError(ValueError):
    """A safe administrator-readable refusal, without payload bytes."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def fingerprint(manifest):
    return hashlib.sha256(canonical(manifest)).hexdigest()


def file_digest(path):
    size, digest = 0, hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _object(value, label):
    if not isinstance(value, dict):
        raise BackupError(label + " must be an object")
    return value


def _text(value, label, maximum=512):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise BackupError(label + " is missing or invalid")
    return value


def _json(raw, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BackupError(label + " contains duplicate fields")
            result[key] = value
        return result
    def invalid(_):
        raise BackupError(label + " contains a non-finite number")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BackupError(label + " is not valid UTF-8 JSON") from exc


def parse_timestamp(value):
    """Read RFC3339 precision consistently without rewriting signed metadata."""
    # Restic emits nanoseconds; Python 3.9 accepts only 3 or 6 fractional digits.
    value = re.sub(r"\.(\d+)(?=Z$|[+-]\d{2}:\d{2}$)",
                   lambda match: "." + match.group(1)[:6].ljust(6, "0"), value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_manifest(manifest, max_bytes=DEFAULT_MAX_BYTES):
    _object(manifest, "Manifest")
    if manifest.get("format") != FORMAT or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise BackupError("This backup format requires an operator-qualified importer")
    if not isinstance(manifest.get("snapshot_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", manifest["snapshot_id"]):
        raise BackupError("The backup snapshot identity is invalid")
    stamp = _text(manifest.get("created_at"), "Backup time", 64)
    try:
        created = parse_timestamp(stamp)
        if created.tzinfo is None or created.utcoffset() != timezone.utc.utcoffset(created):
            raise ValueError()
    except ValueError as exc:
        raise BackupError("The backup time must be UTC") from exc
    source = _object(manifest.get("source"), "Backup producer")
    for field in SOURCE_FIELDS:
        _text(source.get(field), "Backup producer " + field)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", source["image_digest"]):
        raise BackupError("The backup must identify an immutable producer image")
    instance = _object(manifest.get("instance"), "Backup instance")
    _text(instance.get("id"), "Backup instance identity", 128)
    _text(instance.get("name"), "Backup instance name", 256)
    parts = _object(manifest.get("components"), "Backup components")
    if set(parts) != set(COMPONENTS):
        raise BackupError("The backup must contain the complete database, uploads and infrastructure configuration")
    total = 0
    for part in parts.values():
        _object(part, "Backup component")
        size, checksum = part.get("bytes"), part.get("sha256")
        if type(size) is not int or size <= 0 or size > max_bytes:
            raise BackupError("A backup component exceeds the allowed size")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise BackupError("A backup component checksum is invalid")
        total += size
    if total > max_bytes:
        raise BackupError("The backup exceeds the allowed total size")
    counts = _object(manifest.get("counts"), "Backup counts")
    for field in ("users", "teams", "challenges", "solves"):
        if type(counts.get(field)) is not int or counts[field] < 0:
            raise BackupError("The backup comparison counts are invalid")
    if len(canonical(manifest)) > MAX_MANIFEST_BYTES:
        raise BackupError("The backup manifest is too large")
    return manifest


def signature(manifest, key_id, key):
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id):
        raise BackupError("The signing key identity is invalid")
    if not isinstance(key, bytes) or len(key) < 32:
        raise BackupError("Backup signing keys must contain at least 32 bytes")
    value = hmac.new(key, b"open-tech-quest/backup/v1\0" + canonical(manifest), hashlib.sha256).hexdigest()
    return {"algorithm": "hmac-sha256", "key_id": key_id, "signature": value}


def build_bundle(destination, manifest, components, key_id, key, max_bytes=DEFAULT_MAX_BYTES):
    if set(components) != set(COMPONENTS):
        raise BackupError("Supply the complete database, uploads and infrastructure configuration")
    manifest = dict(manifest, components={name: file_digest(components[name]) for name in COMPONENTS})
    validate_manifest(manifest, max_bytes)
    signed = signature(manifest, key_id, key)
    path = Path(destination)
    def entry(name):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        info.compress_type = zipfile.ZIP_STORED
        return info
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        try:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
                bundle.writestr(entry("manifest.json"), canonical(manifest))
                bundle.writestr(entry("signature.json"), canonical(signed))
                for name in COMPONENTS:
                    info = entry(name)
                    info.file_size = manifest["components"][name]["bytes"]
                    with Path(components[name]).open("rb") as source, bundle.open(info, "w", force_zip64=True) as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    path.chmod(0o600)
    try:
        # Do not publish bytes changed between the initial hash and streaming.
        verify_bundle(path, {key_id: key}, max_bytes=max_bytes)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return manifest


def verify_bundle(path, trusted_keys, max_bytes=DEFAULT_MAX_BYTES):
    path = Path(path)
    if path.stat().st_size > max_bytes + MAX_MANIFEST_BYTES + 65536:
        raise BackupError("The backup exceeds the allowed total size")
    try:
        with zipfile.ZipFile(path, "r") as bundle:
            members = bundle.infolist()
            expected = {"manifest.json", "signature.json", *COMPONENTS}
            if len(members) != len(expected) or {m.filename for m in members} != expected:
                raise BackupError("The backup contains missing, duplicate or unexpected files")
            for member in members:
                kind = (member.external_attr >> 16) & 0o170000
                if member.flag_bits & 1 or member.is_dir() or kind not in (0, stat.S_IFREG):
                    raise BackupError("The backup contains an unsupported file entry")
                limit = MAX_MANIFEST_BYTES if member.filename == "manifest.json" else MAX_SIGNATURE_BYTES if member.filename == "signature.json" else max_bytes
                if member.file_size > limit:
                    raise BackupError("A backup component exceeds the allowed size")
            manifest = validate_manifest(_json(bundle.read("manifest.json"), "Manifest"), max_bytes)
            signed = _object(_json(bundle.read("signature.json"), "Signature"), "Signature")
            key_id = signed.get("key_id")
            if signed.get("algorithm") != "hmac-sha256" or not isinstance(key_id, str) or key_id not in trusted_keys:
                raise BackupError("This backup is not signed by a trusted worker key; operator review is required")
            expected_signature = signature(manifest, key_id, trusted_keys[key_id])["signature"]
            provided = signed.get("signature")
            if not isinstance(provided, str) or not re.fullmatch(r"[0-9a-f]{64}", provided) or not hmac.compare_digest(expected_signature, provided):
                raise BackupError("The backup signature does not match its manifest")
            for name in COMPONENTS:
                part = manifest["components"][name]
                if bundle.getinfo(name).file_size != part["bytes"]:
                    raise BackupError("A backup component size does not match the signed manifest")
                total, digest = 0, hashlib.sha256()
                with bundle.open(name) as source:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > part["bytes"]:
                            raise BackupError("A backup component exceeds its signed size")
                        digest.update(block)
                if total != part["bytes"] or not hmac.compare_digest(digest.hexdigest(), part["sha256"]):
                    raise BackupError("A backup component checksum does not match the signed manifest")
            return manifest
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise BackupError("The backup is damaged or uses an unsupported ZIP format") from exc


def compatibility(manifest, qualified_sources, target):
    validate_manifest(manifest)
    source = manifest["source"]
    if any(source[field] != target.get(field) for field in ("schema_revision", "ctfd_schema_revision")):
        return {"supported": False, "reason": "Backup and target schemas differ; a tested operator migration is required"}
    if not any(all(source.get(field) == allowed.get(field) for field in SOURCE_FIELDS) for allowed in qualified_sources):
        return {"supported": False, "reason": "This exact backup producer is not qualified for this installation; operator review is required"}
    return {"supported": True, "reason": None}
