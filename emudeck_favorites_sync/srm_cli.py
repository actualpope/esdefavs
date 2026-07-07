from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import Diagnostic
from .srm_apply import OWNED_PARSER_PREFIX, _write_json_atomic


@dataclass
class SrmCliResult:
    ok: bool
    attempted: bool = False
    appimage: str = ""
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "attempted": self.attempted,
            "appimage": self.appimage,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "searched": self.searched,
        }


def srm_override_path(config: AppConfig) -> Path:
    return config.state_dir / "srm-app-path.txt"


def set_srm_app_path(config: AppConfig, path: str) -> Path:
    expanded = Path(path).expanduser()
    config.state_dir.mkdir(parents=True, exist_ok=True)
    srm_override_path(config).write_text(str(expanded) + "\n", encoding="utf-8")
    return expanded


def _read_srm_override(config: AppConfig) -> Path | None:
    path = srm_override_path(config)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(value).expanduser() if value else None


def _looks_like_srm_appimage(path: Path) -> bool:
    name = path.name.casefold()
    return path.is_file() and path.suffix.casefold() == ".appimage" and "steam" in name and "rom" in name and "manager" in name


def _glob_srm_appimages(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    try:
        return sorted(path for path in directory.iterdir() if _looks_like_srm_appimage(path))
    except OSError:
        return []


def srm_appimage_candidates(config: AppConfig) -> list[Path]:
    candidates: list[Path] = []
    override = _read_srm_override(config)
    if override:
        candidates.append(override)
    if config.roms_dir:
        emulation_root = config.roms_dir.parent
        candidates.extend([
            emulation_root / "tools/Steam-ROM-Manager.AppImage",
            emulation_root / "tools/Steam ROM Manager.AppImage",
            emulation_root / "tools/Steam_ROM_Manager.AppImage",
            emulation_root / "tools/srm/Steam-ROM-Manager.AppImage",
            emulation_root / "tools/srm/Steam ROM Manager.AppImage",
        ])
        candidates.extend(_glob_srm_appimages(emulation_root / "tools"))
        candidates.extend(_glob_srm_appimages(emulation_root / "tools/srm"))
    candidates.extend([
        config.home / "Emulation/tools/srm/Steam-ROM-Manager.AppImage",
        config.home / "Emulation/tools/srm/Steam ROM Manager.AppImage",
        config.home / "Applications/Steam-ROM-Manager.AppImage",
        config.home / "Applications/Steam ROM Manager.AppImage",
        config.home / "Applications/Steam_ROM_Manager.AppImage",
        config.home / "Desktop/Steam-ROM-Manager.AppImage",
    ])
    for directory in (
        config.home / "Applications",
        config.home / "Desktop",
        config.home / "Downloads",
        config.home / "Emulation/tools/srm",
    ):
        candidates.extend(_glob_srm_appimages(directory))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def find_srm_appimage(config: AppConfig) -> Path | None:
    return next((path for path in srm_appimage_candidates(config) if path.is_file()), None)


def _srm_command(config: AppConfig, action: str) -> tuple[list[str] | None, str, list[str]]:
    candidates = srm_appimage_candidates(config)
    appimage = next((path for path in candidates if path.is_file()), None)
    if appimage:
        return [str(appimage), action], str(appimage), [str(path) for path in candidates]
    flatpak = shutil.which("flatpak")
    if flatpak:
        return [flatpak, "run", "com.steamgriddb.steam-rom-manager", action], "flatpak:com.steamgriddb.steam-rom-manager", [str(path) for path in candidates]
    command = shutil.which("steam-rom-manager") or shutil.which("Steam-ROM-Manager")
    if command:
        return [command, action], command, [str(path) for path in candidates]
    return None, "", [str(path) for path in candidates]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR)
    except OSError:
        pass


