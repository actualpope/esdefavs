"""Which games we manage in SRM, and the one-time migration from the ES-DE era."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .library import Library, RomGame, logical_id
from .srm import FAVORITES_COLLECTION, read_all_manifests
from .steam import appid, entry_command, normalize_command, read_shortcuts, shortcut_command, shortcuts_files, tags
from .util import read_json, write_json_atomic


APPLIED = "applied"
PENDING_ADD = "pending_add"
PENDING_REMOVE = "pending_remove"


@dataclass
class GameRecord:
    id: str
    system: str
    rel_path: str
    title: str
    status: str
    entry: dict[str, Any] | None = None
    appid: int | None = None

    @property
    def in_steam(self) -> bool:
        return self.status in {APPLIED, PENDING_REMOVE}

    @property
    def display_name(self) -> str:
        return self.rel_path or self.title

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameRecord":
        return cls(
            id=str(data["id"]),
            system=str(data["system"]),
            rel_path=str(data.get("rel_path") or ""),
            title=str(data.get("title") or ""),
            status=str(data.get("status") or APPLIED),
            entry=data.get("entry") if isinstance(data.get("entry"), dict) else None,
            appid=int(data["appid"]) if isinstance(data.get("appid"), int) else None,
        )


def games_path(config: AppConfig) -> Path:
    return config.state_dir / "games.json"


def save_games(config: AppConfig, records: list[GameRecord]) -> None:
    ordered = sorted(records, key=lambda item: (item.system.casefold(), item.display_name.casefold()))
    write_json_atomic(games_path(config), {"version": 1, "games": [item.to_dict() for item in ordered]})


def load_games(config: AppConfig, library: Library | None = None) -> list[GameRecord]:
    """Our games. The first time, they are imported from the existing SRM setup."""
    data = read_json(games_path(config))
    if isinstance(data, dict) and isinstance(data.get("games"), list):
        return [GameRecord.from_dict(item) for item in data["games"] if isinstance(item, dict) and "id" in item]
    records = migrate(config, library)
    save_games(config, records)
    return records


# -------------------------------------------------------------------- migration


def _applied_by_system(config: AppConfig) -> dict[str, list[dict[str, Any]]]:
    data = read_json(config.state_dir / "applied.json", {})
    entries = data.get("entries") if isinstance(data, dict) else None
    result: dict[str, list[dict[str, Any]]] = {}
    for item in entries if isinstance(entries, list) else []:
        if isinstance(item, dict) and item.get("system") and item.get("relative_rom_path"):
            result.setdefault(str(item["system"]), []).append(item)
    return result


def _steam_appids_by_command(config: AppConfig) -> dict[str, int]:
    result: dict[str, int] = {}
    tagged: set[str] = set()
    for path in shortcuts_files(config):
        try:
            shortcuts = read_shortcuts(path)
        except (OSError, ValueError):
            continue
        for shortcut in shortcuts:
            value = appid(shortcut)
            if value is None:
                continue
            command = shortcut_command(shortcut)
            is_tagged = FAVORITES_COLLECTION in tags(shortcut)
            if command not in result or (is_tagged and command not in tagged):
                result[command] = value
                if is_tagged:
                    tagged.add(command)
    return result


def _find_rel_path(
    entry: dict[str, Any],
    applied: list[dict[str, Any]],
    library_games: list[RomGame],
) -> str:
    command = entry_command(entry)
    title = str(entry.get("title") or "")
    best = ""
    best_length = 0
    for item in applied:
        rom = normalize_command(item.get("resolved_rom_path"))
        if rom and rom in command and len(rom) > best_length:
            best, best_length = str(item["relative_rom_path"]), len(rom)
    if best:
        return best
    for game in library_games:
        rom = normalize_command(game.launch_path)
        if rom and rom in command and len(rom) > best_length:
            best, best_length = game.rel_path, len(rom)
    if best:
        return best
    same_title = [item for item in applied if str(item.get("title") or "") == title]
    return str(same_title[0]["relative_rom_path"]) if len(same_title) == 1 else ""


def migrate(config: AppConfig, library: Library | None) -> list[GameRecord]:
    """Import every game that is already in our SRM parsers, exactly as it is.

    Entries are kept verbatim (same title, same launch command), so SRM and Steam
    see the same games as before and nothing is re-added or dropped. The ROM file
    is looked up only so the game can be shown and fixed later; a game whose ROM
    cannot be found is still kept.
    """
    manifests = read_all_manifests(config)
    applied = _applied_by_system(config)
    library_by_system: dict[str, list[RomGame]] = {}
    for game in library.games if library else []:
        library_by_system.setdefault(game.system, []).append(game)
    appids = _steam_appids_by_command(config)

    records: list[GameRecord] = []
    seen: set[str] = set()
    for system, entries in manifests.items():
        for entry in entries:
            rel_path = _find_rel_path(entry, applied.get(system, []), library_by_system.get(system, []))
            if rel_path:
                record_id = logical_id(system, rel_path)
            else:
                digest = hashlib.sha256(f"{system}\0{entry_command(entry)}\0{entry.get('title')}".encode("utf-8"))
                record_id = f"orphan:{digest.hexdigest()}"
            if record_id in seen:
                digest = hashlib.sha256(f"{record_id}\0{len(records)}".encode("utf-8")).hexdigest()
                record_id = f"orphan:{digest}"
                rel_path = ""
            seen.add(record_id)
            records.append(GameRecord(
                id=record_id,
                system=system,
                rel_path=rel_path,
                title=str(entry.get("title") or ""),
                status=APPLIED,
                entry=dict(entry),
                appid=appids.get(entry_command(entry)),
            ))
    return records


# ------------------------------------------------ launch settings from a sibling


def _escaped(path: str) -> str:
    return path.replace('"', '\\"')


def entry_template(records: list[GameRecord], games_by_id: dict[str, RomGame], system: str) -> tuple[dict[str, Any], str] | None:
    """A game already in Steam for this console whose launch command contains its ROM path.

    Used when no SRM parser matches the console: a new game then gets the same
    emulator and arguments, with only the ROM path swapped.
    """
    for record in records:
        if record.system != system or not record.in_steam or not record.entry or not record.rel_path:
            continue
        game = games_by_id.get(record.id)
        if game and _escaped(game.launch_path) in str(record.entry.get("launchOptions") or ""):
            return record.entry, game.launch_path
    return None


def entry_from_template(template: tuple[dict[str, Any], str], title: str, launch_path: str) -> dict[str, Any]:
    entry, old_path = template
    result = dict(entry)
    result["title"] = title
    result["launchOptions"] = str(entry.get("launchOptions") or "").replace(_escaped(old_path), _escaped(launch_path))
    return result


def pending_changes(records: list[GameRecord]) -> tuple[int, int]:
    return (
        sum(1 for r in records if r.status == PENDING_ADD),
        sum(1 for r in records if r.status == PENDING_REMOVE),
    )


def discard_changes(records: list[GameRecord]) -> None:
    """Undo every move that has not been saved yet."""
    records[:] = [r for r in records if r.status != PENDING_ADD]
    for record in records:
        if record.status == PENDING_REMOVE:
            record.status = APPLIED


# ------------------------------------------------------------ moving games


def move_to_srm(records: list[GameRecord], game: RomGame) -> GameRecord:
    for record in records:
        if record.id == game.id:
            if record.status == PENDING_REMOVE:
                record.status = APPLIED
            return record
    record = GameRecord(id=game.id, system=game.system, rel_path=game.rel_path, title=game.title, status=PENDING_ADD)
    records.append(record)
    return record


def move_out_of_srm(records: list[GameRecord], record_id: str) -> None:
    for record in list(records):
        if record.id != record_id:
            continue
        if record.status == PENDING_ADD:
            records.remove(record)
        elif record.status == APPLIED:
            record.status = PENDING_REMOVE


def undo_remove(records: list[GameRecord], record_id: str) -> None:
    for record in records:
        if record.id == record_id and record.status == PENDING_REMOVE:
            record.status = APPLIED
