from __future__ import annotations

import copy
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import Diagnostic, Manifest
from .srm_preview import _find_parser_candidates
from .steam_shortcuts import manual_entries


OWNED_PARSER_PREFIX = "emudeck-favorites-sync:"
OWNED_TITLE_PREFIX = "ES-DE Favorites Sync"
FAVORITES_COLLECTION = "ES-DE Favorites"


@dataclass
class ApplyResult:
    dry_run: bool
    ok: bool
    written: bool = False
    manual_manifest_root: str = ""
    user_configurations: str = ""
    backups: list[str] = field(default_factory=list)
    parsers_written: list[str] = field(default_factory=list)
    entries_written: int = 0
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "ok": self.ok,
            "written": self.written,
            "manual_manifest_root": self.manual_manifest_root,
            "user_configurations": self.user_configurations,
            "backups": self.backups,
            "parsers_written": self.parsers_written,
            "entries_written": self.entries_written,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        if text.startswith("${", index):
            depth += 1
            index += 2
            continue
        if text[index] == "}" and depth:
            depth -= 1
        elif text[index] == "|" and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def _find_variable_end(text: str, start: int) -> int:
    depth = 1
    index = start + 2
    while index < len(text):
        if text.startswith("${", index):
            depth += 1
            index += 2
            continue
        if text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _resolve_srm_text(text: str, environment: dict[str, str]) -> str:
    aliases = {
        "/": "/",
        "retroarchpath": environment.get("retroarchPath", ""),
        "racores": environment.get("raCoresDirectory", ""),
        "romsdirglobal": environment.get("romsDirectory", ""),
        "steamdirglobal": environment.get("steamDirectory", ""),
        "localimagesdir": environment.get("localImagesDirectory", ""),
    }

    def resolve_expression(expression: str) -> str:
        lowered = expression.casefold()
        if lowered.startswith("os:"):
            parts = _split_top_level(expression)
            platform = parts[0][3:].casefold()
            value = parts[1] if platform == "linux" and len(parts) > 1 else parts[2] if len(parts) > 2 else ""
            return resolve_text(value)
        return aliases.get(lowered, environment.get(expression, ""))

    def resolve_text(value: str) -> str:
        output: list[str] = []
        index = 0
        while index < len(value):
            if value.startswith("${", index):
                end = _find_variable_end(value, index)
                if end == -1:
                    output.append(value[index:])
                    break
                output.append(resolve_expression(value[index + 2:end]))
                index = end + 1
            else:
                output.append(value[index])
                index += 1
        return "".join(output)

    previous = text
    for _ in range(8):
        current = resolve_text(previous)
        if current == previous:
            return current
        previous = current
    return previous


def _settings_environment(settings: dict[str, Any]) -> dict[str, str]:
    env = settings.get("environmentVariables") if isinstance(settings.get("environmentVariables"), dict) else {}
    return {str(key): str(value) for key, value in env.items() if value is not None}


def _delete_disabled_shortcuts(settings: dict[str, Any]) -> bool | None:
    preview = settings.get("previewSettings") if isinstance(settings.get("previewSettings"), dict) else {}
    value = preview.get("deleteDisabledShortcuts")
    return value if isinstance(value, bool) else None


def _manual_entry(entry: Any, parser: dict[str, Any], environment: dict[str, str]) -> dict[str, Any]:
    executable = parser.get("executable") if isinstance(parser.get("executable"), dict) else {}
    target = _resolve_srm_text(str(executable.get("path") or ""), environment)
    args = str(parser.get("executableArgs") or "").replace("${filePath}", entry.resolved_rom_path.replace('"', '\\"'))
    return {
        "title": entry.title,
        "target": target,
        "startIn": _resolve_srm_text(str(parser.get("startInDirectory") or ""), environment),
        "launchOptions": _resolve_srm_text(args, environment),
        "appendArgsToExecutable": bool(executable.get("appendArgsToExecutable", True)),
    }


def _steam_categories_with_favorites(categories: Any) -> list[str]:
    cleaned = [FAVORITES_COLLECTION]
    if not isinstance(categories, list):
        return cleaned
    for item in categories:
        value = str(item)
        if value.casefold() == FAVORITES_COLLECTION.casefold():
            continue
        cleaned.append(value)
    return list(dict.fromkeys(cleaned))


