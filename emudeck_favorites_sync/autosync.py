from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .compatibility import collect_compatibility
from .config import AppConfig
from .models import Manifest
from .scanner import scan
from .srm_apply import stage_apply
from .srm_cli import run_srm_add_owned, run_srm_remove_owned
from .state import save_manifest_atomic
from .steam_shortcuts import manual_entries, remove_stale_shortcuts, steam_library_status


SERVICE_NAME = "emudeck-favorites-sync.service"
DEFAULT_INTERVAL_SECONDS = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def autosync_state_path(config: AppConfig) -> Path:
    return config.state_dir / "autosync.json"


def autosync_log_path(config: AppConfig) -> Path:
    return config.state_dir / "autosync.log"


def last_srm_entries_path(config: AppConfig) -> Path:
    return config.state_dir / "last-srm-entries.json"


def service_path(config: AppConfig) -> Path:
    return config.home / ".config/systemd/user" / SERVICE_NAME


def load_autosync_state(config: AppConfig) -> dict[str, Any]:
    path = autosync_state_path(config)
    if not path.is_file():
        return {
            "enabled": False,
            "pending": False,
            "steam_import_pending": False,
            "srm_add_pending": False,
            "srm_remove_pending": False,
            "last_signature": "",
            "last_check_at": None,
            "last_steam_running": None,
            "last_change_detected_at": None,
            "last_sync_at": None,
            "last_srm_add_at": None,
            "last_srm_remove_at": None,
            "last_steam_import_at": None,
            "last_result": "never-run",
            "last_error": "",
            "favorites": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    defaults = load_autosync_state_defaults()
    defaults.update(data)
    return defaults


def load_autosync_state_defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "pending": False,
        "steam_import_pending": False,
        "srm_add_pending": False,
        "srm_remove_pending": False,
        "last_signature": "",
        "last_check_at": None,
        "last_steam_running": None,
        "last_change_detected_at": None,
        "last_sync_at": None,
        "last_srm_add_at": None,
        "last_srm_remove_at": None,
        "last_steam_import_at": None,
        "last_result": "never-run",
        "last_error": "",
        "favorites": [],
    }


def save_autosync_state(config: AppConfig, state: dict[str, Any]) -> None:
    path = autosync_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_last_srm_entries(config: AppConfig) -> list[dict[str, Any]]:
    path = last_srm_entries_path(config)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def save_last_srm_entries(config: AppConfig, entries: list[dict[str, Any]]) -> None:
    path = last_srm_entries_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def log_autosync(config: AppConfig, message: str) -> None:
    path = autosync_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{utc_now()} {message}\n")


def favorite_signature(manifest: Manifest) -> str:
    items = [
        {
            "id": entry.id,
            "system": entry.system,
            "title": entry.title,
            "path": entry.relative_rom_path,
        }
        for entry in manifest.entries
    ]
    return json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def favorite_summary(manifest: Manifest) -> list[dict[str, str]]:
    return [
        {
            "system": entry.system,
            "title": entry.title,
            "path": entry.relative_rom_path,
        }
        for entry in manifest.entries
    ]


def _run_systemctl(config: AppConfig, *args: str) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", *args],
            cwd=str(config.home),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


def _service_text(config: AppConfig) -> str:
    executable = config.home / ".local/bin/emudeck-favorites-sync"
    command = [
        str(executable),
        "--esde-dir",
        str(config.esde_dir),
        "--state-dir",
        str(config.state_dir),
    ]
    if config.roms_dir:
        command.extend(["--roms-dir", str(config.roms_dir)])
    command.append("autosync-check")
    return f"""[Unit]
Description=EmuDeck Favorites Sync pending startup check
After=graphical-session.target

[Service]
Type=oneshot
ExecStart={" ".join(command)}

[Install]
WantedBy=default.target
"""


