from __future__ import annotations

import binascii
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import Diagnostic


OWNED_TAG = "ES-DE Favorites Sync"
FAVORITES_TAG = "ES-DE Favorites"


@dataclass
class SteamImportResult:
    ok: bool
    written: bool = False
    users_seen: int = 0
    users_written: int = 0
    entries_imported: int = 0
    backups: list[str] = field(default_factory=list)
    shortcuts_files: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "written": self.written,
            "users_seen": self.users_seen,
            "users_written": self.users_written,
            "entries_imported": self.entries_imported,
            "backups": self.backups,
            "shortcuts_files": self.shortcuts_files,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass
class SteamCleanupResult:
    ok: bool
    removed: int = 0
    users_seen: int = 0
    users_written: int = 0
    backups: list[str] = field(default_factory=list)
    shortcuts_files: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "removed": self.removed,
            "users_seen": self.users_seen,
            "users_written": self.users_written,
            "backups": self.backups,
            "shortcuts_files": self.shortcuts_files,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass
class SteamLibraryStatus:
    ok: bool
    users_seen: int = 0
    desired: int = 0
    previous: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def needs_reconcile(self) -> bool:
        return bool(self.missing or self.stale)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "users_seen": self.users_seen,
            "desired": self.desired,
            "previous": self.previous,
            "missing": self.missing,
            "stale": self.stale,
            "needs_reconcile": self.needs_reconcile,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class _BinaryVdfReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    def _byte(self) -> int:
        if self.index >= len(self.data):
            raise ValueError("Unexpected end of shortcuts.vdf")
        value = self.data[self.index]
        self.index += 1
        return value

    def _cstring(self) -> str:
        end = self.data.find(b"\x00", self.index)
        if end == -1:
            raise ValueError("Unterminated string in shortcuts.vdf")
        value = self.data[self.index:end].decode("utf-8", errors="replace")
        self.index = end + 1
        return value

    def _int32(self) -> int:
        if self.index + 4 > len(self.data):
            raise ValueError("Unexpected end of integer in shortcuts.vdf")
        value = int.from_bytes(self.data[self.index:self.index + 4], "little", signed=False)
        self.index += 4
        return value

    def read_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            item_type = self._byte()
            if item_type == 0x08:
                return result
            key = self._cstring()
            if item_type == 0x00:
                result[key] = self.read_object()
            elif item_type == 0x01:
                result[key] = self._cstring()
            elif item_type == 0x02:
                result[key] = self._int32()
            else:
                raise ValueError(f"Unsupported shortcuts.vdf field type: {item_type}")


def _write_cstring(buffer: bytearray, value: str) -> None:
    buffer.extend(str(value).encode("utf-8", errors="replace"))
    buffer.append(0)


def _write_object(buffer: bytearray, key: str, value: dict[str, Any]) -> None:
    buffer.append(0x00)
    _write_cstring(buffer, key)
    for child_key, child_value in value.items():
        if isinstance(child_value, dict):
            _write_object(buffer, child_key, child_value)
        elif isinstance(child_value, int):
            buffer.append(0x02)
            _write_cstring(buffer, child_key)
            buffer.extend((child_value & 0xFFFFFFFF).to_bytes(4, "little", signed=False))
        else:
            buffer.append(0x01)
            _write_cstring(buffer, child_key)
            _write_cstring(buffer, str(child_value))
    buffer.append(0x08)


def read_shortcuts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    reader = _BinaryVdfReader(path.read_bytes())
    item_type = reader._byte()
    if item_type != 0x00:
        raise ValueError("shortcuts.vdf did not start with a root object")
    root_key = reader._cstring()
    if root_key != "shortcuts":
        raise ValueError("shortcuts.vdf root was not 'shortcuts'")
    root = reader.read_object()
    shortcuts: list[dict[str, Any]] = []
    for key in sorted(root, key=lambda value: int(value) if value.isdigit() else value):
        value = root[key]
        if isinstance(value, dict):
            shortcuts.append(value)
    return shortcuts


