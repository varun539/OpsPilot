"""Consistent backup and restore for OpsPilot persistent data.

Run this **inside the backend container** with the application stopped (see
``scripts/compose-backup.sh``, which enforces that):

    python scripts/backup_restore.py backup  --output-dir /var/lib/opspilot/backups/<stamp>
    python scripts/backup_restore.py verify  --backup-dir /var/lib/opspilot/backups/<stamp>
    python scripts/backup_restore.py restore --backup-dir /var/lib/opspilot/backups/<stamp>

Why offline: ChromaDB stores its index in SQLite files that must not be copied
while open. Both application databases are copied with the SQLite backup API
(which would also be safe online), but the whole procedure takes the backend
down for a few seconds so the vector store is quiescent too.

Restore never silently overwrites existing data: it refuses non-empty targets
unless ``--force`` is passed, and it preserves the previous data next to the
target with a ``.pre-restore-<timestamp>`` suffix.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_DB = Path("data/opspilot_app.sqlite")
DEFAULT_CHECKPOINT_DB = Path("data/opspilot_checkpoints.sqlite")
SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")


def app_db_path() -> Path:
    raw = _env("OPSPILOT_APP_DB")
    return Path(raw) if raw else DEFAULT_APP_DB


def checkpoint_db_path() -> Path:
    raw = _env("OPSPILOT_CHECKPOINT_DB")
    return Path(raw) if raw else DEFAULT_CHECKPOINT_DB


def chroma_dir_path() -> Path:
    raw = _env("OPSPILOT_CHROMA_DIR")
    return Path(raw) if raw else REPO_ROOT / "chroma"


def _env(name: str) -> str:
    import os

    return os.getenv(name, "").strip()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _integrity_check(path: Path) -> str:
    """Return the SQLite integrity_check result for ``path``."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    finally:
        connection.close()