def enable_autosync(config: AppConfig) -> dict[str, Any]:
    state = load_autosync_state(config)
    state["enabled"] = True
    state["last_result"] = "enabled"
    state["last_error"] = ""
    save_autosync_state(config, state)

    path = service_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_service_text(config), encoding="utf-8")
    daemon_ok, daemon_output = _run_systemctl(config, "daemon-reload")
    enable_ok, enable_output = _run_systemctl(config, "enable", SERVICE_NAME)
    start_ok, start_output = _run_systemctl(config, "start", SERVICE_NAME)
    log_autosync(config, "autosync enabled with event/startup checks")
    return {
        "enabled": True,
        "service_file": str(path),
        "systemd_ok": daemon_ok and enable_ok,
        "systemd_output": "\n".join(item for item in (daemon_output, enable_output, start_output) if item),
    }


def disable_autosync(config: AppConfig) -> dict[str, Any]:
    state = load_autosync_state(config)
    state["enabled"] = False
    state["pending"] = False
    state["steam_import_pending"] = False
    state["srm_add_pending"] = False
    state["srm_remove_pending"] = False
    state["last_result"] = "disabled"
    state["last_error"] = ""
    save_autosync_state(config, state)
    stop_ok, stop_output = _run_systemctl(config, "disable", "--now", SERVICE_NAME)
    log_autosync(config, "autosync disabled")
    return {
        "enabled": False,
        "service_file": str(service_path(config)),
        "systemd_ok": stop_ok,
        "systemd_output": stop_output,
    }


def _unit_active(config: AppConfig, unit_name: str) -> bool | None:
    ok, output = _run_systemctl(config, "is-active", unit_name)
    if ok:
        return output.strip() == "active"
    if "inactive" in output or "failed" in output:
        return False
    return None


def _service_active(config: AppConfig) -> bool | None:
    return _unit_active(config, SERVICE_NAME)


def autosync_status(config: AppConfig) -> dict[str, Any]:
    manifest = scan(config)
    state = load_autosync_state(config)
    state["favorites"] = favorite_summary(manifest)
    state["current_signature"] = favorite_signature(manifest)
    state["current_favorites_count"] = len(manifest.entries)
    state["service_active"] = _service_active(config)
    state["state_file"] = str(autosync_state_path(config))
    state["log_file"] = str(autosync_log_path(config))
    state["service_file"] = str(service_path(config))
    return state


def esde_closed(config: AppConfig) -> dict[str, Any]:
    state = load_autosync_state(config)
    state["last_esde_closed_at"] = utc_now()
    save_autosync_state(config, state)
    log_autosync(config, "ES-DE close trigger received")
    return autosync_once(config, force=False)


