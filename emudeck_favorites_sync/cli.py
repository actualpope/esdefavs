from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .autosync import autosync_once, autosync_status, disable_autosync, enable_autosync, esde_closed, reset_favorites_sync
from .config import AppConfig, discover_config
from .compatibility import collect_compatibility
from .models import Diagnostic, Manifest, Plan
from .planner import build_plan
from .scanner import scan
from .state import load_manifest, save_manifest_atomic
from .srm_apply import describe_parser_matches, stage_apply
from .srm_cli import run_srm_add_owned, run_srm_remove_owned, set_srm_app_path
from .srm_preview import build_srm_preview
from .steam_shortcuts import import_to_steam


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emudeck-favorites-sync",
        description="Safely scan ES-DE favorites and preview a Steam sync plan.",
    )
    parser.add_argument("--esde-dir", help="ES-DE data directory (default: ~/ES-DE)")
    parser.add_argument("--roms-dir", help="ROM root containing system folders (for example /run/media/.../Emulation/roms)")
    parser.add_argument("--state-dir", help="Program state directory")
    parser.add_argument("--home", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check ES-DE, ROM storage and local configuration (read-only)")
    scan_parser = subparsers.add_parser("scan", help="Scan favorites and save desired state")
    scan_parser.add_argument("--no-save", action="store_true", help="Do not save desired.json")
    subparsers.add_parser("plan", help="Compare desired state with last applied state")
    subparsers.add_parser("check", help="Scan favorites and show the resulting plan")
    preview_parser = subparsers.add_parser("srm-preview", help="Create a safe SRM manifest preview (no Steam changes)")
    preview_parser.add_argument("--output", help="Output JSON file (default: state/srm-preview.json)")
    subparsers.add_parser("status", help="Show paths and current state")
    sync_parser = subparsers.add_parser("sync", help="Recommended flow: scan, validate, and optionally stage SRM files")
    sync_parser.add_argument("--confirm", action="store_true", help="Write SRM staging files after safety checks")
    subparsers.add_parser("autosync-on", help="Turn on background sync after Steam closes")
    subparsers.add_parser("autosync-off", help="Turn off background sync")
    subparsers.add_parser("autosync-status", help="Show whether background sync is on, pending, and what is favorited")
    autosync_now_parser = subparsers.add_parser("autosync-now", help="Run one autosync cycle now")
    autosync_now_parser.add_argument(
        "--summary", action="store_true", help="Print a short human-readable result instead of the full status"
    )
    subparsers.add_parser("list-favorites", help="Show which ES-DE games are currently favorited (read-only)")
    subparsers.add_parser("esde-closed", help=argparse.SUPPRESS)
    subparsers.add_parser("srm-add-now", help="Run Steam ROM Manager add for the ES-DE Favorites Sync parsers now")
    subparsers.add_parser("srm-remove-now", help="Run Steam ROM Manager remove for the ES-DE Favorites Sync parsers now")
    srm_path_parser = subparsers.add_parser("set-srm-path", help="Remember the Steam ROM Manager AppImage path")
    srm_path_parser.add_argument("path", help="Path to Steam-ROM-Manager.AppImage")
    subparsers.add_parser("steam-import-now", help="Import staged favorites into Steam shortcuts now")
    subparsers.add_parser("autosync-check", help=argparse.SUPPRESS)
    report_parser = subparsers.add_parser(
        "compatibility-report", help="Create a read-only SteamOS, EmuDeck, SRM and Steam report"
    )
    report_parser.add_argument("--output", help="Output JSON file (default: ~/Desktop/emudeck-favorites-sync-report.json)")
    apply_parser = subparsers.add_parser("apply", help="Stage safe SRM Manual parsers/manifests")
    apply_parser.add_argument("--dry-run", action="store_true", help="Validate and show what would be written")
    apply_parser.add_argument("--confirm", action="store_true", help="Write SRM staging files after safety checks")
    reset_parser = subparsers.add_parser(
        "reset", help="Remove every ES-DE Favorites Sync entry from SRM and Steam (does not touch ES-DE favorites)"
    )
    reset_parser.add_argument("--confirm", action="store_true", help="Actually remove; without this, only preview")
    return parser


def _config(args: argparse.Namespace) -> AppConfig:
    return discover_config(args.esde_dir, args.roms_dir, args.state_dir, args.home)


def _status_word(manifest: Manifest) -> str:
    health = manifest.scan_health
    if health.get("errors", 0):
        return "BLOCKED"
    if health.get("warnings", 0):
        return "READY_WITH_WARNINGS"
    return "READY"


def _print_header() -> None:
    print(f"EmuDeck Favorites Sync {__version__}")
    print("-" * 42)


def _print_diagnostics(items: list[Diagnostic]) -> None:
    if not items:
        print("Diagnostics: none")
        return
    print(f"Diagnostics ({len(items)}):")
    for item in items:
        location = f" [{item.system}]" if item.system else ""
        print(f"  {item.severity.upper():7}{location} {item.message}")


def _manifest_summary(manifest: Manifest) -> None:
    health = manifest.scan_health
    print(f"Result:       {_status_word(manifest)}")
    print(f"Systems:      {health.get('gamelists_ok', 0)}/{health.get('gamelists_seen', 0)} gamelists parsed")
    print(f"Favorites:    {len(manifest.entries)} valid favorites")
    print(f"Removal safe: {'yes' if health.get('removal_safe') else 'no'}")
    if manifest.entries:
        print("\nFavorites:")
        for entry in manifest.entries:
            suffix = f" (alternative emulator: {entry.alternative_emulator})" if entry.alternative_emulator else ""
            print(f"  {entry.system:12} {entry.title}{suffix}")
    if manifest.diagnostics:
        print()
    _print_diagnostics(manifest.diagnostics)


def _print_plan(plan: Plan) -> None:
    print(f"Add:             {len(plan.additions)}")
    print(f"Change:          {len(plan.changes)}")
    print(f"Remove:          {len(plan.removals)}")
    print(f"Blocked removal: {len(plan.blocked_removals)}")
    for heading, entries in (("Add", plan.additions), ("Remove", plan.removals)):
        if entries:
            print(f"\n{heading}:")
            for entry in entries:
                print(f"  {entry.system:12} {entry.title}")
    if plan.changes:
        print("\nChange:")
        for before, after in plan.changes:
            print(f"  {after.system:12} {after.title}")
            if before.resolved_rom_path != after.resolved_rom_path:
                print(f"               path: {before.resolved_rom_path} -> {after.resolved_rom_path}")
            if before.alternative_emulator != after.alternative_emulator:
                print(f"               emulator: {before.alternative_emulator or 'default'} -> {after.alternative_emulator or 'default'}")
    if plan.blocked_removals:
        print("\nBlocked removals (nothing will be deleted):")
        for entry, reason in plan.blocked_removals:
            print(f"  {entry.system:12} {entry.title}: {reason}")
    print("\nSteam/SRM changes: run 'apply --dry-run' to validate staging")


def _load_or_error(path: Path) -> Manifest | None:
    try:
        return load_manifest(path)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error


def _doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    manifest = scan(config)
    if args.json:
        print(json.dumps({"config": {
            "esde_dir": str(config.esde_dir), "roms_dir": str(config.roms_dir or ""),
            "state_dir": str(config.state_dir), "metadata_save_mode": config.metadata_save_mode,
            "roms_source": config.roms_source,
        }, "manifest": manifest.to_dict(), "compatibility": collect_compatibility(config)}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"ES-DE:        {config.esde_dir}")
        print(f"Gamelists:    {config.gamelists_dir}")
        print(f"ROMs:         {config.roms_dir or 'not detected'}")
        print(f"ROM source:   {config.roms_source}")
        print(f"Metadata save:{config.metadata_save_mode:>12}")
        print(f"State:        {config.state_dir}\n")
        _manifest_summary(manifest)
        compatibility = collect_compatibility(config)
        print("\nCompatibility probe:")
        print(f"  SRM AppImage: {compatibility['srm']['appimage'] or 'not found'}")
        print(f"  SRM parsers:  {len(compatibility['srm']['parsers'])}")
        print(f"  Steam users:  {len(compatibility['steam']['users'])}")
        running = compatibility["steam"]["running"]
        print(f"  Steam:        {'running' if running is True else 'stopped' if running is False else 'unknown'}")
    return 2 if manifest.scan_health.get("errors") else 0


def _run_scan(args: argparse.Namespace, show_plan: bool = False) -> int:
    config = _config(args)
    manifest = scan(config)
    desired_path = config.state_dir / "desired.json"
    if not getattr(args, "no_save", False):
        save_manifest_atomic(desired_path, manifest)
    if args.json:
        output: dict[str, object] = {"manifest": manifest.to_dict(), "saved_to": str(desired_path)}
        if show_plan:
            applied = _load_or_error(config.state_dir / "applied.json")
            output["plan"] = _plan_dict(build_plan(manifest, applied))
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        _print_header()
        _manifest_summary(manifest)
        if not getattr(args, "no_save", False):
            print(f"\nDesired state saved: {desired_path}")
        if show_plan:
            print("\nPlan")
            print("-" * 42)
            applied = _load_or_error(config.state_dir / "applied.json")
            _print_plan(build_plan(manifest, applied))
    return 2 if manifest.scan_health.get("errors") else 0


def _plan_dict(plan: Plan) -> dict[str, object]:
    return {
        "additions": [entry.to_dict() for entry in plan.additions],
        "removals": [entry.to_dict() for entry in plan.removals],
        "changes": [{"before": before.to_dict(), "after": after.to_dict()} for before, after in plan.changes],
        "blocked_removals": [{"entry": entry.to_dict(), "reason": reason} for entry, reason in plan.blocked_removals],
        "steam_changes_enabled": False,
    }


def _plan(args: argparse.Namespace) -> int:
    config = _config(args)
    desired = _load_or_error(config.state_dir / "desired.json")
    if desired is None:
        print("No desired state exists. Run 'emudeck-favorites-sync scan' first.", file=sys.stderr)
        return 2
    applied = _load_or_error(config.state_dir / "applied.json")
    plan = build_plan(desired, applied)
    if args.json:
        print(json.dumps(_plan_dict(plan), ensure_ascii=False, indent=2))
    else:
        _print_header()
        _print_plan(plan)
    return 0


def _save_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _srm_preview(args: argparse.Namespace) -> int:
    config = _config(args)
    manifest = scan(config)
    desired_path = config.state_dir / "desired.json"
    save_manifest_atomic(desired_path, manifest)
    preview = build_srm_preview(config, manifest)
    output = Path(args.output).expanduser() if args.output else config.state_dir / "srm-preview.json"
    _save_json_atomic(output, preview)
    if args.json:
        print(json.dumps({"saved_to": str(output), "preview": preview}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"SRM preview created:\n  {output}")
        print(f"\nFavorites:       {len(manifest.entries)}")
        print(f"Preview entries: {len(preview['entries'])}")
        print(f"Unmatched:       {len(preview['unmatched'])}")
        print("Steam/SRM changes: none")
        if preview["entries"]:
            print("\nPreview:")
            for item in preview["entries"]:
                warning = "  [check variables]" if item.get("warning") else ""
                print(f"  {item['system']:12} {item['title']} -> {item['parser_title']}{warning}")
        if preview["unmatched"]:
            print("\nUnmatched:")
            for item in preview["unmatched"]:
                print(f"  {item['system']:12} {item['title']}: {item['reason']}")
    return 2 if manifest.scan_health.get("errors") or preview["unmatched"] else 0


def _apply(args: argparse.Namespace) -> int:
    if args.dry_run and args.confirm:
        print("Use either --dry-run or --confirm, not both.", file=sys.stderr)
        return 2
    dry_run = not args.confirm
    config = _config(args)
    manifest = scan(config)
    report = collect_compatibility(config)
    result = stage_apply(config, manifest, dry_run=dry_run, steam_running=report["steam"]["running"])
    if args.json:
        print(json.dumps({"apply": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print("Mode:          " + ("dry-run" if dry_run else "confirm"))
        print(f"Result:        {'OK' if result.ok else 'BLOCKED'}")
        print(f"Entries:       {result.entries_written}")
        print(f"Parsers:       {len(result.parsers_written)}")
        print(f"SRM config:    {result.user_configurations}")
        print(f"Manifests:     {result.manual_manifest_root}")
        if result.parsers_written:
            print("\nOwned parsers:")
            for parser_id in result.parsers_written:
                print(f"  {parser_id}")
        if result.backups:
            print("\nBackups:")
            for backup in result.backups:
                print(f"  {backup}")
        if result.diagnostics:
            print()
            _print_diagnostics(result.diagnostics)
        if result.ok and dry_run:
            print("\nNo files were changed. Run again with --confirm to write SRM staging files.")
        elif result.written:
            print("\nSRM staging files were written. Open Steam ROM Manager and add games from the ES-DE Favorites Sync parsers.")
    if any(item.severity == "error" for item in result.diagnostics):
        return 2
    return 0


def _sync(args: argparse.Namespace) -> int:
    config = _config(args)
    manifest = scan(config)
    desired_path = config.state_dir / "desired.json"
    save_manifest_atomic(desired_path, manifest)
    report = collect_compatibility(config)
    result = stage_apply(config, manifest, dry_run=not args.confirm, steam_running=report["steam"]["running"])
    if args.json:
        print(json.dumps({
            "manifest": manifest.to_dict(),
            "desired_saved_to": str(desired_path),
            "apply": result.to_dict(),
        }, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print("Scan")
        print("-" * 42)
        _manifest_summary(manifest)
        print(f"\nDesired state saved: {desired_path}")
        print("\nSRM staging")
        print("-" * 42)
        print("Mode:          " + ("confirm" if args.confirm else "dry-run"))
        print(f"Result:        {'OK' if result.ok else 'BLOCKED'}")
        print(f"Entries:       {result.entries_written}")
        print(f"Parsers:       {len(result.parsers_written)}")
        if result.parsers_written:
            print("\nOwned parsers:")
            for parser_id in result.parsers_written:
                print(f"  {parser_id}")
        if result.backups:
            print("\nBackups:")
            for backup in result.backups:
                print(f"  {backup}")
        if result.diagnostics:
            print()
            _print_diagnostics(result.diagnostics)
        if result.ok and not args.confirm:
            print("\nNext:")
            print("  1. Close Steam completely.")
            print("  2. Run: ~/.local/bin/emudeck-favorites-sync sync --confirm")
        elif result.written:
            print("\nNext:")
            print("  Open Steam ROM Manager and run the parsers named 'ES-DE Favorites Sync - ...'.")
    if manifest.scan_health.get("errors") or any(item.severity == "error" for item in result.diagnostics):
        return 2
    return 0


def _autosync_on(args: argparse.Namespace) -> int:
    config = _config(args)
    result = enable_autosync(config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print("Autosync:      ON")
        print(f"Service file:  {result['service_file']}")
        print(f"Systemd:       {'OK' if result['systemd_ok'] else 'CHECK NEEDED'}")
        if result.get("systemd_output"):
            print("\nSystemd output:")
            print(result["systemd_output"])
        print("\nWhat happens now:")
        print("  Ny modell: ES-DE-lukkehooken er triggeren, ikke en timer.")
        print("  Hvis Steam kjører, lagres endringen som pending.")
        print("  Pending behandles ved neste trygge trigger/startup eller Oppdater Steam nå.")
        print("  Favoritt-endringer blir oppdaget i bakgrunnen.")
        print("  Hvis Steam kjører, venter programmet.")
        print("  Neste gang Steam er lukket, skrives SRM-staging automatisk.")
    return 0 if result["systemd_ok"] else 2


def _autosync_off(args: argparse.Namespace) -> int:
    config = _config(args)
    result = disable_autosync(config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print("Autosync:      OFF")
        print(f"Service file:  {result['service_file']}")
        print(f"Systemd:       {'OK' if result['systemd_ok'] else 'CHECK NEEDED'}")
        if result.get("systemd_output"):
            print("\nSystemd output:")
            print(result["systemd_output"])
    return 0 if result["systemd_ok"] else 2


def _print_autosync_status(data: dict[str, object]) -> None:
    service_active = data.get("service_active")
    if service_active is True:
        service = "running startup check"
    elif service_active is False:
        service = "idle"
    else:
        service = "unknown"
    print(f"Autosync:      {'ON' if data.get('enabled') else 'OFF'}")
    print(f"Service:       {service}")
    print(f"Pending sync:  {'yes' if data.get('pending') else 'no'}")
    print(f"Pending remove:{' yes' if data.get('srm_remove_pending') else ' no'}")
    print(f"Pending SRM:   {'yes' if data.get('srm_add_pending') else 'no'}")
    print(f"Pending import:{' yes' if data.get('steam_import_pending') else ' no'}")
    print(f"Last check:    {data.get('last_check_at') or 'never'}")
    steam_running = data.get("last_steam_running")
    if steam_running is True:
        steam_text = "running"
    elif steam_running is False:
        steam_text = "stopped"
    else:
        steam_text = "unknown"
    print(f"Steam seen:    {steam_text}")
    print(f"Last ES-DE:    {data.get('last_esde_closed_at') or 'never'}")
    print(f"Last change:   {data.get('last_change_detected_at') or 'never'}")
    print(f"Last sync:     {data.get('last_sync_at') or 'never'}")
    print(f"Last remove:   {data.get('last_srm_remove_at') or 'never'}")
    print(f"Last SRM add:  {data.get('last_srm_add_at') or 'never'}")
    print(f"Last import:   {data.get('last_steam_import_at') or 'never'}")
    print(f"Last result:   {data.get('last_result') or 'unknown'}")
    if data.get("last_error"):
        print(f"Last error:    {data['last_error']}")
    print(f"Favorites:     {data.get('current_favorites_count', 0)}")
    favorites = data.get("favorites")
    if isinstance(favorites, list) and favorites:
        print("\nCurrent favorites:")
        for item in favorites:
            if isinstance(item, dict):
                print(f"  {item.get('system', ''):12} {item.get('title', '')}")
    print("\nFiles:")
    print(f"  State:   {data.get('state_file')}")
    print(f"  Log:     {data.get('log_file')}")
    print(f"  Service: {data.get('service_file')}")


def _autosync_status(args: argparse.Namespace) -> int:
    config = _config(args)
    data = autosync_status(config)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_header()
        _print_autosync_status(data)
    return 0


def _print_autosync_summary(result: dict[str, object]) -> None:
    state = result.get("state") or {}
    count = len(state.get("favorites") or [])
    apply_result = result.get("apply") or {}
    warnings = [
        item for item in apply_result.get("diagnostics", [])
        if isinstance(item, dict) and item.get("severity") == "warning"
    ]
    if result.get("reason") == "Steam is running":
        print(f"Steam kjører. Lukk Steam helt og prøv igjen. ({count} favoritter registrert.)")
        return
    if result.get("synced"):
        print(f"Ferdig. {count} favoritt(er) er synkronisert til Steam.")
        if warnings:
            print(f"\n{len(warnings)} advarsel(er):")
            for item in warnings:
                location = f" [{item.get('system')}]" if item.get("system") else ""
                print(f"  {location} {item.get('message')}")
        return
    error = state.get("last_error") or "Ukjent feil. Kjør 'compatibility-report' i terminal for detaljer."
    print(f"Kunne ikke oppdatere Steam: {error}")


def _autosync_now(args: argparse.Namespace) -> int:
    config = _config(args)
    result = autosync_once(config, force=True)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif getattr(args, "summary", False):
        _print_autosync_summary(result)
    else:
        _print_header()
        print(f"Changed:       {'yes' if result.get('changed') else 'no'}")
        print(f"Forced update: {'yes' if result.get('forced') else 'no'}")
        print(f"Synced:        {'yes' if result.get('synced') else 'no'}")
        state = result["state"]
        print(f"Reason:        {result.get('reason', state.get('last_result'))}")
        print()
        _print_autosync_status(autosync_status(config))
    return 0 if result.get("synced") or result.get("reason") in {"no pending changes", "Steam is running"} else 2


def _list_favorites(args: argparse.Namespace) -> int:
    config = _config(args)
    manifest = scan(config)
    if args.json:
        print(json.dumps({"favorites": [entry.to_dict() for entry in manifest.entries]}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        if not manifest.entries:
            print("Ingen favoritter funnet i ES-DE.")
        else:
            print(f"Favoritter ({len(manifest.entries)}):\n")
            for entry in manifest.entries:
                print(f"  {entry.system:12} {entry.title}")
        if manifest.scan_health.get("errors"):
            print("\nMerk: enkelte systemer kunne ikke leses. Kjør 'doctor' for detaljer.")
    return 2 if manifest.scan_health.get("errors") else 0


def _autosync_check(args: argparse.Namespace) -> int:
    config = _config(args)
    result = autosync_once(config, force=False)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("synced") or result.get("reason") in {"no pending changes", "Steam is running"} else 2


def _esde_closed(args: argparse.Namespace) -> int:
    config = _config(args)
    result = esde_closed(config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("synced") or result.get("reason") in {"no pending changes", "Steam is running"} else 2


def _srm_add_now(args: argparse.Namespace) -> int:
    config = _config(args)
    report = collect_compatibility(config)
    result = run_srm_add_owned(config, steam_running=report["steam"]["running"])
    if args.json:
        print(json.dumps({"srm_add": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"Result:        {'OK' if result.ok else 'BLOCKED'}")
        print(f"AppImage:      {result.appimage or 'not found'}")
        print(f"Return code:   {result.returncode if result.returncode is not None else 'not run'}")
        if result.stdout:
            print("\nSRM stdout:")
            print(result.stdout)
        if result.stderr:
            print("\nSRM stderr:")
            print(result.stderr)
        if result.searched and not result.ok:
            print("\nSearched:")
            for path in result.searched:
                print(f"  {path}")
        if result.diagnostics:
            print()
            _print_diagnostics(result.diagnostics)
    return 0 if result.ok else 2


def _srm_remove_now(args: argparse.Namespace) -> int:
    config = _config(args)
    report = collect_compatibility(config)
    result = run_srm_remove_owned(config, steam_running=report["steam"]["running"])
    if args.json:
        print(json.dumps({"srm_remove": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"Result:        {'OK' if result.ok else 'BLOCKED'}")
        print(f"AppImage:      {result.appimage or 'not found'}")
        print(f"Return code:   {result.returncode if result.returncode is not None else 'not run'}")
        if result.stdout:
            print("\nSRM stdout:")
            print(result.stdout)
        if result.stderr:
            print("\nSRM stderr:")
            print(result.stderr)
        if result.searched and not result.ok:
            print("\nSearched:")
            for path in result.searched:
                print(f"  {path}")
        if result.diagnostics:
            print()
            _print_diagnostics(result.diagnostics)
    return 0 if result.ok else 2


def _set_srm_path(args: argparse.Namespace) -> int:
    config = _config(args)
    path = set_srm_app_path(config, args.path)
    if args.json:
        print(json.dumps({"srm_appimage": str(path)}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"SRM AppImage saved: {path}")
        print("\nNext:")
        print("  Close Steam completely, then run 'srm-add-now' or use the GUI.")
    return 0


def _steam_import_now(args: argparse.Namespace) -> int:
    config = _config(args)
    report = collect_compatibility(config)
    result = import_to_steam(config, steam_running=report["steam"]["running"])
    if args.json:
        print(json.dumps({"steam_import": result.to_dict()}, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"Result:        {'OK' if result.ok else 'BLOCKED'}")
        print(f"Entries:       {result.entries_imported}")
        print(f"Steam users:   {result.users_written}/{result.users_seen}")
        if result.shortcuts_files:
            print("\nWritten shortcuts:")
            for path in result.shortcuts_files:
                print(f"  {path}")
        if result.backups:
            print("\nBackups:")
            for backup in result.backups:
                print(f"  {backup}")
        if result.diagnostics:
            print()
            _print_diagnostics(result.diagnostics)
    return 0 if result.ok else 2


def _reset(args: argparse.Namespace) -> int:
    config = _config(args)
    result = reset_favorites_sync(config, dry_run=not args.confirm)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_header()
        if result.get("steam_running"):
            print("Steam kjører. Lukk Steam helt og prøv igjen.")
        elif result["dry_run"]:
            print(f"Dette VILLE fjernet {len(result['parsers_found'])} SRM-parser(e) og alle Steam-oppføringer laget av dem.")
            print(f"Nåværende favorittoppføringer i manifestet: {result['manifest_entries_found']}")
            if result["parsers_found"]:
                print("\nParsere som ville blitt fjernet:")
                for parser_id in result["parsers_found"]:
                    print(f"  {parser_id}")
            print("\nES-DE sine egne favoritter røres ikke. Ingenting er endret ennå.")
            print("Kjør 'reset --confirm' for å faktisk fjerne dette.")
        else:
            print(f"Fjernet {len(result['parsers_found'])} SRM-parser(e).")
            steam_cleanup = result.get("steam_cleanup") or {}
            print(f"Fjernet {steam_cleanup.get('removed', 0)} Steam-snarvei(er).")
            print("Autosync-status og lagret tilstand er nullstilt.")
            print("ES-DE sine egne favoritter er ikke endret.")
    return 0 if result.get("ok") or result.get("dry_run") else 2


def _show_status(args: argparse.Namespace) -> int:
    config = _config(args)
    desired = _load_or_error(config.state_dir / "desired.json")
    applied = _load_or_error(config.state_dir / "applied.json")
    data = {
        "version": __version__, "esde_dir": str(config.esde_dir),
        "roms_dir": str(config.roms_dir or ""), "state_dir": str(config.state_dir),
        "desired_exists": desired is not None, "applied_exists": applied is not None,
        "favorites": len(desired.entries) if desired else None,
        "srm_preview_exists": (config.state_dir / "srm-preview.json").is_file(),
        "steam_changes_enabled": False,
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_header()
        for label, value in (
            ("ES-DE", data["esde_dir"]), ("ROMs", data["roms_dir"] or "not detected"),
            ("State", data["state_dir"]), ("Desired state", "present" if desired else "not created"),
            ("Applied state", "present" if applied else "not created (expected in phase 1)"),
            ("SRM preview", "present" if data["srm_preview_exists"] else "not created"),
            ("Steam apply", "disabled (safe test version)"),
        ):
            print(f"{label + ':':15} {value}")
    return 0


def _compatibility_report(args: argparse.Namespace) -> int:
    config = _config(args)
    manifest = scan(config)
    report = collect_compatibility(config)
    report["scan"] = manifest.to_dict()
    parser_matches = describe_parser_matches(config, manifest)
    report["parser_matches"] = parser_matches
    output = Path(args.output).expanduser() if args.output else config.home / "Desktop/emudeck-favorites-sync-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_header()
        print(f"SRM parsers: {len(report['srm']['parsers'])}")
        print(f"Steam users: {len(report['steam']['users'])}")
        print(f"Valid favorites: {len(manifest.entries)}")
        print("\nEmulator-matching per system:")
        if not parser_matches:
            print("  (ingen favoritter å sjekke)")
        for item in parser_matches:
            if item["matched_parser"] is None:
                print(f"  {item['system']:10} ({item['example_title']}): INGEN SRM-parser funnet")
                continue
            problem = ""
            if item["unresolved_target"]:
                problem = "  <-- KJØRBAR FIL LØSER TIL TOMT, VIL IKKE STARTE"
            print(f"  {item['system']:10} {item['example_title']:20} -> {item['matched_parser']}{problem}")
            print(f"               target: {item['resolved_target'] or '(tomt)'}")
            if item["competing_parsers"]:
                print(f"               ADVARSEL: like god match mot: {', '.join(item['competing_parsers'])}")
        print(f"\nRapport lagret: {output}")
        print("Ingen Steam-, SRM- eller EmuDeck-konfigurasjon ble endret.")
    return 2 if manifest.scan_health.get("errors") else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "scan":
        return _run_scan(args)
    if args.command == "check":
        return _run_scan(args, show_plan=True)
    if args.command == "plan":
        return _plan(args)
    if args.command == "srm-preview":
        return _srm_preview(args)
    if args.command == "sync":
        return _sync(args)
    if args.command == "autosync-on":
        return _autosync_on(args)
    if args.command == "autosync-off":
        return _autosync_off(args)
    if args.command == "autosync-status":
        return _autosync_status(args)
    if args.command == "autosync-now":
        return _autosync_now(args)
    if args.command == "list-favorites":
        return _list_favorites(args)
    if args.command == "autosync-check":
        return _autosync_check(args)
    if args.command == "esde-closed":
        return _esde_closed(args)
    if args.command == "srm-add-now":
        return _srm_add_now(args)
    if args.command == "srm-remove-now":
        return _srm_remove_now(args)
    if args.command == "set-srm-path":
        return _set_srm_path(args)
    if args.command == "steam-import-now":
        return _steam_import_now(args)
    if args.command == "status":
        return _show_status(args)
    if args.command == "compatibility-report":
        return _compatibility_report(args)
    if args.command == "apply":
        return _apply(args)
    if args.command == "reset":
        return _reset(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
