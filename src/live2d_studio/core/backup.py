"""Find and remove the timestamped ``*.bak-*`` files the studio creates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

BACKUP_PATTERN = re.compile(r"\.bak-\d{8}-\d{6}(?:-\d+)?$")


@dataclass(frozen=True)
class BackupFile:
    """One backup file, with the original file it belongs to."""

    path: Path
    original: str
    size: int
    modified: datetime


def is_backup(path: str | Path) -> bool:
    return bool(BACKUP_PATTERN.search(Path(path).name))


def find_backups(root: str | Path) -> list[BackupFile]:
    """List backups below ``root``, newest first.  Missing folders are ignored."""
    directory = Path(root).expanduser().resolve()
    if not directory.is_dir():
        return []

    backups: list[BackupFile] = []
    try:
        candidates = list(directory.rglob("*"))
    except OSError:
        return []
    for path in candidates:
        if not path.is_file() or not is_backup(path):
            continue
        original = BACKUP_PATTERN.sub("", path.name)
        try:
            stat = path.stat()
        except OSError:
            continue
        backups.append(
            BackupFile(
                path=path,
                original=original,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    backups.sort(key=lambda item: item.modified, reverse=True)
    return backups


def delete_backups(backups: Iterable[BackupFile]) -> tuple[int, list[str]]:
    """Delete backups; returns ``(deleted count, failure messages)``."""
    deleted = 0
    failures: list[str] = []
    for backup in backups:
        try:
            backup.path.unlink()
        except OSError as exc:
            failures.append(f"{backup.path.name}：{exc}")
        else:
            deleted += 1
    return deleted, failures
