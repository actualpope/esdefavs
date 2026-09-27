"""Scan the ROM folder: every console folder and the games in it."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import AppConfig
from .srm import M3U_UNSUPPORTED_SYSTEMS, SrmData, system_extensions


# Top-level folders in an EmuDeck ROM folder that are not consoles.
SKIP_SYSTEM_DIRS = {
    "cloud",
    "desktop",
    "downloaded_media",
    "emulators",
    "generic-applications",
    "media",
    "remoteplay",
    "tools",
}

# Used only when no SRM parser tells us which extensions a console uses.
NON_ROM_EXTENSIONS = {
    ".bak", ".bmp", ".bps", ".cfg", ".cht", ".db", ".desktop", ".gif", ".htm", ".html", ".ini",
    ".ips", ".jpeg", ".jpg", ".json", ".lnk", ".log", ".md", ".md5", ".mkv", ".mp4", ".nfo",
    ".part", ".pdf", ".png", ".rtc", ".sav", ".sfv", ".sh", ".sha1", ".srm", ".state", ".tmp",
    ".txt", ".ups", ".url", ".webp", ".xdelta", ".xml", ".yml", ".yaml",
}

PLAYLIST_EXTENSIONS = {".m3u", ".cue", ".gdi"}
MAX_DEPTH = 4


def logical_id(system: str, rel_path: str) -> str:
    """Same identity rule as earlier versions: console + path inside the console folder."""
    digest = hashlib.sha256(f"{system}\0{rel_path}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(frozen=True)
class RomGame:
    id: str
    system: str
    rel_path: str
    title: str
    launch_path: str

    @property
    def filename(self) -> str:
        return self.rel_path


@dataclass
class Library:
    roms_dir: Path | None
    games: list[RomGame] = field(default_factory=list)
    error: str = ""

    def by_id(self) -> dict[str, RomGame]:
        return {game.id: game for game in self.games}


def multi_disc_playlist_file(directory: Path) -> Path:
    # ES-DE's "directories interpreted as files" convention: a multi-disc game is a
    # directory (e.g. "Game.m3u") holding the discs plus a playlist with the same
    # name as the directory. SRM mishandles the directory path itself
    # (SteamGridDB/steam-rom-manager#386), so the game launches the inner playlist.
    candidate = directory / directory.name
    return candidate if candidate.is_file() else directory


def _playlist_references(path: Path) -> list[Path]:
    try:
        if path.stat().st_size > 1_000_000:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    suffix = path.suffix.casefold()
    names: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if suffix == ".m3u":
            names.append(line)
        elif suffix == ".cue":
            match = re.match(r'(?i)FILE\s+"([^"]+)"', line) or re.match(r"(?i)FILE\s+(\S+)", line)
            if match:
                names.append(match.group(1))
        elif suffix == ".gdi":
            quoted = re.search(r'"([^"]+)"', line)
            if quoted:
                names.append(quoted.group(1))
            else:
                parts = line.split()
                if len(parts) >= 5:
                    names.append(parts[4])
    references: list[Path] = []
    for name in names:
        name = name.replace("\\", "/")
        reference = Path(name) if name.startswith("/") else path.parent / name
        references.append(Path(os.path.normpath(reference)))
    return references


def _is_game_name(name: str, allowed: set[str] | None) -> bool:
    if name.startswith("."):
        return False
    suffix = PurePosixPath(name).suffix.casefold()
    if not suffix:
        return False
    if allowed is not None:
        return suffix in allowed
    return suffix not in NON_ROM_EXTENSIONS


def scan_system(system_root: Path, system: str, allowed: set[str] | None) -> list[RomGame]:
    if allowed is not None:
        allowed = set(allowed)
        if system in M3U_UNSUPPORTED_SYSTEMS:
            allowed.discard(".m3u")
        else:
            allowed.add(".m3u")
    candidates: list[tuple[Path, bool]] = []  # (path, is_folder_game)
    visited: set[str] = set()

    def walk(directory: Path, depth: int) -> None:
        try:
            real = os.path.realpath(directory)
        except OSError:
            return
        if real in visited:
            return
        visited.add(real)
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError:
            return
        for child in children:
            path = Path(child.path)
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if is_dir:
                if child.name.startswith("."):
                    continue
                if _is_game_name(child.name, allowed) and (
                    allowed is not None or path.suffix.casefold() == ".m3u"
                ):
                    candidates.append((path, True))
                elif depth < MAX_DEPTH:
                    walk(path, depth + 1)
            elif _is_game_name(child.name, allowed):
                candidates.append((path, False))

    walk(system_root, 0)

    hidden: set[str] = set()
    for path, is_folder in candidates:
        if not is_folder and path.suffix.casefold() in PLAYLIST_EXTENSIONS:
            hidden.update(os.path.normcase(str(item)) for item in _playlist_references(path))

    games: list[RomGame] = []
    for path, is_folder in candidates:
        if os.path.normcase(os.path.normpath(path)) in hidden:
            continue
        rel_path = path.relative_to(system_root).as_posix()
        launch = multi_disc_playlist_file(path) if is_folder else path
        title = path.stem if path.suffix else path.name
        games.append(RomGame(
            id=logical_id(system, rel_path),
            system=system,
            rel_path=rel_path,
            title=title,
            launch_path=str(launch.resolve(strict=False)),
        ))
    return games


def scan_library(config: AppConfig, srm: SrmData | None) -> Library:
    roms_dir = config.roms_dir
    library = Library(roms_dir=roms_dir)
    if roms_dir is None:
        library.error = "Fant ingen rom-mappe. Velg den med «Velg rom-mappe»."
        return library
    if not roms_dir.is_dir():
        library.error = f"Rom-mappa er ikke tilgjengelig: {roms_dir}"
        return library
    try:
        system_dirs = sorted(
            (path for path in roms_dir.iterdir() if path.is_dir()),
            key=lambda item: item.name.casefold(),
        )
    except OSError as error:
        library.error = f"Kunne ikke lese rom-mappa: {error}"
        return library
    for system_dir in system_dirs:
        system = system_dir.name
        if system.startswith(".") or system.casefold() in SKIP_SYSTEM_DIRS:
            continue
        extensions = system_extensions(config, srm, system)
        system_root = system_dir.resolve()
        library.games.extend(scan_system(system_root, system, extensions or None))
    library.games.sort(key=lambda game: (game.system.casefold(), game.rel_path.casefold()))
    return library
