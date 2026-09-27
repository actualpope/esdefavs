"""Everything that reads or writes Steam ROM Manager's own configuration.

Our games live in one *Manual* SRM parser per console (parserId
``emudeck-favorites-sync:<system>``). Each parser reads a manifest file that we
write, so SRM itself never scans ROM folders for these games. The launch command
for a game is taken from the console's normal EmuDeck/SRM parser, resolved to
concrete paths, and stored in the manifest.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .config import AppConfig
from .util import read_json, write_json_atomic


# Internal ids from when the program was called "EmuDeck Favorites Sync". SRM keeps
# track of the games it added per parserId, so these must never change.
OWNED_PARSER_PREFIX = "emudeck-favorites-sync:"
OWNED_TITLE_PREFIX = "SRM Sync"
OLD_TITLE_PREFIXES = ("ES-DE Favorites Sync",)
FAVORITES_COLLECTION = "ES-DE Favorites"

# Consoles whose standard EmuDeck emulator does not understand .m3u playlists
# (PCSX2/pcsx2#7640, #6696). Multi-disc games there are listed disc by disc.
M3U_UNSUPPORTED_SYSTEMS = {"ps2"}

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


class SrmError(Exception):
    """A problem with the SRM setup that stops an operation."""


# --------------------------------------------------------------------------- paths


def parser_file(config: AppConfig) -> Path:
    return config.srm_user_data / "userConfigurations.json"


def settings_file(config: AppConfig) -> Path:
    return config.srm_user_data / "userSettings.json"


def manual_root(config: AppConfig) -> Path:
    return config.srm_user_data / "manualManifests/emudeck-favorites-sync"


def manifest_path(config: AppConfig, system: str) -> Path:
    return manual_root(config) / system / "favorites.json"


# ----------------------------------------------------------------- SRM variables


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


def resolve_srm_text(text: str, environment: dict[str, str]) -> str:
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


def settings_environment(settings: dict[str, Any]) -> dict[str, str]:
    env = settings.get("environmentVariables") if isinstance(settings.get("environmentVariables"), dict) else {}
    return {str(key): str(value) for key, value in env.items() if value is not None}


def _preview_setting(settings: dict[str, Any], key: str) -> bool | None:
    preview = settings.get("previewSettings") if isinstance(settings.get("previewSettings"), dict) else {}
    value = preview.get(key)
    return value if isinstance(value, bool) else None


# ------------------------------------------------------------------- SRM files


@dataclass
class SrmData:
    parsers: list[Any]
    settings: dict[str, Any]

    @property
    def dict_parsers(self) -> list[dict[str, Any]]:
        return [item for item in self.parsers if isinstance(item, dict)]

    @property
    def environment(self) -> dict[str, str]:
        return settings_environment(self.settings)

    @property
    def delete_disabled_shortcuts(self) -> bool | None:
        return _preview_setting(self.settings, "deleteDisabledShortcuts")

    @property
    def retrieve_current_steam_images(self) -> bool | None:
        return _preview_setting(self.settings, "retrieveCurrentSteamImages")

    def owned_parsers(self) -> dict[str, dict[str, Any]]:
        return {system: item for item in self.dict_parsers if (system := owned_system(item))}


def load_srm(config: AppConfig) -> SrmData:
    parsers_path = parser_file(config)
    settings_path = settings_file(config)
    if not parsers_path.is_file():
        raise SrmError(f"Fant ikke SRM-oppsettet: {parsers_path}. Er Steam ROM Manager satt opp via EmuDeck?")
    parsers = read_json(parsers_path)
    if not isinstance(parsers, list):
        raise SrmError(f"Kunne ikke lese SRM-parserne i {parsers_path}.")
    settings = read_json(settings_path, {}) if settings_path.is_file() else {}
    if not isinstance(settings, dict):
        raise SrmError(f"Kunne ikke lese SRM-innstillingene i {settings_path}.")
    return SrmData(parsers=parsers, settings=settings)


def set_preview_setting(config: AppConfig, key: str, value: bool) -> None:
    """Change one of SRM's preview settings, keeping a backup of the file."""
    from .util import backup, timestamp

    path = settings_file(config)
    settings = read_json(path, {}) if path.is_file() else {}
    if not isinstance(settings, dict):
        raise SrmError(f"Kunne ikke lese SRM-innstillingene i {path}.")
    backup(path, config.state_dir / "backups" / timestamp() / "srm")
    preview = settings.get("previewSettings") if isinstance(settings.get("previewSettings"), dict) else {}
    preview[key] = value
    settings["previewSettings"] = preview
    write_json_atomic(path, settings)