def write_shortcuts(path: Path, shortcuts: list[dict[str, Any]]) -> None:
    root = {str(index): shortcut for index, shortcut in enumerate(shortcuts)}
    buffer = bytearray()
    _write_object(buffer, "shortcuts", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(bytes(buffer))
    os.replace(temporary, path)


def _shortcut_appid(app_name: str, exe: str) -> int:
    checksum = binascii.crc32((exe + app_name).encode("utf-8")) & 0xFFFFFFFF
    return checksum | 0x80000000


def _tags(shortcut: dict[str, Any]) -> list[str]:
    tags = shortcut.get("tags")
    if not isinstance(tags, dict):
        return []
    result: list[str] = []
    for key in sorted(tags, key=lambda value: int(value) if str(value).isdigit() else str(value)):
        value = tags[key]
        if isinstance(value, str):
            result.append(value)
    return result


def _owned(shortcut: dict[str, Any]) -> bool:
    return OWNED_TAG in _tags(shortcut)


def _manual_entries(config: AppConfig) -> list[dict[str, Any]]:
    root = config.home / ".config/steam-rom-manager/userData/manualManifests/emudeck-favorites-sync"
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for manifest in sorted(root.glob("*/favorites.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    entries.append(item)
    return entries


def manual_entries(config: AppConfig) -> list[dict[str, Any]]:
    return _manual_entries(config)


def _shortcut_key_from_entry(entry: dict[str, Any]) -> tuple[str, str, str]:
    shortcut = _steam_shortcut(entry)
    return (
        str(shortcut.get("AppName") or ""),
        str(shortcut.get("Exe") or ""),
        str(shortcut.get("LaunchOptions") or ""),
    )


def _shortcut_key(shortcut: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(shortcut.get("AppName") or ""),
        str(shortcut.get("Exe") or ""),
        str(shortcut.get("LaunchOptions") or ""),
    )


def _normalize_match_text(value: object) -> str:
    return " ".join(str(value or "").replace("\\", "/").replace('"', "").casefold().split())


def _entry_matches_shortcut(entry: dict[str, Any], shortcut: dict[str, Any]) -> bool:
    entry_key = _shortcut_key_from_entry(entry)
    shortcut_key = _shortcut_key(shortcut)
    if entry_key == shortcut_key:
        return True

    title = _normalize_match_text(entry.get("title"))
    app_name = _normalize_match_text(shortcut.get("AppName"))
    if not title or title != app_name:
        return False

    expected_launch = _normalize_match_text(entry.get("launchOptions"))
    actual_launch = _normalize_match_text(shortcut.get("LaunchOptions"))
    if expected_launch and actual_launch and (expected_launch in actual_launch or actual_launch in expected_launch):
        return True

    expected_target = _normalize_match_text(entry.get("target"))
    actual_exe = _normalize_match_text(shortcut.get("Exe"))
    return bool(expected_target and actual_exe and expected_target == actual_exe and expected_launch == actual_launch)


def _entry_matches_entry(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _shortcut_key_from_entry(left) == _shortcut_key_from_entry(right):
        return True
    return (
        _normalize_match_text(left.get("title")) == _normalize_match_text(right.get("title"))
        and _normalize_match_text(left.get("launchOptions")) == _normalize_match_text(right.get("launchOptions"))
    )


def _steam_userdata_roots(config: AppConfig) -> list[Path]:
    return [
        config.home / ".local/share/Steam/userdata",
        config.home / ".steam/steam/userdata",
        config.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata",
    ]


def _steam_user_config_dirs(config: AppConfig) -> list[Path]:
    root = next((path for path in _steam_userdata_roots(config) if path.is_dir()), None)
    if root is None:
        return []
    try:
        return sorted(path / "config" for path in root.iterdir() if path.is_dir() and path.name.isdigit())
    except OSError:
        return []


def _backup(path: Path, backup_dir: Path) -> str | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / path.name
    if destination.exists():
        index = 2
        while True:
            candidate = backup_dir / f"{path.name}.{index}"
            if not candidate.exists():
                destination = candidate
                break
            index += 1
    shutil.copy2(path, destination)
    return str(destination)


def _steam_shortcut(entry: dict[str, Any]) -> dict[str, Any]:
    title = str(entry.get("title") or "")
    target = str(entry.get("target") or "")
    start_in = str(entry.get("startIn") or "")
    launch_options = str(entry.get("launchOptions") or "")
    exe = f'"{target}"' if target and not target.startswith('"') else target
    shortcut = {
        "appid": _shortcut_appid(title, exe),
        "AppName": title,
        "Exe": exe,
        "StartDir": f'"{start_in}"' if start_in and not start_in.startswith('"') else start_in,
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "LastPlayTime": 0,
        "tags": {"0": OWNED_TAG, "1": FAVORITES_TAG},
    }
    return shortcut


def import_to_steam(config: AppConfig, *, steam_running: bool | None) -> SteamImportResult:
    result = SteamImportResult(ok=False)
    if steam_running is True:
        result.diagnostics.append(Diagnostic("error", "STEAM_RUNNING", "Close Steam completely before Steam import."))
        return result

    manual_entries = _manual_entries(config)
    if not manual_entries:
        result.diagnostics.append(Diagnostic(
            "error",
            "NO_MANUAL_MANIFESTS",
            "No SRM manual manifests were found. Run SRM staging first.",
        ))
        return result

    user_dirs = _steam_user_config_dirs(config)
    result.users_seen = len(user_dirs)
    if not user_dirs:
        result.diagnostics.append(Diagnostic("error", "STEAM_USERS_NOT_FOUND", "Could not find Steam userdata/config folders."))
        return result

    new_shortcuts = [_steam_shortcut(entry) for entry in manual_entries]
    backup_dir = config.state_dir / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / "steam-shortcuts"
    for config_dir in user_dirs:
        shortcuts_file = config_dir / "shortcuts.vdf"
        try:
            existing = read_shortcuts(shortcuts_file)
        except (OSError, ValueError) as error:
            result.diagnostics.append(Diagnostic("error", "SHORTCUTS_READ_FAILED", str(error), path=str(shortcuts_file)))
            continue
        backup = _backup(shortcuts_file, backup_dir / config_dir.parent.name)
        if backup:
            result.backups.append(backup)
        merged = [shortcut for shortcut in existing if not _owned(shortcut)]
        merged.extend(new_shortcuts)
        try:
            write_shortcuts(shortcuts_file, merged)
        except OSError as error:
            result.diagnostics.append(Diagnostic("error", "SHORTCUTS_WRITE_FAILED", str(error), path=str(shortcuts_file)))
            continue
        result.shortcuts_files.append(str(shortcuts_file))
        result.users_written += 1

    result.entries_imported = len(new_shortcuts)
    result.written = result.users_written > 0
    result.ok = result.written and not any(item.severity == "error" for item in result.diagnostics)
    return result


def remove_stale_shortcuts(
    config: AppConfig,
    *,
    previous_entries: list[dict[str, Any]],
    current_entries: list[dict[str, Any]],
    steam_running: bool | None,
) -> SteamCleanupResult:
    result = SteamCleanupResult(ok=False)
    if steam_running is True:
        result.diagnostics.append(Diagnostic("error", "STEAM_RUNNING", "Close Steam completely before shortcut cleanup."))
        return result

    stale_entries = [
        entry for entry in previous_entries
        if not any(_entry_matches_entry(entry, current) for current in current_entries)
    ]
    if not stale_entries:
        result.ok = True
        return result

    user_dirs = _steam_user_config_dirs(config)
    result.users_seen = len(user_dirs)
    if not user_dirs:
        result.diagnostics.append(Diagnostic("error", "STEAM_USERS_NOT_FOUND", "Could not find Steam userdata/config folders."))
        return result

    backup_dir = config.state_dir / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / "steam-cleanup"
    for config_dir in user_dirs:
        shortcuts_file = config_dir / "shortcuts.vdf"
        try:
            existing = read_shortcuts(shortcuts_file)
        except (OSError, ValueError) as error:
            result.diagnostics.append(Diagnostic("error", "SHORTCUTS_READ_FAILED", str(error), path=str(shortcuts_file)))
            continue
        kept = [
            shortcut for shortcut in existing
            if not any(_entry_matches_shortcut(entry, shortcut) for entry in stale_entries)
        ]
        removed_here = len(existing) - len(kept)
        if removed_here == 0:
            continue
        backup = _backup(shortcuts_file, backup_dir / config_dir.parent.name)
        if backup:
            result.backups.append(backup)
        try:
            write_shortcuts(shortcuts_file, kept)
        except OSError as error:
            result.diagnostics.append(Diagnostic("error", "SHORTCUTS_WRITE_FAILED", str(error), path=str(shortcuts_file)))
            continue
        result.shortcuts_files.append(str(shortcuts_file))
        result.users_written += 1
        result.removed += removed_here

    result.ok = not any(item.severity == "error" for item in result.diagnostics)
    return result


def steam_library_status(
    config: AppConfig,
    *,
    current_entries: list[dict[str, Any]],
    previous_entries: list[dict[str, Any]],
) -> SteamLibraryStatus:
    result = SteamLibraryStatus(
        ok=False,
        desired=len(current_entries),
        previous=len(previous_entries),
    )
    user_dirs = _steam_user_config_dirs(config)
    result.users_seen = len(user_dirs)
    if not user_dirs:
        if current_entries or previous_entries:
            result.diagnostics.append(Diagnostic(
                "error",
                "STEAM_USERS_NOT_FOUND",
                "Could not find Steam userdata/config folders.",
            ))
            return result
        result.ok = True
        return result

    actual_shortcuts: list[dict[str, Any]] = []
    for config_dir in user_dirs:
        shortcuts_file = config_dir / "shortcuts.vdf"
        try:
            shortcuts = read_shortcuts(shortcuts_file)
        except (OSError, ValueError) as error:
            result.diagnostics.append(Diagnostic("error", "SHORTCUTS_READ_FAILED", str(error), path=str(shortcuts_file)))
            continue
        actual_shortcuts.extend(shortcuts)

    result.missing = [
        entry for entry in current_entries
        if not any(_entry_matches_shortcut(entry, shortcut) for shortcut in actual_shortcuts)
    ]
    stale_entries = [
        entry for entry in previous_entries
        if not any(_entry_matches_entry(entry, current) for current in current_entries)
    ]
    result.stale = [
        entry for entry in stale_entries
        if any(_entry_matches_shortcut(entry, shortcut) for shortcut in actual_shortcuts)
    ]
    result.ok = not any(item.severity == "error" for item in result.diagnostics)
    return result
