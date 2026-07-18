from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import GameEntry, Manifest


SYSTEM_ALIASES: dict[str, tuple[str, ...]] = {
    "gba": ("gba", "game boy advance", "gameboy advance", "nintendo game boy advance"),
    "gb": ("gb", "game boy", "gameboy", "nintendo game boy"),
    "gbc": ("gbc", "game boy color", "gameboy color", "nintendo game boy color"),
    "gc": ("gc", "gamecube", "game cube", "nintendo gamecube", "nintendo game cube"),
    "n3ds": ("n3ds", "3ds", "nintendo 3ds"),
    "nds": ("nds", "ds", "nintendo ds"),
    "psx": ("psx", "playstation", "sony playstation"),
    "ps2": ("ps2", "playstation 2", "sony playstation 2"),
    "psp": ("psp", "playstation portable", "sony playstation portable"),
    "snes": ("snes", "super nintendo", "super nintendo entertainment system"),
    "wiiu": ("wiiu", "wii u", "nintendo wii u"),
}


@dataclass(frozen=True)
class SrmPreviewEntry:
    id: str
    system: str
    title: str
    parser_title: str
    target: str
    start_in: str
    launch_options: str
    append_args_to_executable: bool
    steam_categories: list[str]
    image_providers: list[str]
    source_rom: str
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rom_directory_matches(raw: str, system: str, roms_dir: Path | None) -> bool:
    text = raw.replace("\\", "/").rstrip("/")
    if (
        text.endswith(f"/{system}")
        or text.endswith(f"/{system}/roms")
        or text.endswith(f"${{/}}{system}")
        or text.endswith(f"${{/}}{system}${{/}}roms")
    ):
        return True
    if roms_dir is not None:
        concrete_roots = {
            str(roms_dir / system).replace("\\", "/").rstrip("/"),
            str(roms_dir / system / "roms").replace("\\", "/").rstrip("/"),
        }
        if text in concrete_roots:
            return True
    return text in {
        f"${{romsdirglobal}}/{system}",
        f"${{romsdirglobal}}/{system}/roms",
        f"${{romsdirglobal}}${{/}}{system}",
        f"${{romsdirglobal}}${{/}}{system}${{/}}roms",
    }


def _parser_inputs_text(parser: dict[str, Any]) -> str:
    inputs = parser.get("parserInputs")
    if not isinstance(inputs, dict):
        return ""
    return " ".join(str(value).casefold() for value in inputs.values())


def _parser_mentions_system(parser: dict[str, Any], system: str) -> bool:
    text = _parser_inputs_text(parser)
    return (
        f"{system}/" in text
        or f"{system}" in text.split()
        or f"{{{system}" in text
        or f",{system}" in text
    )


def _rom_directory_is_global_root(raw: str) -> bool:
    text = raw.replace("\\", "/").rstrip("/")
    return text in {"${romsdirglobal}", "${romsdirglobal}${/}"}


def _parser_score(parser: dict[str, Any], entry: GameEntry, config: AppConfig) -> int:
    score = 0
    rom_directory = str(parser.get("romDirectory", ""))
    if _rom_directory_matches(rom_directory, entry.system, config.roms_dir):
        score += 100
    elif _rom_directory_is_global_root(rom_directory) and _parser_mentions_system(parser, entry.system):
        score += 80
    categories = parser.get("steamCategories") if isinstance(parser.get("steamCategories"), list) else []
    aliases = SYSTEM_ALIASES.get(entry.system, (entry.system,))
    category_text = " ".join(str(item).casefold() for item in categories)
    title_text = str(parser.get("configTitle", "")).casefold()
    if any(alias in category_text or alias in title_text for alias in aliases):
        score += 20
    if not parser.get("disabled", False):
        score += 2
    if parser.get("parserType") == "Glob":
        score += 1
    return score


def _find_parser_candidates(
    parsers: list[dict[str, Any]], entry: GameEntry, config: AppConfig
) -> list[tuple[dict[str, Any], int]]:
    scored = [(item, _parser_score(item, entry, config)) for item in parsers]
    qualifying = [(item, score) for item, score in scored if score >= 100]
    qualifying.sort(key=lambda pair: pair[1], reverse=True)
    return qualifying


