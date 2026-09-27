"""Run the Steam ROM Manager command line for some of our own parsers."""

from __future__ import annotations

import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig
from .srm import owned_system, parser_file
from .util import read_json, write_json_atomic


@dataclass
class SrmCliResult:
    ok: bool
    action: str = ""
    systems: list[str] = field(default_factory=list)
    app: str = ""
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def srm_override_path(config: AppConfig) -> Path:
    return config.state_dir / "srm-app-path.txt"


def set_srm_app_path(config: AppConfig, path: str) -> Path:
    expanded = Path(path).expanduser()
    config.state_dir.mkdir(parents=True, exist_ok=True)
    srm_override_path(config).write_text(str(expanded) + "\n", encoding="utf-8")
    return expanded


def _read_srm_override(config: AppConfig) -> Path | None:
    path = srm_override_path(config)
    try:
        value = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
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
        if str(candidate) not in seen:
            seen.add(str(candidate))
            unique.append(candidate)
    return unique


def srm_command(config: AppConfig) -> tuple[list[str] | None, str]:
    """The command that starts SRM (without the action), and a label for it."""
    appimage = next((path for path in srm_appimage_candidates(config) if path.is_file()), None)
    if appimage:
        return [str(appimage)], str(appimage)
    flatpak = shutil.which("flatpak")
    flatpak_installed = any(
        (root / "app/com.steamgriddb.steam-rom-manager").exists()
        for root in (Path("/var/lib/flatpak"), config.home / ".local/share/flatpak")
    )
    if flatpak and flatpak_installed:
        return [flatpak, "run", "com.steamgriddb.steam-rom-manager"], "flatpak: com.steamgriddb.steam-rom-manager"
    command = shutil.which("steam-rom-manager") or shutil.which("Steam-ROM-Manager")
    if command:
        return [command], command
    return None, ""


def _make_executable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    except OSError:
        pass


def run_srm(config: AppConfig, action: str, systems: list[str], *, timeout_seconds: int = 600) -> SrmCliResult:
    """Run ``srm <action>`` with only our parsers for ``systems`` enabled.

    Every other parser is temporarily disabled so SRM leaves its games alone, and
    the parser file is restored exactly afterwards. Steam must be closed.
    """
    result = SrmCliResult(ok=False, action=action, systems=sorted(systems))
    command, label = srm_command(config)
    if command is None:
        result.error = "Fant ikke Steam ROM Manager. Velg SRM AppImage under «SRM-oppsett»."
        return result
    result.app = label

    path = parser_file(config)
    original = read_json(path)
    if not isinstance(original, list):
        result.error = f"Kunne ikke lese SRM-parserne i {path}."
        return result
    wanted = set(systems)
    modified: list[Any] = []
    enabled = 0
    for item in original:
        if not isinstance(item, dict):
            modified.append(item)
            continue
        copy = dict(item)
        is_wanted = owned_system(copy) in wanted
        copy["disabled"] = not is_wanted
        enabled += is_wanted
        modified.append(copy)
    if enabled == 0:
        result.error = "Ingen av våre SRM-parsere passet til konsollene som skulle oppdateres."
        return result

    if command[0].casefold().endswith(".appimage"):
        _make_executable(Path(command[0]))
    result.command = [*command, action]
    try:
        write_json_atomic(path, modified)
        completed = subprocess.run(
            result.command,
            cwd=str(config.home),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        result.stdout = completed.stdout.strip()[-20000:]
        result.stderr = completed.stderr.strip()[-20000:]
        result.returncode = completed.returncode
        if completed.returncode == 0:
            result.ok = True
        else:
            result.error = f"Steam ROM Manager {action} feilet (kode {completed.returncode})."
    except subprocess.TimeoutExpired:
        result.error = f"Steam ROM Manager {action} brukte for lang tid og ble stoppet."
    except OSError as error:
        result.error = f"Kunne ikke starte Steam ROM Manager: {error}"
    finally:
        try:
            write_json_atomic(path, original)
        except OSError as error:
            result.ok = False
            result.error = f"Kunne ikke sette tilbake SRM-parserne: {error}"
    return result