def load_srm_or_none(config: AppConfig) -> SrmData | None:
    try:
        return load_srm(config)
    except SrmError:
        return None


# ----------------------------------------------------------- parser matching


def owned_system(parser: dict[str, Any]) -> str | None:
    parser_id = str(parser.get("parserId", ""))
    if not parser_id.startswith(OWNED_PARSER_PREFIX):
        return None
    system = parser_id[len(OWNED_PARSER_PREFIX):].strip()
    return system or None


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
    # Globs look like "gc/**/${title}@(...)", "${/}gc${/}**${/}..." or "@(gc|wii)/...".
    text = _parser_inputs_text(parser).replace("${/}", "/")
    return system.casefold() in set(re.split(r"[^a-z0-9_+\-]+", text))


def _rom_directory_is_global_root(raw: str, roms_dir: Path | None = None) -> bool:
    text = raw.replace("\\", "/").rstrip("/")
    if roms_dir is not None and text == str(roms_dir).replace("\\", "/").rstrip("/"):
        return True
    return text.casefold() in {"${romsdirglobal}", "${romsdirglobal}${/}"}


def parser_score(parser: dict[str, Any], system: str, roms_dir: Path | None) -> int:
    score = 0
    rom_directory = str(parser.get("romDirectory", ""))
    if _rom_directory_matches(rom_directory, system, roms_dir):
        score += 100
    elif _rom_directory_is_global_root(rom_directory, roms_dir) and _parser_mentions_system(parser, system):
        score += 100
    categories = parser.get("steamCategories") if isinstance(parser.get("steamCategories"), list) else []
    aliases = SYSTEM_ALIASES.get(system, (system,))
    category_text = " ".join(str(item).casefold() for item in categories)
    title_text = str(parser.get("configTitle", "")).casefold()
    if any(alias in category_text or alias in title_text for alias in aliases):
        score += 20
    if not parser.get("disabled", False):
        score += 2
    if parser.get("parserType") == "Glob":
        score += 1
    return score


def parser_candidates(parsers: list[dict[str, Any]], system: str, roms_dir: Path | None) -> list[tuple[dict[str, Any], int]]:
    """Source parsers (EmuDeck's own, never ours) that fit a console's ROM folder, best first."""
    scored = [
        (item, parser_score(item, system, roms_dir))
        for item in parsers
        if owned_system(item) is None
    ]
    qualifying = [(item, score) for item, score in scored if score >= 100]
    qualifying.sort(key=lambda pair: pair[1], reverse=True)
    return qualifying


def parser_preferences_path(config: AppConfig) -> Path:
    return config.state_dir / "parser-preferences.json"


def load_parser_preferences(config: AppConfig) -> dict[str, str]:
    data = read_json(parser_preferences_path(config), {})
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def save_parser_preference(config: AppConfig, system: str, preference: str) -> dict[str, str]:
    preferences = load_parser_preferences(config)
    if preference:
        preferences[system] = preference
    else:
        preferences.pop(system, None)
    write_json_atomic(parser_preferences_path(config), preferences)
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


def source_parser(config: AppConfig, srm: SrmData, system: str, preferences: dict[str, str] | None = None) -> dict[str, Any] | None:
    prefs = load_parser_preferences(config) if preferences is None else preferences
    return select_parser_candidate(parser_candidates(srm.dict_parsers, system, config.roms_dir), prefs.get(system))