def _manual_parser_from_source(source: dict[str, Any], system: str, manual_dir: Path) -> dict[str, Any]:
    parser = copy.deepcopy(source)
    category = ""
    categories = _steam_categories_with_favorites(source.get("steamCategories"))
    console_categories = [item for item in categories if item.casefold() != FAVORITES_COLLECTION.casefold()]
    if console_categories:
        category = str(console_categories[0])
    parser["parserId"] = f"{OWNED_PARSER_PREFIX}{system}"
    parser["configTitle"] = f"{OWNED_TITLE_PREFIX} - {category or system}"
    parser["parserType"] = "Manual"
    parser["disabled"] = False
    parser["parserInputs"] = {"manualManifests": str(manual_dir)}
    parser["romDirectory"] = ""
    parser["executableArgs"] = ""
    parser["startInDirectory"] = ""
    parser["titleModifier"] = "${fuzzyTitle}"
    parser["steamCategories"] = categories
    return parser


def _owned_system(parser: dict[str, Any]) -> str | None:
    parser_id = str(parser.get("parserId", ""))
    if not parser_id.startswith(OWNED_PARSER_PREFIX):
        return None
    system = parser_id[len(OWNED_PARSER_PREFIX):].strip()
    return system or None


def _preserve_owned_parser(parser: dict[str, Any], system: str, manual_dir: Path) -> dict[str, Any]:
    preserved = copy.deepcopy(parser)
    preserved["parserId"] = f"{OWNED_PARSER_PREFIX}{system}"
    preserved["parserType"] = "Manual"
    preserved["disabled"] = False
    preserved["parserInputs"] = {"manualManifests": str(manual_dir)}
    preserved["romDirectory"] = ""
    preserved["executableArgs"] = ""
    preserved["startInDirectory"] = ""
    preserved["titleModifier"] = "${fuzzyTitle}"
    preserved["steamCategories"] = _steam_categories_with_favorites(preserved.get("steamCategories"))
    return preserved


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
    if path.is_dir():
        shutil.copytree(path, destination)
    else:
        shutil.copy2(path, destination)
    return str(destination)