def _sqlite_backup(source: Path, destination: Path) -> None:
    """Copy a SQLite database through the online backup API."""
    if not source.is_file():
        raise FileNotFoundError(f"database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source), timeout=30)
    try:
        destination_connection = sqlite3.connect(str(destination))
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()


def _db_files(path: Path) -> list[Path]:
    return [Path(f"{path}{suffix}") for suffix in SQLITE_SUFFIXES]


def _count_files(directory: Path, *, skip_preserved: bool = False) -> int:
    count = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if skip_preserved and any(
            part.startswith(".pre-restore-") for part in path.relative_to(directory).parts
        ):
            continue
        count += 1
    return count


def command_backup(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    if output.exists() and any(output.iterdir()):
        print(f"refusing to write into non-empty directory: {output}", file=sys.stderr)
        return 1
    output.mkdir(parents=True, exist_ok=True)

    sources = {
        "app.sqlite": app_db_path(),
        "checkpoints.sqlite": checkpoint_db_path(),
    }
    report: dict[str, object] = {
        "created_at": _timestamp(),
        "databases": {},
        "chroma": {},
    }

    for name, source in sources.items():
        destination = output / name
        _sqlite_backup(source, destination)
        integrity = _integrity_check(destination)
        report["databases"][name] = {  # type: ignore[index]
            "source": source.name,
            "bytes": destination.stat().st_size,
            "integrity_check": integrity,
        }
        print(f"backed up {source} -> {destination} (integrity: {integrity})")
        if integrity != "ok":
            print("backup integrity check failed", file=sys.stderr)
            return 1

    chroma_source = chroma_dir_path()
    if chroma_source.is_dir():
        chroma_target = output / "chroma"
        shutil.copytree(chroma_source, chroma_target)
        files = _count_files(chroma_target)
        report["chroma"] = {"source": chroma_source.name, "files": files}  # type: ignore[index]
        print(f"backed up {chroma_source} -> {chroma_target} ({files} files)")
    else:
        report["chroma"] = {"source": chroma_source.name, "files": 0}  # type: ignore[index]
        print(f"chroma directory not present, skipping: {chroma_source}")

    (output / "manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"backup complete: {output}")
    return 0


def _verify_backup(directory: Path) -> bool:
    if not directory.is_dir():
        print(f"backup directory not found: {directory}", file=sys.stderr)
        return False

    ok = True
    for name in ("app.sqlite", "checkpoints.sqlite"):
        path = directory / name
        if not path.is_file():
            print(f"missing backup file: {path}", file=sys.stderr)
            ok = False
            continue
        try:
            integrity = _integrity_check(path)
        except sqlite3.Error as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            ok = False
            continue
        print(f"{name}: integrity_check={integrity}, bytes={path.stat().st_size}")
        if integrity != "ok":
            ok = False

    chroma = directory / "chroma"
    if chroma.is_dir():
        print(f"chroma: {_count_files(chroma)} files")
    else:
        print("chroma: not present in backup (acceptable for an empty vector store)")

    print("verification: OK" if ok else "verification: FAILED")
    return ok


def command_verify(args: argparse.Namespace) -> int:
    return 0 if _verify_backup(Path(args.backup_dir)) else 1


def _move_aside(path: Path, stamp: str) -> None:
    target = path.with_name(f"{path.name}.pre-restore-{stamp}")
    if target.exists():
        raise FileExistsError(f"preserved copy already exists: {target}")
    path.rename(target)
    print(f"preserved previous data: {target}")


def _move_directory_contents_aside(directory: Path, stamp: str) -> None:
    """Preserve a directory's contents without moving the mountpoint itself.

    ChromaDB lives at the root of a persistent volume, so the directory cannot
    be renamed and its children cannot cross the mount boundary. The preserved
    copy therefore stays inside the volume under a hidden sibling directory.
    """
    preserved = directory / f".pre-restore-{stamp}"
    preserved.mkdir(parents=True, exist_ok=False)
    for child in list(directory.iterdir()):
        if child == preserved:
            continue
        child.rename(preserved / child.name)
    print(f"preserved previous data: {preserved}")


def command_restore(args: argparse.Namespace) -> int:
    backup = Path(args.backup_dir)
    if not _verify_backup(backup):
        print("refusing to restore an unverified backup", file=sys.stderr)
        return 1

    targets = {
        "app.sqlite": app_db_path(),
        "checkpoints.sqlite": checkpoint_db_path(),
    }
    existing = [path for path in targets.values() if path.exists()]
    chroma_target = chroma_dir_path()
    if chroma_target.is_dir() and any(chroma_target.iterdir()):
        existing.append(chroma_target)

    if existing and not args.force:
        print(
            "refusing to overwrite existing data (pass --force to replace): "
            + ", ".join(str(path) for path in existing),
            file=sys.stderr,
        )
        return 1

    stamp = _timestamp()
    for name, target in targets.items():
        for path in _db_files(target):
            if path.exists():
                _move_aside(path, stamp)
        _sqlite_backup(backup / name, target)
        integrity = _integrity_check(target)
        print(f"restored {name} -> {target} (integrity: {integrity})")
        if integrity != "ok":
            print("restored database failed integrity check", file=sys.stderr)
            return 1

    chroma_backup = backup / "chroma"
    chroma_target.mkdir(parents=True, exist_ok=True)
    if any(chroma_target.iterdir()):
        _move_directory_contents_aside(chroma_target, stamp)
    if chroma_backup.is_dir():
        for child in chroma_backup.iterdir():
            if child.is_dir():
                shutil.copytree(child, chroma_target / child.name)
            else:
                shutil.copy2(child, chroma_target / child.name)
        print(
            "restored chroma -> "
            f"{chroma_target} ({_count_files(chroma_target, skip_preserved=True)} files)"
        )
    else:
        print("backup has no chroma data; left the chroma directory empty")

    print("restore complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="create a consistent backup")
    backup.add_argument("--output-dir", required=True)
    backup.set_defaults(handler=command_backup)

    verify = subparsers.add_parser("verify", help="verify a backup in isolation")
    verify.add_argument("--backup-dir", required=True)
    verify.set_defaults(handler=command_verify)

    restore = subparsers.add_parser("restore", help="restore a verified backup")
    restore.add_argument("--backup-dir", required=True)
    restore.add_argument("--force", action="store_true")
    restore.set_defaults(handler=command_restore)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