# ------------------------------------------------------------ ROM extensions

_GLOB_EXTENSIONS_RE = re.compile(r"\$\{title\}(?:@\(([^)]*)\)|(\.[A-Za-z0-9_+\-]+))")


def parser_extensions(parser: dict[str, Any]) -> set[str]:
    """File extensions a Glob parser picks up, lower-cased and with a leading dot."""
    text = " ".join(str(value) for value in (parser.get("parserInputs") or {}).values()) if isinstance(
        parser.get("parserInputs"), dict
    ) else ""
    extensions: set[str] = set()
    for group, single in _GLOB_EXTENSIONS_RE.findall(text):
        values = group.split("|") if group else [single]
        for value in values:
            value = value.strip()
            if value.startswith(".") and len(value) > 1 and "*" not in value:
                extensions.add(value.casefold())
    return extensions


def system_extensions(config: AppConfig, srm: SrmData | None, system: str) -> set[str]:
    if srm is None:
        return set()
    extensions: set[str] = set()
    for parser, _ in parser_candidates(srm.dict_parsers, system, config.roms_dir):
        extensions |= parser_extensions(parser)
    return extensions


# ------------------------------------------------------------- manifest entries


@dataclass
class EntryResult:
    entry: dict[str, Any] | None
    parser_title: str = ""
    problem: str = ""


def build_entry(
    title: str,
    launch_path: str,
    system: str,
    parser: dict[str, Any] | None,
    environment: dict[str, str],
) -> EntryResult:
    """The manifest entry SRM gets for one game, resolved from the console's parser."""
    if parser is None:
        return EntryResult(None, problem=f"Fant ingen SRM-parser for konsollen «{system}».")
    if system in M3U_UNSUPPORTED_SYSTEMS and launch_path.casefold().endswith(".m3u"):
        return EntryResult(None, problem=f"Emulatoren for {system} støtter ikke .m3u. Legg til diskene hver for seg.")
    executable = parser.get("executable") if isinstance(parser.get("executable"), dict) else {}
    source_target = str(executable.get("path") or "")
    target = resolve_srm_text(source_target, environment)
    path = PurePosixPath(launch_path)
    file_variables = {
        "fileName": path.name,
        "extension": path.suffix,
        "dir": str(path.parent),
    }
    args = str(parser.get("executableArgs") or "").replace("${filePath}", launch_path.replace('"', '\\"'))
    entry = {
        "title": title,
        "target": target,
        "startIn": resolve_srm_text(str(parser.get("startInDirectory") or ""), environment),
        "launchOptions": resolve_srm_text(args, {**file_variables, **environment}),
        "appendArgsToExecutable": bool(executable.get("appendArgsToExecutable", True)),
    }
    parser_title = str(parser.get("configTitle") or parser.get("parserId") or "?")
    if source_target.strip() and not target.strip():
        return EntryResult(
            None,
            parser_title=parser_title,
            problem=(
                f"Emulatorstien i SRM-parseren «{parser_title}» ble tom ({source_target}). "
                "Sjekk SRM sine miljøvariabler (f.eks. retroarchPath)."
            ),
        )
    if not target.strip():
        return EntryResult(None, parser_title=parser_title, problem=f"SRM-parseren «{parser_title}» har ingen emulator.")
    return EntryResult(entry, parser_title=parser_title)


def read_manifest(config: AppConfig, system: str) -> list[dict[str, Any]]:
    data = read_json(manifest_path(config, system), [])
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def read_all_manifests(config: AppConfig) -> dict[str, list[dict[str, Any]]]:
    root = manual_root(config)
    result: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*/favorites.json")):
        result[path.parent.name] = read_manifest(config, path.parent.name)
    return result


def write_manifest(config: AppConfig, system: str, entries: list[dict[str, Any]]) -> None:
    write_json_atomic(manifest_path(config, system), entries)


# --------------------------------------------------------------- owned parsers


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


