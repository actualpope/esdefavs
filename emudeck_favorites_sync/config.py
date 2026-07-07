from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .models import Diagnostic


@dataclass
class AppConfig:
    home: Path
    esde_dir: Path
    gamelists_dir: Path
    roms_dir: Path | None
    state_dir: Path
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metadata_save_mode: str = "unknown"
    roms_source: str = "not found"


def _expand_path(value: str, home: Path) -> Path:
    value = value.replace("%HOME%", str(home)).replace("$HOME", str(home))
    return Path(os.path.expandvars(value)).expanduser()


def _read_esde_settings(esde_dir: Path) -> tuple[str, str | None]:
    settings = esde_dir / "es_settings.xml"
    if not settings.is_file():
        return "unknown", None
    try:
        root = ET.parse(settings).getroot()
    except (ET.ParseError, OSError):
        return "unreadable", None
    save_mode = "unknown"
    rom_directory = None
    for element in root.iter():
        name = element.attrib.get("name")
        if name == "SaveGamelistsMode":
            save_mode = element.attrib.get("value", "unknown")
        elif name == "ROMDirectory":
            rom_directory = element.attrib.get("value") or None
    return save_mode, rom_directory


def _read_emudeck_candidates(home: Path) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    settings_files = [
        home / ".config/EmuDeck/settings.sh",
        home / "emudeck/settings.sh",
    ]
    names = {"romsPath", "roms_path", "ROMsPath", "emuPath", "emulationPath"}
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for settings in settings_files:
        if not settings.is_file():
            continue
        try:
            lines = settings.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = assignment.match(line)
            if not match or match.group(1) not in names:
                continue
            raw = match.group(2).strip().strip("'\"")
            if not raw or "$" in raw or "`" in raw:
                continue
            path = _expand_path(raw, home)
            if match.group(1).lower().startswith("roms"):
                candidates.append((path, f"{settings}:{match.group(1)}"))
            else:
                candidates.append((path / "roms", f"{settings}:{match.group(1)}"))
    return candidates


def discover_config(
    esde_override: str | None = None,
    roms_override: str | None = None,
    state_override: str | None = None,
    home_override: str | None = None,
) -> AppConfig:
    home = Path(home_override).expanduser() if home_override else Path.home()
    esde_dir = _expand_path(esde_override, home) if esde_override else home / "ES-DE"
    state_dir = (
        _expand_path(state_override, home)
        if state_override
        else home / ".local/state/emudeck-favorites-sync"
    )
    diagnostics: list[Diagnostic] = []
    save_mode, esde_rom_directory = _read_esde_settings(esde_dir)

    candidates: list[tuple[Path, str]] = []
    if roms_override:
        candidates.append((_expand_path(roms_override, home), "command line"))
    elif esde_rom_directory:
        candidates.append((_expand_path(esde_rom_directory, home), "ES-DE ROMDirectory"))
    else:
        candidates.extend(_read_emudeck_candidates(home))
        candidates.append((home / "Emulation/roms", "EmuDeck internal default"))
        media_roots = [Path("/run/media/deck"), Path("/run/media")]
        for media_root in media_roots:
            if media_root.is_dir():
                try:
                    for path in media_root.glob("*/Emulation/roms"):
                        candidates.append((path, "removable media discovery"))
                except OSError:
                    pass

    roms_dir = None
    roms_source = "not found"
    for candidate, source in candidates:
        if candidate.is_dir():
            roms_dir = candidate.resolve()
            roms_source = source
            break
    if roms_dir is None and candidates:
        roms_dir = candidates[0]
        roms_source = candidates[0][1]
        diagnostics.append(Diagnostic(
            "error", "ROMS_UNAVAILABLE",
            f"ROM directory is configured but unavailable: {roms_dir}",
            path=str(roms_dir),
        ))
    elif roms_dir is None:
        diagnostics.append(Diagnostic(
            "error", "ROMS_NOT_FOUND",
            "Could not detect the ROM directory. Use --roms-dir /path/to/Emulation/roms.",
        ))

    if not esde_dir.is_dir():
        diagnostics.append(Diagnostic(
            "error", "ESDE_NOT_FOUND", f"ES-DE directory not found: {esde_dir}", path=str(esde_dir)
        ))
    if not (esde_dir / "gamelists").is_dir():
        diagnostics.append(Diagnostic(
            "error", "GAMELISTS_NOT_FOUND",
            f"ES-DE gamelists directory not found: {esde_dir / 'gamelists'}",
            path=str(esde_dir / "gamelists"),
        ))
    if save_mode in {"on exit", "never"}:
        diagnostics.append(Diagnostic(
            "warning", "DELAYED_METADATA",
            f"ES-DE SaveGamelistsMode is '{save_mode}'; favorites may not be visible on disk immediately.",
            path=str(esde_dir / "es_settings.xml"),
        ))
    elif save_mode == "unreadable":
        diagnostics.append(Diagnostic(
            "warning", "SETTINGS_UNREADABLE", "Could not parse ES-DE es_settings.xml."
        ))

    return AppConfig(
        home=home,
        esde_dir=esde_dir,
        gamelists_dir=esde_dir / "gamelists",
        roms_dir=roms_dir,
        state_dir=state_dir,
        diagnostics=diagnostics,
        metadata_save_mode=save_mode,
        roms_source=roms_source,
    )

