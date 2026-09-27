"""Steam's shortcuts.vdf and the Steam client process."""

from __future__ import annotations

import binascii
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig


# ----------------------------------------------------------------- shortcuts.vdf


class _BinaryVdfReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.index = 0

    def byte(self) -> int:
        if self.index >= len(self.data):
            raise ValueError("Unexpected end of shortcuts.vdf")
        value = self.data[self.index]
        self.index += 1
        return value

    def cstring(self) -> str:
        end = self.data.find(b"\x00", self.index)
        if end == -1:
            raise ValueError("Unterminated string in shortcuts.vdf")
        value = self.data[self.index:end].decode("utf-8", errors="replace")
        self.index = end + 1
        return value

    def int32(self) -> int:
        if self.index + 4 > len(self.data):
            raise ValueError("Unexpected end of integer in shortcuts.vdf")
        value = int.from_bytes(self.data[self.index:self.index + 4], "little", signed=False)
        self.index += 4
        return value

    def read_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            item_type = self.byte()
            if item_type == 0x08:
                return result
            key = self.cstring()
            if item_type == 0x00:
                result[key] = self.read_object()
            elif item_type == 0x01:
                result[key] = self.cstring()
            elif item_type == 0x02:
                result[key] = self.int32()
            else:
                raise ValueError(f"Unsupported shortcuts.vdf field type: {item_type}")


def _write_cstring(buffer: bytearray, value: str) -> None:
    buffer.extend(str(value).encode("utf-8", errors="replace"))
    buffer.append(0)


def _write_object(buffer: bytearray, key: str, value: dict[str, Any]) -> None:
    buffer.append(0x00)
    _write_cstring(buffer, key)
    for child_key, child_value in value.items():
        if isinstance(child_value, dict):
            _write_object(buffer, child_key, child_value)
        elif isinstance(child_value, bool) or isinstance(child_value, int):
            buffer.append(0x02)
            _write_cstring(buffer, child_key)
            buffer.extend((int(child_value) & 0xFFFFFFFF).to_bytes(4, "little", signed=False))
        else:
            buffer.append(0x01)
            _write_cstring(buffer, child_key)
            _write_cstring(buffer, str(child_value))
    buffer.append(0x08)


def read_shortcuts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    reader = _BinaryVdfReader(path.read_bytes())
    if reader.byte() != 0x00:
        raise ValueError("shortcuts.vdf did not start with a root object")
    if reader.cstring().casefold() != "shortcuts":
        raise ValueError("shortcuts.vdf root was not 'shortcuts'")
    root = reader.read_object()
    shortcuts: list[dict[str, Any]] = []
    for key in sorted(root, key=lambda value: int(value) if value.isdigit() else 1 << 30):
        value = root[key]
        if isinstance(value, dict):
            shortcuts.append(value)
    return shortcuts


def write_shortcuts(path: Path, shortcuts: list[dict[str, Any]]) -> None:
    root = {str(index): shortcut for index, shortcut in enumerate(shortcuts)}
    buffer = bytearray()
    _write_object(buffer, "shortcuts", root)
    buffer.append(0x08)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(bytes(buffer))
    os.replace(temporary, path)


def field(shortcut: dict[str, Any], name: str) -> Any:
    """Read a shortcuts.vdf field regardless of case (Steam writes AppName, SRM appname)."""
    if name in shortcut:
        return shortcut[name]
    lowered = name.casefold()
    for key, value in shortcut.items():
        if key.casefold() == lowered:
            return value
    return None


def set_field(shortcut: dict[str, Any], name: str, value: Any) -> None:
    lowered = name.casefold()
    for key in list(shortcut):
        if key.casefold() == lowered:
            shortcut[key] = value
            return
    shortcut[name] = value


def tags(shortcut: dict[str, Any]) -> list[str]:
    value = field(shortcut, "tags")
    if not isinstance(value, dict):
        return []
    return [item for _, item in sorted(value.items(), key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 0)
            if isinstance(item, str)]


def appid(shortcut: dict[str, Any]) -> int | None:
    value = field(shortcut, "appid")
    return value & 0xFFFFFFFF if isinstance(value, int) else None


def shortcut_appid(exe: str, app_name: str) -> int:
    """The appid Steam and SRM derive from an Exe and a name."""
    return (binascii.crc32((exe + app_name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def normalize_command(text: object) -> str:
    return " ".join(str(text or "").replace("\\", "/").replace('"', "").replace("'", "").casefold().split())


def shortcut_command(shortcut: dict[str, Any]) -> str:
    return normalize_command(f"{field(shortcut, 'Exe') or ''} {field(shortcut, 'LaunchOptions') or ''}")


def entry_command(entry: dict[str, Any]) -> str:
    return normalize_command(f"{entry.get('target') or ''} {entry.get('launchOptions') or ''}")


def _userdata_roots(config: AppConfig) -> list[Path]:
    return [
        config.home / ".local/share/Steam/userdata",
        config.home / ".steam/steam/userdata",
        config.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata",
    ]


def shortcuts_files(config: AppConfig) -> list[Path]:
    """shortcuts.vdf of every Steam user that has one (or at least a config folder)."""
    seen: set[str] = set()
    result: list[Path] = []
    for root in _userdata_roots(config):
        if not root.is_dir():
            continue
        try:
            users = sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit() and path.name != "0")
        except OSError:
            continue
        for user in users:
            config_dir = user / "config"
            if not config_dir.is_dir():
                continue
            real = os.path.realpath(config_dir)
            if real in seen:
                continue
            seen.add(real)
            result.append(config_dir / "shortcuts.vdf")
        if result:
            break
    return result


# -------------------------------------------------------------------- the client


def steam_running() -> bool:
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    try:
        children = list(proc.iterdir())
    except OSError:
        return False
    for child in children:
        if not child.name.isdigit():
            continue
        try:
            name = (child / "comm").read_text(encoding="utf-8", errors="ignore").strip().lower()
        except OSError:
            continue
        if name in {"steam", "steamwebhelper"}:
            return True
    return False


def game_mode() -> bool:
    """True inside SteamOS Game Mode, where closing Steam would end the session."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()
    if "gamescope" in desktop:
        return True
    return os.environ.get("SteamGamepadUI") == "1" and "kde" not in desktop


def shutdown_steam(wait_seconds: int = 90, progress: Callable[[str], None] | None = None) -> bool:
    if not steam_running():
        return True
    command = shutil.which("steam")
    if command:
        subprocess.Popen([command, "-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    elif shutil.which("flatpak"):
        subprocess.Popen(["flatpak", "run", "com.valvesoftware.Steam", "-shutdown"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        return False
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not steam_running():
            time.sleep(2)  # let Steam finish writing its own files
            return not steam_running()
        if progress:
            progress("Venter på at Steam skal lukkes …")
        time.sleep(1)
    return not steam_running()


def start_steam() -> bool:
    command = shutil.which("steam")
    try:
        if command:
            subprocess.Popen([command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        if shutil.which("flatpak"):
            subprocess.Popen(["flatpak", "run", "com.valvesoftware.Steam"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    except OSError:
        return False
    return False
