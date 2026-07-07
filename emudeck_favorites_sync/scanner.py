from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import AppConfig
from .models import Diagnostic, GameEntry, Manifest, SystemHealth


_XML_DECLARATION_RE = re.compile(r"<\?xml[^?]*\?>", re.IGNORECASE)


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes"}


def _normalize_relative(source: str) -> str | None:
    text = source.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    normalized = str(path)
    return None if normalized in {"", "."} else normalized


def _logical_id(system: str, relative_path: str) -> str:
    digest = hashlib.sha256(f"{system}\0{relative_path}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False


def _parse_gamelist(gamelist: Path) -> tuple[ET.Element, bool]:
    try:
        return ET.parse(gamelist).getroot(), False
    except ET.ParseError:
        raw = gamelist.read_text(encoding="utf-8-sig")
        without_declarations = _XML_DECLARATION_RE.sub("", raw)
        wrapped = f"<__emudeck_favorites_sync_root__>{without_declarations}</__emudeck_favorites_sync_root__>"
        return ET.fromstring(wrapped), True


def _resolve_entry(
    source_path: str,
    system_root: Path,
    folder_link: str | None = None,
) -> tuple[Path | None, str | None, str | None]:
    raw = (folder_link or source_path).strip()
    expanded = Path(os.path.expanduser(raw))
    if expanded.is_absolute():
        resolved = expanded.resolve(strict=False)
        if not _within(resolved, system_root):
            return None, None, "absolute path is outside the expected system ROM root"
        relative = resolved.relative_to(system_root.resolve(strict=False)).as_posix()
        return resolved, relative, None
    relative = _normalize_relative(raw)
    if relative is None:
        return None, None, "invalid relative path or directory traversal"
    resolved = (system_root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
    if not _within(resolved, system_root):
        return None, None, "resolved path escapes the expected system ROM root"
    return resolved, relative, None


def scan(config: AppConfig) -> Manifest:
    diagnostics = list(config.diagnostics)
    entries: list[GameEntry] = []
    systems: dict[str, SystemHealth] = {}
    gamelists_seen = 0
    gamelists_ok = 0

    if not config.gamelists_dir.is_dir():
        return _build_manifest(config, entries, systems, diagnostics, gamelists_seen, gamelists_ok)

    try:
        system_dirs = sorted(
            path for path in config.gamelists_dir.iterdir()
            if path.is_dir() and path.name != "CLEANUP"
        )
    except OSError as error:
        diagnostics.append(Diagnostic("error", "GAMELISTS_READ_FAILED", str(error)))
        return _build_manifest(config, entries, systems, diagnostics, gamelists_seen, gamelists_ok)

    for system_dir in system_dirs:
        gamelist = system_dir / "gamelist.xml"
        if not gamelist.is_file():
            continue
        system = system_dir.name
        gamelists_seen += 1
        system_root = (config.roms_dir / system) if config.roms_dir else Path("/__missing_rom_root__") / system
        health = SystemHealth(system, str(gamelist), str(system_root))
        systems[system] = health
        favorite_source_problem = False
        health.storage_available = system_root.is_dir()
        if not health.storage_available:
            health.reason = "system ROM directory is unavailable"
            diagnostics.append(Diagnostic(
                "error", "SYSTEM_STORAGE_UNAVAILABLE",
                f"ROM directory is unavailable for {system}: {system_root}", system, str(system_root)
            ))
        try:
            root, recovered_fragment = _parse_gamelist(gamelist)
            health.parsed = True
            gamelists_ok += 1
            if recovered_fragment:
                diagnostics.append(Diagnostic(
                    "warning", "RECOVERED_XML_FRAGMENT",
                    "Gamelist was not a single well-formed XML document; parsed it as a safe XML fragment.",
                    system, str(gamelist)
                ))
        except ET.ParseError as error:
            health.reason = f"malformed XML: {error}"
            diagnostics.append(Diagnostic(
                "error", "MALFORMED_XML", f"Could not parse {gamelist}: {error}", system, str(gamelist)
            ))
            continue
        except OSError as error:
            health.reason = f"read error: {error}"
            diagnostics.append(Diagnostic(
                "error", "GAMELIST_READ_FAILED", f"Could not read {gamelist}: {error}", system, str(gamelist)
            ))
            continue

        for node in list(root.iter("game")) + list(root.iter("folder")):
            child_favorite = _is_true(node.findtext("favorite"))
            attribute_favorite = _is_true(node.attrib.get("favorite"))
            if attribute_favorite and not child_favorite:
                diagnostics.append(Diagnostic(
                    "warning", "LEGACY_FAVORITE_ATTRIBUTE",
                    "Favorite was stored as an attribute; accepted for compatibility.", system, str(gamelist)
                ))
            if not (child_favorite or attribute_favorite):
                continue
            health.favorites_seen += 1
            source_path = (node.findtext("path") or "").strip()
            if not source_path:
                favorite_source_problem = True
                diagnostics.append(Diagnostic(
                    "error", "MISSING_PATH", "Favorite entry has no <path>.", system, str(gamelist)
                ))
                continue
            folder_link = (node.findtext("folderlink") or "").strip() or None
            resolved, relative, error = _resolve_entry(source_path, system_root, folder_link)
            if error:
                favorite_source_problem = True
                diagnostics.append(Diagnostic(
                    "error", "UNSAFE_PATH", f"Skipped {source_path}: {error}.", system, source_path
                ))
                continue
            if not health.storage_available or resolved is None or relative is None:
                favorite_source_problem = True
                continue
            if not resolved.exists():
                favorite_source_problem = True
                diagnostics.append(Diagnostic(
                    "warning", "MISSING_ROM", f"Favorite ROM does not exist: {resolved}", system, str(resolved)
                ))
                continue
            entry_type = "folder" if node.tag == "folder" else "game"
            if resolved.is_dir() and entry_type == "game":
                entry_type = "folder-game"
            title = (node.findtext("name") or resolved.stem or resolved.name).strip()
            entries.append(GameEntry(
                id=_logical_id(system, relative),
                system=system,
                title=title,
                source_path=source_path,
                relative_rom_path=relative,
                resolved_rom_path=str(resolved),
                entry_type=entry_type,
                alternative_emulator=(node.findtext("altemulator") or "").strip(),
            ))
            health.favorites_included += 1

        health.removal_safe = health.parsed and health.storage_available and not favorite_source_problem
        if health.removal_safe:
            health.reason = "healthy"
        elif favorite_source_problem:
            health.reason = "one or more favorite entries could not be resolved safely"

    entries.sort(key=lambda item: (item.system.casefold(), item.title.casefold(), item.relative_rom_path))
    if len({entry.id for entry in entries}) != len(entries):
        diagnostics.append(Diagnostic(
            "error", "DUPLICATE_ID", "Duplicate logical IDs were generated; removals are blocked."
        ))
        for health in systems.values():
            health.removal_safe = False
            health.reason = "duplicate logical IDs"
    return _build_manifest(config, entries, systems, diagnostics, gamelists_seen, gamelists_ok)


def _build_manifest(
    config: AppConfig,
    entries: list[GameEntry],
    systems: dict[str, SystemHealth],
    diagnostics: list[Diagnostic],
    gamelists_seen: int,
    gamelists_ok: int,
) -> Manifest:
    storage_available = bool(config.roms_dir and config.roms_dir.is_dir())
    errors = sum(item.severity == "error" for item in diagnostics)
    removal_safe = bool(systems) and all(item.removal_safe for item in systems.values()) and errors == 0
    return Manifest(
        schema_version=1,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        source={
            "esde_dir": str(config.esde_dir),
            "esde_gamelists_dir": str(config.gamelists_dir),
            "roms_dir": str(config.roms_dir or ""),
            "roms_source": config.roms_source,
            "metadata_save_mode": config.metadata_save_mode,
        },
        scan_health={
            "systems_seen": len(systems),
            "gamelists_seen": gamelists_seen,
            "gamelists_ok": gamelists_ok,
            "gamelists_failed": gamelists_seen - gamelists_ok,
            "storage_roots_available": storage_available,
            "errors": errors,
            "warnings": sum(item.severity == "warning" for item in diagnostics),
            "removal_safe": removal_safe,
        },
        entries=entries,
        systems=systems,
        diagnostics=diagnostics,
    )
