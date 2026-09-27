from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .util import read_json, write_json_atomic


@dataclass
class AppConfig:
    home: Path
    state_dir: Path
    roms_dir: Path | None
    roms_source: str = "not found"

    @property
    def srm_user_data(self) -> Path:
        return self.home / ".config/steam-rom-manager/userData"

    @property
    def settings_path(self) -> Path:
        return self.state_dir / "settings.json"


def _expand_path(value: str, home: Path) -> Path:
    value = value.replace("%HOME%", str(home)).replace("$HOME", str(home))
    return Path(os.path.expandvars(value)).expanduser()


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


def migrate_state_dir(old: Path, new: Path) -> None:
    """Move saved state from the program's old name (EmuDeck Favorites Sync) to the new one."""
    if not old.is_dir() or old.is_symlink():
        return
    try:
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            return
        for child in list(old.iterdir()):
            target = new / child.name
            if not target.exists():
                child.rename(target)
        if not any(old.iterdir()):
            old.rmdir()
    except OSError:
        pass


def load_settings(state_dir: Path) -> dict:
    data = read_json(state_dir / "settings.json", {})
    return data if isinstance(data, dict) else {}


def save_setting(config: AppConfig, key: str, value: object) -> None:
    settings = load_settings(config.state_dir)
    if value in (None, ""):
        settings.pop(key, None)
    else:
        settings[key] = value
    write_json_atomic(config.settings_path, settings)


def set_roms_dir(config: AppConfig, path: str | Path) -> None:
    resolved = Path(path).expanduser().resolve()
    save_setting(config, "roms_dir", str(resolved))
    config.roms_dir = resolved
    config.roms_source = "valgt i programmet"


def discover_config(
    roms_override: str | None = None,
    state_override: str | None = None,
    home_override: str | None = None,
) -> AppConfig:
    home = Path(home_override).expanduser() if home_override else Path.home()
    if state_override:
        state_dir = _expand_path(state_override, home)
    else:
        state_dir = home / ".local/state/srm-sync"
        migrate_state_dir(home / ".local/state/emudeck-favorites-sync", state_dir)

    candidates: list[tuple[Path, str]] = []
    if roms_override:
        candidates.append((_expand_path(roms_override, home), "command line"))
    else:
        chosen = load_settings(state_dir).get("roms_dir")
        if isinstance(chosen, str) and chosen:
            candidates.append((Path(chosen), "valgt i programmet"))
        candidates.extend(_read_emudeck_candidates(home))
        candidates.append((home / "Emulation/roms", "EmuDeck standard"))
        for media_root in (Path("/run/media/deck"), Path("/run/media")):
            if media_root.is_dir():
                try:
                    for path in sorted(media_root.glob("*/Emulation/roms")):
                        candidates.append((path, "minnekort"))
                except OSError:
                    pass

    for candidate, source in candidates:
        if candidate.is_dir():
            return AppConfig(home=home, state_dir=state_dir, roms_dir=candidate.resolve(), roms_source=source)
    if candidates and candidates[0][1] in {"command line", "valgt i programmet"}:
        # Keep an explicitly chosen folder even if it is unavailable right now
        # (for example an unmounted SD card), so the GUI can say so.
        return AppConfig(home=home, state_dir=state_dir, roms_dir=candidates[0][0], roms_source=candidates[0][1])
    return AppConfig(home=home, state_dir=state_dir, roms_dir=None)
