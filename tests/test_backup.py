"""Tests for backup discovery and cleanup."""

from __future__ import annotations

from pathlib import Path

from live2d_studio.core.backup import (
    delete_backups,
    find_backups,
    is_backup,
)


def _touch(path: Path, text: str = "backup") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_is_backup_matches_timestamp_names():
    assert is_backup("model.model3.json.bak-20240101-120000")
    assert is_backup("smile.exp3.json.bak-20240101-120000-2")
    assert not is_backup("model.model3.json")
    assert not is_backup(".model.model3.json.abcd1234.tmp")
    assert not is_backup("notes.bak-2024")


def test_find_backups_scans_nested_folders_newest_first(tmp_path: Path):
    older = _touch(tmp_path / "model.model3.json.bak-20240101-120000")
    newer = _touch(tmp_path / "expressions" / "smile.exp3.json.bak-20240102-120000")
    # Regular files and temporary files must be ignored
    _touch(tmp_path / "model.model3.json")
    _touch(tmp_path / ".model.model3.json.abcd1234.tmp")

    backups = find_backups(tmp_path)
    assert {item.path for item in backups} == {older, newer}
    assert {item.original for item in backups} == {
        "model.model3.json",
        "smile.exp3.json",
    }
    assert all(item.size > 0 for item in backups)
    assert backups[0].path == newer

    deleted, failures = delete_backups(backups)
    assert deleted == 2
    assert failures == []
    assert find_backups(tmp_path) == []
    assert (tmp_path / "model.model3.json").is_file()


def test_missing_folder_has_no_backups(tmp_path: Path):
    assert find_backups(tmp_path / "missing") == []