def autosync_once(config: AppConfig, *, force: bool = False) -> dict[str, Any]:
    state = load_autosync_state(config)
    manifest = scan(config)
    signature = favorite_signature(manifest)
    changed = signature != state.get("last_signature", "")
    if changed or force:
        state["pending"] = True
        if changed:
            state["last_change_detected_at"] = utc_now()
        state["last_signature"] = signature
        state["favorites"] = favorite_summary(manifest)
        save_manifest_atomic(config.state_dir / "desired.json", manifest)
        if changed:
            log_autosync(config, f"favorites changed; {len(manifest.entries)} valid favorites")
        else:
            log_autosync(config, f"manual update requested; forcing SRM reconciliation for {len(manifest.entries)} favorites")

    report = collect_compatibility(config)
    steam_running = report["steam"]["running"] is True
    state["last_check_at"] = utc_now()
    state["last_steam_running"] = steam_running
    steam_status = None
    if not state.get("pending") and not force and not steam_running:
        current_srm_entries = manual_entries(config)
        previous_srm_entries = load_last_srm_entries(config) or current_srm_entries
        steam_status = steam_library_status(
            config,
            current_entries=current_srm_entries,
            previous_entries=previous_srm_entries,
        )
        if steam_status.ok and steam_status.needs_reconcile:
            state["pending"] = True
            state["last_result"] = "steam-library-mismatch"
            save_autosync_state(config, state)
            log_autosync(
                config,
                "Steam library mismatch; "
                f"{len(steam_status.missing)} missing, {len(steam_status.stale)} stale shortcuts",
            )
    if not state.get("pending"):
        state["last_result"] = "no-change"
        save_autosync_state(config, state)
        return {
            "changed": changed,
            "forced": force,
            "synced": False,
            "reason": "no pending changes",
            "steam_library": steam_status.to_dict() if steam_status else None,
            "state": state,
        }
    if steam_running:
        state["last_result"] = "pending-steam-running"
        save_autosync_state(config, state)
        log_autosync(config, "pending changes; Steam is running")
        return {"changed": changed, "forced": force, "synced": False, "reason": "Steam is running", "state": state}

    existing_srm_entries = manual_entries(config)
    state["srm_remove_pending"] = True
    srm_remove = run_srm_remove_owned(config, steam_running=False)
    if not srm_remove.ok:
        log_autosync(config, "SRM remove did not complete; continuing with direct stale shortcut cleanup")
    state["srm_remove_pending"] = False
    if srm_remove.ok:
        state["last_srm_remove_at"] = utc_now()
        log_autosync(config, "ran Steam ROM Manager remove for existing ES-DE Favorites Sync parsers")

    result = stage_apply(config, manifest, dry_run=False, steam_running=False)
    srm_add = None
    steam_cleanup = None
    if result.ok and result.written:
        current_srm_entries = manual_entries(config)
        previous_srm_entries = load_last_srm_entries(config) or existing_srm_entries
        steam_cleanup = remove_stale_shortcuts(
            config,
            previous_entries=previous_srm_entries,
            current_entries=current_srm_entries,
            steam_running=False,
        )
        if steam_cleanup.ok and steam_cleanup.removed:
            log_autosync(config, f"direct cleanup removed {steam_cleanup.removed} stale Steam shortcuts")
        elif not steam_cleanup.ok:
            log_autosync(config, "direct stale shortcut cleanup did not complete")
        state["srm_add_pending"] = True
        srm_add = run_srm_add_owned(config, steam_running=False)
        if srm_add.ok:
            state["pending"] = False
            state["srm_add_pending"] = False
            state["last_srm_add_at"] = utc_now()
            state["last_result"] = "synced-and-srm-added"
            state["last_error"] = ""
            save_last_srm_entries(config, current_srm_entries)
            log_autosync(config, "ran Steam ROM Manager add for ES-DE Favorites Sync parsers")
        else:
            state["last_result"] = "staged-srm-add-blocked"
            state["last_error"] = "; ".join(item.message for item in srm_add.diagnostics if item.severity == "error")
            log_autosync(config, f"SRM add blocked: {state['last_error']}")
        state["last_sync_at"] = utc_now()
        log_autosync(config, f"synced {result.entries_written} favorites into SRM staging")
    else:
        state["last_result"] = "blocked"
        state["last_error"] = "; ".join(item.message for item in result.diagnostics if item.severity == "error")
        log_autosync(config, f"sync blocked: {state['last_error']}")
    save_autosync_state(config, state)
    return {
        "changed": changed,
        "forced": force,
        "synced": result.ok and result.written,
        "apply": result.to_dict(),
        "srm_remove": srm_remove.to_dict(),
        "steam_cleanup": steam_cleanup.to_dict() if steam_cleanup else None,
        "steam_library": steam_status.to_dict() if steam_status else None,
        "srm_add": srm_add.to_dict() if srm_add else None,
        "steam_import": None,
        "state": state,
    }


def watch_autosync(config: AppConfig, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> int:
    log_autosync(config, f"watcher started; interval={interval_seconds}s")
    while True:
        state = load_autosync_state(config)
        if not state.get("enabled"):
            log_autosync(config, "watcher exiting because autosync is disabled")
            return 0
        try:
            autosync_once(config)
        except Exception as error:  # defensive service loop
            state = load_autosync_state(config)
            state["last_result"] = "error"
            state["last_error"] = str(error)
            save_autosync_state(config, state)
            log_autosync(config, f"error: {error}")
        time.sleep(interval_seconds)