def _owned_parser_fields(parser: dict[str, Any], system: str, manual_dir: Path) -> dict[str, Any]:
    # Only the visible name follows the program's name; parserId stays the same so
    # SRM still recognises the games it added earlier.
    title = str(parser.get("configTitle") or "")
    for old in OLD_TITLE_PREFIXES:
        if title.startswith(old):
            parser["configTitle"] = OWNED_TITLE_PREFIX + title[len(old):]
    parser["parserId"] = f"{OWNED_PARSER_PREFIX}{system}"
    parser["parserType"] = "Manual"
    parser["disabled"] = False
    parser["parserInputs"] = {"manualManifests": str(manual_dir)}
    parser["romDirectory"] = ""
    parser["executableArgs"] = ""
    parser["startInDirectory"] = ""
    parser["titleModifier"] = "${fuzzyTitle}"
    parser["steamCategories"] = _steam_categories_with_favorites(parser.get("steamCategories"))
    return parser


def new_owned_parser(source: dict[str, Any], system: str, manual_dir: Path) -> dict[str, Any]:
    parser = copy.deepcopy(source)
    categories = _steam_categories_with_favorites(source.get("steamCategories"))
    console_categories = [item for item in categories if item.casefold() != FAVORITES_COLLECTION.casefold()]
    category = str(console_categories[0]) if console_categories else ""
    parser["configTitle"] = f"{OWNED_TITLE_PREFIX} - {category or system}"
    return _owned_parser_fields(parser, system, manual_dir)


def preserved_owned_parser(parser: dict[str, Any], system: str, manual_dir: Path) -> dict[str, Any]:
    return _owned_parser_fields(copy.deepcopy(parser), system, manual_dir)


def with_owned_parsers(config: AppConfig, srm: SrmData, sources: dict[str, dict[str, Any]]) -> list[Any]:
    """SRM's parser list with an owned parser for every console in ``sources``.

    Existing owned parsers keep their name and settings; missing ones are created
    from the console's source parser. Everything else is left exactly as it was.
    """
    existing = srm.owned_parsers()
    result: list[Any] = []
    for item in srm.parsers:
        system = owned_system(item) if isinstance(item, dict) else None
        if system:
            result.append(preserved_owned_parser(item, system, manual_root(config) / system))
        else:
            result.append(item)
    for system in sorted(set(sources) - set(existing)):
        result.append(new_owned_parser(sources[system], system, manual_root(config) / system))
    return result


def write_parsers(config: AppConfig, parsers: list[Any]) -> None:
    write_json_atomic(parser_file(config), parsers)


# ------------------------------------------------------------------- overview


@dataclass
class SystemParserInfo:
    system: str
    candidates: list[str] = field(default_factory=list)
    chosen: str = ""
    preference: str = ""
    owned_title: str = ""
    target: str = ""
    problem: str = ""


def describe_systems(config: AppConfig, srm: SrmData, systems: list[str]) -> list[SystemParserInfo]:
    preferences = load_parser_preferences(config)
    owned = srm.owned_parsers()
    environment = srm.environment
    result: list[SystemParserInfo] = []
    for system in systems:
        candidates = parser_candidates(srm.dict_parsers, system, config.roms_dir)
        info = SystemParserInfo(
            system=system,
            candidates=[str(item.get("configTitle") or item.get("parserId") or "?") for item, _ in candidates],
            preference=preferences.get(system, ""),
            owned_title=str(owned[system].get("configTitle") or "") if system in owned else "",
        )
        chosen = select_parser_candidate(candidates, info.preference)
        if chosen is None:
            info.problem = "Ingen SRM-parser for denne konsollen."
        else:
            info.chosen = str(chosen.get("configTitle") or chosen.get("parserId") or "?")
            check = build_entry("test", "/test.rom", system, chosen, environment)
            if check.entry is None and check.problem and "m3u" not in check.problem:
                info.problem = check.problem
            elif check.entry is not None:
                info.target = str(check.entry["target"])
        result.append(info)
    return result
