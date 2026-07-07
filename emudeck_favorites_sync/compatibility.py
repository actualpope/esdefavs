from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .srm_cli import find_srm_appimage, srm_appimage_candidates, srm_override_path


def _read_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.is_file():
        return result
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    except OSError:
        pass
    return result


def _find_first(candidates: list[Path]) -> Path | None:
    return next((path for path in candidates if path.exists()), None)


def _steam_running() -> bool | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    try:
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            try:
                name = (child / "comm").read_text(encoding="utf-8", errors="ignore").strip().lower()
                if name in {"steam", "steamwebhelper"}:
                    return True
            except OSError:
                continue
    except OSError:
        return None
    return False


def _safe_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, str(error)


def _parser_summary(item: dict[str, Any]) -> dict[str, Any]:
    executable = item.get("executable") if isinstance(item.get("executable"), dict) else {}
    accounts = item.get("userAccounts") if isinstance(item.get("userAccounts"), dict) else {}
    controllers = item.get("controllers") if isinstance(item.get("controllers"), dict) else {}
    return {
        "config_title": item.get("configTitle"),
        "parser_id": item.get("parserId"),
        "parser_type": item.get("parserType"),
        "version": item.get("version"),
        "disabled": item.get("disabled", False),
        "rom_directory": item.get("romDirectory"),
        "steam_directory": item.get("steamDirectory"),
        "steam_categories": item.get("steamCategories", []),
        "image_providers": item.get("imageProviders", []),
        "online_image_queries": item.get("onlineImageQueries", []),
        "steam_input_enabled": item.get("steamInputEnabled"),
        "controller_types": sorted(key for key, value in controllers.items() if value),
        "specified_accounts": accounts.get("specifiedAccounts", []),
        "executable_path": executable.get("path"),
        "append_args_to_executable": executable.get("appendArgsToExecutable"),
        "executable_args": item.get("executableArgs"),
        "start_in_directory": item.get("startInDirectory"),
        "parser_inputs": item.get("parserInputs", {}),
    }


def _steam_users(home: Path) -> tuple[str | None, list[str]]:
    roots = [
        home / ".local/share/Steam/userdata",
        home / ".steam/steam/userdata",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata",
    ]
    root = _find_first(roots)
    if root is None or not root.is_dir():
        return None, []
    try:
        users = sorted(path.name for path in root.iterdir() if path.is_dir() and path.name.isdigit())
    except OSError:
        users = []
    return str(root), users


def collect_compatibility(config: AppConfig) -> dict[str, Any]:
    srm_config = config.home / ".config/steam-rom-manager"
    user_data = srm_config / "userData"
    parser_file = user_data / "userConfigurations.json"
    settings_file = user_data / "userSettings.json"
    parsers: list[dict[str, Any]] = []
    parser_error = None
    if parser_file.is_file():
        value, parser_error = _safe_json(parser_file)
        if isinstance(value, list):
            parsers = [_parser_summary(item) for item in value if isinstance(item, dict)]
        elif parser_error is None:
            parser_error = "top-level value is not an array"

    settings_summary: dict[str, Any] = {}
    settings_error = None
    if settings_file.is_file():
        value, settings_error = _safe_json(settings_file)
        if isinstance(value, dict):
            preview = value.get("previewSettings") if isinstance(value.get("previewSettings"), dict) else {}
            settings_summary = {
                "delete_disabled_shortcuts": preview.get("deleteDisabledShortcuts"),
                "disable_categories": preview.get("disableCategories"),
                "environment_steam_directory": (
                    value.get("environmentVariables", {}).get("steamDirectory")
                    if isinstance(value.get("environmentVariables"), dict) else None
                ),
            }

    srm_appimage = find_srm_appimage(config)
    esde_appimage = _find_first([
        config.home / "Applications/ES-DE.AppImage",
        config.home / "Applications/ES-DE_x64.AppImage",
        (config.roms_dir.parent / "tools/EmulationStation-DE-x64_SteamDeck.AppImage") if config.roms_dir else Path("/__none__"),
    ])
    steam_userdata, steam_users = _steam_users(config.home)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only_probe": True,
        "os": _read_os_release(),
        "home": str(config.home),
        "esde": {
            "data_directory": str(config.esde_dir),
            "gamelists_directory": str(config.gamelists_dir),
            "appimage": str(esde_appimage) if esde_appimage else None,
            "metadata_save_mode": config.metadata_save_mode,
        },
        "emudeck": {
            "roms_directory": str(config.roms_dir) if config.roms_dir else None,
            "roms_detection_source": config.roms_source,
        },
        "srm": {
            "appimage": str(srm_appimage) if srm_appimage else None,
            "appimage_override_file": str(srm_override_path(config)),
            "appimage_candidates": [str(path) for path in srm_appimage_candidates(config)],
            "config_directory": str(srm_config),
            "user_configurations": str(parser_file),
            "user_settings": str(settings_file),
            "parser_read_error": parser_error,
            "settings_read_error": settings_error,
            "settings": settings_summary,
            "parsers": parsers,
        },
        "steam": {
            "running": _steam_running(),
            "userdata_directory": steam_userdata,
            "users": steam_users,
        },
    }
