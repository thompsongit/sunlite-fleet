from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_config

_ARCHIVE_FILES = {"config.toml", "database.sqlite3", "manifest.json", "web.env"}
_PAYLOAD_FILES = ("config.toml", "database.sqlite3", "web.env")
_MAX_ARCHIVE_FILE_BYTES = 256 * 1024 * 1024


def check_deployment(config_path: Path, environment_path: Path | None = None) -> None:
    config = load_config(config_path)
    if config.database_path != "/var/lib/sunlite-scheduler/sunlite.db":
        raise ValueError("production database_path must use /var/lib/sunlite-scheduler")
    if config.controller_socket != "/run/sunlite-scheduler/controller.sock":
        raise ValueError("production controller_socket must use /run/sunlite-scheduler")
    if environment_path is not None:
        _validate_environment(environment_path.read_bytes())


def create_backup(
    database_path: Path,
    config_path: Path,
    environment_path: Path,
    output_path: Path,
    release: str,
) -> None:
    for path in (database_path, config_path, environment_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    check_deployment(config_path, environment_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(output_path)

    with tempfile.TemporaryDirectory(prefix="sunlite-backup-") as directory:
        snapshot = Path(directory) / "database.sqlite3"
        _snapshot_database(database_path, snapshot)
        payload = {
            "config.toml": config_path.read_bytes(),
            "database.sqlite3": snapshot.read_bytes(),
            "web.env": environment_path.read_bytes(),
        }
        created_at = datetime.now(UTC)
        manifest = {
            "schema": 1,
            "created_at": created_at.isoformat(),
            "release": release,
            "sha256": {name: _digest(data) for name, data in payload.items()},
        }
        payload["manifest.json"] = json.dumps(
            manifest, indent=2, sort_keys=True
        ).encode() + b"\n"
        _write_archive(output_path, payload, int(created_at.timestamp()))


def verify_backup(archive_path: Path) -> dict[str, Any]:
    return _verify_payload(_read_archive(archive_path))


def _verify_payload(payload: dict[str, bytes]) -> dict[str, Any]:
    manifest = json.loads(payload["manifest.json"])
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("unsupported backup manifest")
    checksums = manifest.get("sha256")
    if not isinstance(checksums, dict):
        raise ValueError("backup checksums are missing")
    for name in _PAYLOAD_FILES:
        if checksums.get(name) != _digest(payload[name]):
            raise ValueError(f"backup checksum mismatch: {name}")

    with tempfile.TemporaryDirectory(prefix="sunlite-verify-") as directory:
        root = Path(directory)
        database = root / "database.sqlite3"
        config = root / "config.toml"
        environment = root / "web.env"
        database.write_bytes(payload["database.sqlite3"])
        config.write_bytes(payload["config.toml"])
        environment.write_bytes(payload["web.env"])
        _check_database(database)
        check_deployment(config, environment)
    return manifest


def restore_backup(
    archive_path: Path,
    database_path: Path,
    config_path: Path,
    environment_path: Path,
) -> None:
    payload = _read_archive(archive_path)
    _verify_payload(payload)
    staged: list[tuple[Path, Path]] = []
    targets = {
        "database.sqlite3": database_path,
        "config.toml": config_path,
        "web.env": environment_path,
    }
    try:
        for name, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", dir=target.parent
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload[name])
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.chmod(0o640)
            staged.append((temporary_path, target))
        database_path.with_name(f"{database_path.name}-wal").unlink(missing_ok=True)
        database_path.with_name(f"{database_path.name}-shm").unlink(missing_ok=True)
        for staged_path, target in staged:
            staged_path.replace(target)
    finally:
        for staged_path, _target in staged:
            staged_path.unlink(missing_ok=True)


def _snapshot_database(source: Path, target: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)
    _check_database(target)


def _check_database(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError("SQLite quick_check failed")


def _validate_environment(data: bytes) -> None:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(data.decode().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment line {number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")

    required = (
        "SUNLITE_CF_TEAM_DOMAIN",
        "SUNLITE_CF_AUDIENCE",
        "SUNLITE_SESSION_SECRET",
        "SUNLITE_ALLOWED_HOSTS",
        "SUNLITE_CONTROLLER_SOCKET",
    )
    if values.get("SUNLITE_ACCESS_REQUIRED", "").lower() not in {"1", "true", "yes", "on"}:
        raise ValueError("Cloudflare Access must be required in production")
    for key in required:
        value = values.get(key, "")
        if not value or "replace" in value.lower() or "__" in value:
            raise ValueError(f"{key} is not configured")
    if len(values["SUNLITE_SESSION_SECRET"]) < 32:
        raise ValueError("SUNLITE_SESSION_SECRET must contain at least 32 characters")
    if not values["SUNLITE_CF_TEAM_DOMAIN"].startswith("https://"):
        raise ValueError("SUNLITE_CF_TEAM_DOMAIN must use https://")
    if values["SUNLITE_CONTROLLER_SOCKET"] != "/run/sunlite-scheduler/controller.sock":
        raise ValueError("SUNLITE_CONTROLLER_SOCKET must use /run/sunlite-scheduler")
    if not values.get("SUNLITE_ADMIN_EMAILS") and not values.get("SUNLITE_OPERATOR_EMAILS"):
        raise ValueError("configure at least one administrator or operator email")
    role_emails = ",".join(
        (values.get("SUNLITE_ADMIN_EMAILS", ""), values.get("SUNLITE_OPERATOR_EMAILS", ""))
    )
    if "replace" in role_emails.lower() or "@" not in role_emails:
        raise ValueError("administrator and operator emails are not configured")
    hosts = {item.strip() for item in values["SUNLITE_ALLOWED_HOSTS"].split(",")}
    if hosts <= {"127.0.0.1", "localhost", "testserver"}:
        raise ValueError("SUNLITE_ALLOWED_HOSTS must include the public hostname")
    if values.get("SUNLITE_COOKIE_SECURE", "true").lower() not in {"1", "true", "yes", "on"}:
        raise ValueError("secure cookies are required in production")


def _write_archive(path: Path, payload: dict[str, bytes], modified_at: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with tarfile.open(temporary_path, "w:gz") as archive:
            for name in sorted(payload):
                data = payload[name]
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o600
                member.mtime = modified_at
                archive.addfile(member, io.BytesIO(data))
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_archive(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(_ARCHIVE_FILES) or {
            member.name for member in members
        } != _ARCHIVE_FILES:
            raise ValueError("backup contains unexpected or missing files")
        if any(not member.isfile() or member.size > _MAX_ARCHIVE_FILE_BYTES for member in members):
            raise ValueError("backup contains an invalid file")
        payload: dict[str, bytes] = {}
        for member in members:
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot read backup member: {member.name}")
            payload[member.name] = stream.read(_MAX_ARCHIVE_FILE_BYTES + 1)
            if len(payload[member.name]) > _MAX_ARCHIVE_FILE_BYTES:
                raise ValueError(f"backup member is too large: {member.name}")
    return payload


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain a Sunlite deployment")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="Validate deployment configuration")
    check.add_argument("--config", type=Path, required=True)
    check.add_argument("--environment", type=Path)

    backup = commands.add_parser("backup", help="Create a verified backup archive")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--config", type=Path, required=True)
    backup.add_argument("--environment", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--release", required=True)

    verify = commands.add_parser("verify", help="Verify a backup archive")
    verify.add_argument("archive", type=Path)

    restore = commands.add_parser("restore", help="Restore a verified backup archive")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--database", type=Path, required=True)
    restore.add_argument("--config", type=Path, required=True)
    restore.add_argument("--environment", type=Path, required=True)

    arguments = parser.parse_args()
    if arguments.command == "check":
        check_deployment(arguments.config, arguments.environment)
    elif arguments.command == "backup":
        create_backup(
            arguments.database,
            arguments.config,
            arguments.environment,
            arguments.output,
            arguments.release,
        )
    elif arguments.command == "verify":
        print(json.dumps(verify_backup(arguments.archive), sort_keys=True))
    else:
        restore_backup(
            arguments.archive,
            arguments.database,
            arguments.config,
            arguments.environment,
        )