def stage_apply(
    config: AppConfig,
    manifest: Manifest,
    *,
    dry_run: bool,
    steam_running: bool | None,
) -> ApplyResult:
    srm_user_data = config.home / ".config/steam-rom-manager/userData"
    parser_file = srm_user_data / "userConfigurations.json"
    settings_file = srm_user_data / "userSettings.json"
    manual_root = srm_user_data / "manualManifests/emudeck-favorites-sync"
    result = ApplyResult(
        dry_run=dry_run,
        ok=False,
        manual_manifest_root=str(manual_root),
        user_configurations=str(parser_file),
    )

    if manifest.scan_health.get("errors"):
        result.diagnostics.append(Diagnostic("error", "SCAN_NOT_SAFE", "Latest scan is not safe enough to apply."))
    elif not manifest.scan_health.get("removal_safe"):
        result.diagnostics.append(Diagnostic(
            "warning",
            "REMOVAL_NOT_SAFE",
            "Latest scan is safe for staging valid favorites, but removals are not safe. Missing or unsafe favorites were skipped.",
        ))
    if steam_running is True and not dry_run:
        result.diagnostics.append(Diagnostic("error", "STEAM_RUNNING", "Close Steam completely before apply --confirm."))
    if not parser_file.is_file():
        result.diagnostics.append(Diagnostic("error", "SRM_CONFIG_NOT_FOUND", f"Missing {parser_file}", path=str(parser_file)))
    if not settings_file.is_file():
        result.diagnostics.append(Diagnostic("error", "SRM_SETTINGS_NOT_FOUND", f"Missing {settings_file}", path=str(settings_file)))
    if any(item.severity == "error" for item in result.diagnostics):
        return result

    try:
        parsers = _read_json(parser_file)
        settings = _read_json(settings_file)
    except (OSError, ValueError, TypeError) as error:
        result.diagnostics.append(Diagnostic("error", "SRM_READ_FAILED", str(error)))
        return result
    if not isinstance(parsers, list) or not isinstance(settings, dict):
        result.diagnostics.append(Diagnostic("error", "SRM_SCHEMA_UNEXPECTED", "SRM config/settings shape was not expected."))
        return result
    if _delete_disabled_shortcuts(settings) is True:
        result.diagnostics.append(Diagnostic(
            "error", "DELETE_DISABLED_SHORTCUTS_ENABLED",
            "SRM setting deleteDisabledShortcuts is enabled; apply is blocked.",
        ))
        return result

    environment = _settings_environment(settings)
    raw_parsers = [item for item in parsers if isinstance(item, dict)]
    grouped_entries: dict[str, list[dict[str, Any]]] = {}
    grouped_parsers: dict[str, dict[str, Any]] = {}
    ambiguous_systems_warned: set[str] = set()
    for entry in manifest.entries:
        candidates = _find_parser_candidates(raw_parsers, entry, config)
        if not candidates:
            result.diagnostics.append(Diagnostic(
                "error", "NO_MATCHING_SRM_PARSER",
                f"No matching SRM parser found for {entry.system} / {entry.title}.",
                entry.system,
                entry.resolved_rom_path,
            ))
            continue
        source = candidates[0][0]
        if len(candidates) > 1 and candidates[0][1] == candidates[1][1] and entry.system not in ambiguous_systems_warned:
            ambiguous_systems_warned.add(entry.system)
            competing = ", ".join(
                f"'{item.get('configTitle') or item.get('parserId') or '?'}'" for item, score in candidates
                if score == candidates[0][1]
            )
            result.diagnostics.append(Diagnostic(
                "warning", "AMBIGUOUS_SRM_PARSER",
                f"Multiple SRM parsers match {entry.system} equally well ({competing}); "
                f"used '{source.get('configTitle')}'. If this is the wrong emulator, disable the unwanted "
                "parser(s) in Steam ROM Manager and run 'Oppdater ES-DE favoritter' again.",
                entry.system,
            ))
        manual_entry = _manual_entry(entry, source, environment)
        source_target = str((source.get("executable") or {}).get("path") or "")
        if source_target.strip() and not manual_entry["target"].strip():
            result.diagnostics.append(Diagnostic(
                "warning", "UNRESOLVED_SRM_VARIABLE",
                f"{entry.system} / {entry.title}: the executable path {source_target!r} resolved to empty; "
                "the emulator will not launch. Check Steam ROM Manager's global/environment variables for "
                "this system's parser.",
                entry.system, entry.resolved_rom_path,
            ))
        grouped_parsers[entry.system] = source
        grouped_entries.setdefault(entry.system, []).append(manual_entry)
    if any(item.severity == "error" for item in result.diagnostics):
        return result

    result.entries_written = sum(len(items) for items in grouped_entries.values())
    result.parsers_written = [f"{OWNED_PARSER_PREFIX}{system}" for system in sorted(grouped_entries)]
    result.ok = True
    if dry_run:
        return result

    backup_dir = config.state_dir / "backups" / _timestamp()
    for path in (parser_file, settings_file, manual_root):
        backup_path = _backup(path, backup_dir)
        if backup_path:
            result.backups.append(backup_path)

    if manual_root.exists():
        shutil.rmtree(manual_root)
    existing_owned = {
        system: item
        for item in raw_parsers
        if isinstance(item, dict)
        for system in [_owned_system(item)]
        if system
    }
    systems_to_write = sorted(set(grouped_entries) | set(existing_owned))
    for system in systems_to_write:
        entries = grouped_entries.get(system, [])
        system_dir = manual_root / system
        system_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(system_dir / "favorites.json", entries)

    remaining = [
        item for item in raw_parsers
        if not str(item.get("parserId", "")).startswith(OWNED_PARSER_PREFIX)
    ]
    owned = [
        _manual_parser_from_source(grouped_parsers[system], system, manual_root / system)
        for system in sorted(grouped_entries)
    ]
    owned.extend(
        _preserve_owned_parser(existing_owned[system], system, manual_root / system)
        for system in sorted(set(existing_owned) - set(grouped_entries))
    )
    _write_json_atomic(parser_file, [*remaining, *owned])
    result.written = True
    return result