def parser_preferences_path(config: AppConfig) -> Path:
    return config.state_dir / "parser-preferences.json"


def load_parser_preferences(config: AppConfig) -> dict[str, str]:
    path = parser_preferences_path(config)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def save_parser_preference(config: AppConfig, system: str, preference: str) -> dict[str, str]:
    preferences = load_parser_preferences(config)
    if preference:
        preferences[system] = preference
    else:
        preferences.pop(system, None)
    path = parser_preferences_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(preferences, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return preferences


def select_parser_candidate(
    candidates: list[tuple[dict[str, Any], int]], preference: str | None
) -> dict[str, Any] | None:
    if not candidates:
        return None
    if preference:
        preferred = next(
            (item for item, _ in candidates if preference.casefold() in str(item.get("configTitle", "")).casefold()),
            None,
        )
        if preferred is not None:
            return preferred
    return candidates[0][0]


def _find_parser(
    parsers: list[dict[str, Any]], entry: GameEntry, config: AppConfig, preference: str | None = None
) -> dict[str, Any] | None:
    return select_parser_candidate(_find_parser_candidates(parsers, entry, config), preference)


def _replace_file_path(template: str, entry: GameEntry) -> str:
    quoted = entry.resolved_rom_path.replace('"', '\\"')
    return template.replace("${filePath}", quoted)


def _entry_from_parser(entry: GameEntry, parser: dict[str, Any]) -> SrmPreviewEntry:
    executable = parser.get("executable") if isinstance(parser.get("executable"), dict) else {}
    target = str(executable.get("path") or "")
    launch_options = _replace_file_path(str(parser.get("executableArgs") or ""), entry)
    start_in = str(parser.get("startInDirectory") or "")
    categories = parser.get("steamCategories") if isinstance(parser.get("steamCategories"), list) else []
    providers = parser.get("imageProviders") if isinstance(parser.get("imageProviders"), list) else []
    warning = ""
    if "${" in target or "${" in launch_options or "${" in start_in:
        warning = "Contains SRM variables; verify in Steam ROM Manager before real apply."
    return SrmPreviewEntry(
        id=entry.id,
        system=entry.system,
        title=entry.title,
        parser_title=str(parser.get("configTitle") or ""),
        target=target,
        start_in=start_in,
        launch_options=launch_options,
        append_args_to_executable=bool(executable.get("appendArgsToExecutable", True)),
        steam_categories=[str(item) for item in categories],
        image_providers=[str(item) for item in providers],
        source_rom=entry.resolved_rom_path,
        warning=warning,
    )


def build_srm_preview(config: AppConfig, manifest: Manifest) -> dict[str, Any]:
    parser_file = config.home / ".config/steam-rom-manager/userData/userConfigurations.json"
    raw_parsers = _read_json(parser_file)
    parsers = raw_parsers if isinstance(raw_parsers, list) else []
    preferences = load_parser_preferences(config)
    entries: list[SrmPreviewEntry] = []
    unmatched: list[dict[str, str]] = []
    for entry in manifest.entries:
        parser = _find_parser(
            [item for item in parsers if isinstance(item, dict)], entry, config, preferences.get(entry.system)
        )
        if parser is None:
            unmatched.append({
                "id": entry.id,
                "system": entry.system,
                "title": entry.title,
                "source_rom": entry.resolved_rom_path,
                "reason": "No matching SRM parser found for this system ROM directory.",
            })
            continue
        entries.append(_entry_from_parser(entry, parser))
    systems = sorted({entry.system for entry in manifest.entries})
    return {
        "schema_version": 1,
        "mode": "preview_only",
        "safe_to_apply": False,
        "parser_file": str(parser_file),
        "systems": systems,
        "entries": [entry.to_dict() for entry in entries],
        "unmatched": unmatched,
        "notes": [
            "This file is generated for inspection only.",
            "It does not modify Steam, Steam ROM Manager, EmuDeck, or ES-DE.",
            "Real apply will remain blocked until this preview is validated.",
        ],
    }