def _run_srm_owned(config: AppConfig, *, action: str, steam_running: bool | None, timeout_seconds: int = 180) -> SrmCliResult:
    result = SrmCliResult(ok=False)
    if steam_running is True:
        result.diagnostics.append(Diagnostic("error", "STEAM_RUNNING", f"Close Steam completely before SRM {action}."))
        return result

    command, app_label, searched = _srm_command(config, action)
    result.searched = searched
    if command is None:
        result.diagnostics.append(Diagnostic(
            "error",
            "SRM_APP_NOT_FOUND",
            "Could not find Steam ROM Manager. Use the GUI option 'Velg SRM AppImage' if SRM is installed somewhere else.",
        ))
        return result
    result.appimage = app_label

    parser_file = config.home / ".config/steam-rom-manager/userData/userConfigurations.json"
    if not parser_file.is_file():
        result.diagnostics.append(Diagnostic("error", "SRM_CONFIG_NOT_FOUND", f"Missing {parser_file}", path=str(parser_file)))
        return result

    try:
        original = _read_json(parser_file)
    except (OSError, ValueError, TypeError) as error:
        result.diagnostics.append(Diagnostic("error", "SRM_CONFIG_READ_FAILED", str(error), path=str(parser_file)))
        return result
    if not isinstance(original, list):
        result.diagnostics.append(Diagnostic("error", "SRM_CONFIG_SCHEMA_UNEXPECTED", "SRM parser config was not a list.", path=str(parser_file)))
        return result

    owned_count = sum(
        1 for item in original
        if isinstance(item, dict) and str(item.get("parserId", "")).startswith(OWNED_PARSER_PREFIX)
    )
    if owned_count == 0 and action == "remove":
        result.ok = True
        return result
    if owned_count == 0:
        result.diagnostics.append(Diagnostic("error", "OWNED_PARSERS_NOT_FOUND", "No ES-DE Favorites Sync parsers exist yet."))
        return result

    modified: list[Any] = []
    for item in original:
        if not isinstance(item, dict):
            modified.append(item)
            continue
        copy = dict(item)
        is_owned = str(copy.get("parserId", "")).startswith(OWNED_PARSER_PREFIX)
        copy["disabled"] = not is_owned
        modified.append(copy)

    if command and command[0].endswith(".AppImage"):
        _make_executable(Path(command[0]))
    result.command = command
    result.attempted = True
    try:
        _write_json_atomic(parser_file, modified)
        completed = subprocess.run(
            command,
            cwd=str(config.home),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        result.stdout = completed.stdout.strip()
        result.stderr = completed.stderr.strip()
        result.returncode = completed.returncode
        if completed.returncode == 0:
            result.ok = True
        else:
            result.diagnostics.append(Diagnostic(
                "error",
                f"SRM_{action.upper()}_FAILED",
                f"Steam ROM Manager {action} failed with exit code {completed.returncode}.",
            ))
    except subprocess.TimeoutExpired as error:
        result.stdout = (error.stdout or "").strip() if isinstance(error.stdout, str) else ""
        result.stderr = (error.stderr or "").strip() if isinstance(error.stderr, str) else ""
        result.diagnostics.append(Diagnostic("error", f"SRM_{action.upper()}_TIMEOUT", f"Steam ROM Manager {action} timed out."))
    except OSError as error:
        result.diagnostics.append(Diagnostic("error", f"SRM_{action.upper()}_START_FAILED", str(error)))
    finally:
        try:
            _write_json_atomic(parser_file, original)
        except OSError as error:
            result.ok = False
            result.diagnostics.append(Diagnostic("error", "SRM_CONFIG_RESTORE_FAILED", str(error), path=str(parser_file)))
    return result


def run_srm_add_owned(config: AppConfig, *, steam_running: bool | None, timeout_seconds: int = 180) -> SrmCliResult:
    return _run_srm_owned(config, action="add", steam_running=steam_running, timeout_seconds=timeout_seconds)


def run_srm_remove_owned(config: AppConfig, *, steam_running: bool | None, timeout_seconds: int = 180) -> SrmCliResult:
    return _run_srm_owned(config, action="remove", steam_running=steam_running, timeout_seconds=timeout_seconds)