@dataclass
class PurgeResult:
    ok: bool
    dry_run: bool
    parsers_found: list[str] = field(default_factory=list)
    entries_found: int = 0
    backups: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "parsers_found": self.parsers_found,
            "entries_found": self.entries_found,
            "backups": self.backups,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def purge_owned_parsers(config: AppConfig, *, dry_run: bool) -> PurgeResult:
    srm_user_data = config.home / ".config/steam-rom-manager/userData"
    parser_file = srm_user_data / "userConfigurations.json"
    manual_root = srm_user_data / "manualManifests/emudeck-favorites-sync"
    result = PurgeResult(ok=False, dry_run=dry_run, entries_found=len(manual_entries(config)))

    if not parser_file.is_file():
        result.ok = True
        return result
    try:
        parsers = _read_json(parser_file)
    except (OSError, ValueError, TypeError) as error:
        result.diagnostics.append(Diagnostic("error", "SRM_READ_FAILED", str(error)))
        return result
    if not isinstance(parsers, list):
        result.diagnostics.append(Diagnostic("error", "SRM_SCHEMA_UNEXPECTED", "SRM parser config was not a list."))
        return result

    owned = [
        item for item in parsers
        if isinstance(item, dict) and str(item.get("parserId", "")).startswith(OWNED_PARSER_PREFIX)
    ]
    result.parsers_found = [str(item.get("parserId")) for item in owned]
    result.ok = True
    if dry_run or not owned:
        return result

    backup_dir = config.state_dir / "backups" / _timestamp()
    for path in (parser_file, manual_root):
        backup_path = _backup(path, backup_dir)
        if backup_path:
            result.backups.append(backup_path)

    remaining = [
        item for item in parsers
        if not (isinstance(item, dict) and str(item.get("parserId", "")).startswith(OWNED_PARSER_PREFIX))
    ]
    _write_json_atomic(parser_file, remaining)
    if manual_root.exists():
        shutil.rmtree(manual_root)
    return result


def describe_parser_matches(config: AppConfig, manifest: Manifest) -> list[dict[str, Any]]:
    """Per-system breakdown of which SRM parser a favorite would use and why, for troubleshooting."""
    srm_user_data = config.home / ".config/steam-rom-manager/userData"
    parser_file = srm_user_data / "userConfigurations.json"
    settings_file = srm_user_data / "userSettings.json"
    results: list[dict[str, Any]] = []

    try:
        parsers = _read_json(parser_file) if parser_file.is_file() else []
    except (OSError, ValueError, TypeError):
        parsers = []
    raw_parsers = [item for item in parsers if isinstance(item, dict)] if isinstance(parsers, list) else []

    environment: dict[str, str] = {}
    try:
        settings = _read_json(settings_file) if settings_file.is_file() else {}
    except (OSError, ValueError, TypeError):
        settings = {}
    if isinstance(settings, dict):
        environment = _settings_environment(settings)

    seen_systems: set[str] = set()
    for entry in manifest.entries:
        if entry.system in seen_systems:
            continue
        seen_systems.add(entry.system)
        candidates = _find_parser_candidates(raw_parsers, entry, config)
        if not candidates:
            results.append({
                "system": entry.system, "example_title": entry.title,
                "matched_parser": None, "resolved_target": None, "resolved_launch_options": None,
                "unresolved_target": False, "competing_parsers": [],
            })
            continue
        source = candidates[0][0]
        manual_entry = _manual_entry(entry, source, environment)
        source_target = str((source.get("executable") or {}).get("path") or "")
        competing = (
            [str(item.get("configTitle") or item.get("parserId")) for item, score in candidates if score == candidates[0][1]]
            if len(candidates) > 1 and candidates[0][1] == candidates[1][1] else []
        )
        results.append({
            "system": entry.system,
            "example_title": entry.title,
            "matched_parser": str(source.get("configTitle") or source.get("parserId") or "?"),
            "parser_disabled": bool(source.get("disabled", False)),
            "resolved_target": manual_entry["target"],
            "resolved_launch_options": manual_entry["launchOptions"],
            "unresolved_target": bool(source_target.strip() and not manual_entry["target"].strip()),
            "competing_parsers": competing,
        })
    return results
