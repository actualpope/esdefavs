"""«Oppdater» and «Fiks»: bring Steam in line with our game list through SRM.

How SRM behaves (checked against its source), and what that means here:

* ``srm add`` makes Steam contain exactly what each *enabled* parser produces:
  new games are added, existing ones are refreshed, and games that parser added
  earlier but no longer lists are deleted. Parsers that are disabled are left
  alone. So for every console we touch, the manifest must list *all* of that
  console's games, not just the new ones.
* A run that produces zero games never finishes, so a console that becomes
  empty is handled with ``srm remove`` instead.
* SRM identifies a shortcut by its Exe + current name. A game renamed in Steam is
  therefore not recognised and gets re-added. We snapshot shortcuts.vdf before
  the run and afterwards merge such duplicates back and restore names (and, for
  «Oppdater», any launch options changed by hand).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .games import APPLIED, PENDING_ADD, PENDING_REMOVE, GameRecord, load_games, save_games
from .library import Library, scan_library
from .srm import (
    FAVORITES_COLLECTION,
    SrmError,
    build_entry,
    load_parser_preferences,
    load_srm,
    manual_root,
    parser_file,
    select_parser_candidate,
    parser_candidates,
    with_owned_parsers,
    write_manifest,
    write_parsers,
)
from .srm_cli import SrmCliResult, run_srm
from .steam import (
    appid,
    entry_command,
    field as vdf_field,
    game_mode,
    read_shortcuts,
    set_field,
    shortcut_command,
    shortcuts_files,
    shutdown_steam,
    start_steam,
    steam_running,
    tags,
    write_shortcuts,
)
from .util import backup, timestamp, write_json_atomic


UPDATE = "update"
FIX = "fix"

Progress = Callable[[str], None]


@dataclass
class RunReport:
    mode: str
    ok: bool = False
    nothing_to_do: bool = False
    error: str = ""
    added: list[str] = field(default_factory=list)
    add_failed: list[tuple[str, str]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    remove_failed: list[tuple[str, str]] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    fix_problems: list[tuple[str, str]] = field(default_factory=list)
    srm_runs: list[SrmCliResult] = field(default_factory=list)
    steam_was_running: bool = False
    steam_restarted: bool = False
    backup_dir: str = ""

    def summary(self) -> str:
        if self.error:
            return f"Noe gikk galt:\n\n{self.error}"
        if self.nothing_to_do:
            if self.mode == FIX:
                return "Det er ingen spill i SRM å fikse ennå."
            return "Ingen endringer å lagre. Flytt spill mellom listene først."
        lines: list[str] = []
        if self.mode == UPDATE:
            lines.append(f"{len(self.added)} spill ble lagt til i Steam.")
            if self.removed or self.remove_failed:
                lines.append(f"{len(self.removed)} spill ble fjernet fra Steam.")
        else:
            lines.append(f"Startinnstillingene ble sjekket for alle spill. {len(self.fixed)} spill fikk oppdaterte verdier.")
        if self.add_failed:
            lines.append("")
            lines.append("Ble ikke lagt til:")
            lines.extend(f"  • {title}: {reason}" for title, reason in self.add_failed)
        if self.remove_failed:
            lines.append("")
            lines.append("Ble ikke fjernet:")
            lines.extend(f"  • {title}: {reason}" for title, reason in self.remove_failed)
        if self.fix_problems:
            lines.append("")
            lines.append("Beholdt som før:")
            lines.extend(f"  • {title}: {reason}" for title, reason in self.fix_problems)
        failed_runs = [run for run in self.srm_runs if not run.ok]
        if failed_runs:
            lines.append("")
            lines.extend(run.error for run in failed_runs)
        if self.steam_restarted:
            lines.append("")
            lines.append("Steam startes igjen nå.")
        return "\n".join(lines)


def _log(progress: Progress | None, text: str) -> None:
    if progress:
        progress(text)


def _record_label(record: GameRecord) -> str:
    return f"{record.display_name} ({record.system})"


@dataclass
class _Plan:
    systems: list[str]
    desired: dict[str, list[tuple[GameRecord, dict[str, Any]]]]
    removals: dict[str, list[GameRecord]]
    add_failed: list[tuple[GameRecord, str]]
    fix_problems: list[tuple[GameRecord, str]]


def _plan(config: AppConfig, mode: str, records: list[GameRecord], library: Library, srm: Any) -> _Plan:
    if mode == UPDATE:
        systems = sorted({r.system for r in records if r.status in {PENDING_ADD, PENDING_REMOVE}})
    else:
        systems = sorted({r.system for r in records if r.in_steam})
    preferences = load_parser_preferences(config)
    environment = srm.environment
    games = library.by_id()
    plan = _Plan(systems=systems, desired={}, removals={}, add_failed=[], fix_problems=[])
    for system in systems:
        source = select_parser_candidate(
            parser_candidates(srm.dict_parsers, system, config.roms_dir), preferences.get(system)
        )
        desired: list[tuple[GameRecord, dict[str, Any]]] = []
        for record in (r for r in records if r.system == system):
            if record.status == PENDING_REMOVE and mode == UPDATE:
                plan.removals.setdefault(system, []).append(record)
                continue
            if record.status == PENDING_ADD and mode == FIX:
                continue
            recompute = record.status == PENDING_ADD or mode == FIX
            entry = record.entry
            if recompute:
                game = games.get(record.id) if record.rel_path else None
                if game is None:
                    if record.status == PENDING_ADD:
                        plan.add_failed.append((record, "Fant ikke rom-fila lenger."))
                        continue
                    if record.rel_path:
                        plan.fix_problems.append((record, "fant ikke rom-fila"))
                else:
                    result = build_entry(record.title, game.launch_path, system, source, environment)
                    if result.entry is not None:
                        entry = result.entry
                    elif record.status == PENDING_ADD:
                        plan.add_failed.append((record, result.problem))
                        continue
                    else:
                        plan.fix_problems.append((record, result.problem))
            if entry is None:
                continue
            desired.append((record, entry))
        plan.desired[system] = desired
    return plan


def _snapshot_shortcuts(files: list[Path]) -> dict[Path, dict[int, dict[str, Any]]]:
    snapshot: dict[Path, dict[int, dict[str, Any]]] = {}
    for path in files:
        try:
            shortcuts = read_shortcuts(path)
        except (OSError, ValueError):
            continue
        snapshot[path] = {value: copy.deepcopy(item) for item in shortcuts if (value := appid(item)) is not None}
    return snapshot


def _merge_duplicates(shortcuts: list[dict[str, Any]], is_ours: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    """Fold shortcuts that share an appid into the oldest one (SRM re-added a renamed game)."""
    keep: list[dict[str, Any]] = []
    first_by_appid: dict[int, dict[str, Any]] = {}
    for shortcut in shortcuts:
        value = appid(shortcut)
        if value is None or not is_ours(shortcut):
            keep.append(shortcut)
            continue
        original = first_by_appid.get(value)
        if original is None:
            first_by_appid[value] = shortcut
            keep.append(shortcut)
            continue
        for name in ("Exe", "StartDir", "LaunchOptions", "icon"):
            newer = vdf_field(shortcut, name)
            if newer is not None:
                set_field(original, name, newer)
        merged_tags = list(dict.fromkeys([*tags(original), *tags(shortcut)]))
        set_field(original, "tags", {str(index): tag for index, tag in enumerate(merged_tags)})
    return keep


def _reconcile_steam(
    mode: str,
    plan: _Plan,
    files: list[Path],
    snapshot: dict[Path, dict[int, dict[str, Any]]],
) -> tuple[dict[str, int], set[str]]:
    """Tidy shortcuts.vdf after SRM and find each game's shortcut.

    Returns the appid found for each desired record, and the ids of removed
    records that are still present in Steam.
    """
    desired = [(record, entry) for items in plan.desired.values() for record, entry in items]
    removals = [record for items in plan.removals.values() for record in items]
    known_appids = {r.appid for r, _ in desired if r.appid} | {r.appid for r in removals if r.appid}
    commands = {entry_command(entry) for _, entry in desired}
    commands |= {entry_command(r.entry) for r in removals if r.entry}

    def is_ours(shortcut: dict[str, Any]) -> bool:
        return (
            FAVORITES_COLLECTION in tags(shortcut)
            or appid(shortcut) in known_appids
            or shortcut_command(shortcut) in commands
        )

    found: dict[str, int] = {}
    still_present: set[str] = set()
    for path in files:
        try:
            original = read_shortcuts(path)
        except (OSError, ValueError):
            continue
        before = snapshot.get(path, {})
        shortcuts = _merge_duplicates(copy.deepcopy(original), is_ours)
        by_appid = {value: item for item in shortcuts if (value := appid(item)) is not None}
        claimed: set[int] = set()
        delete: set[int] = set()

        for record, entry in desired:
            expected = entry_command(entry)
            shortcut = None
            keep_old_fields = mode == UPDATE and record.status == APPLIED
            if keep_old_fields and record.appid in by_appid:
                shortcut = by_appid[record.appid]
            if shortcut is None:
                matches = [item for item in shortcuts if shortcut_command(item) == expected and id(item) not in claimed]
                matches.sort(key=lambda item: (appid(item) != record.appid, FAVORITES_COLLECTION not in tags(item)))
                shortcut = matches[0] if matches else None
            if shortcut is None and record.appid in by_appid and entry == record.entry:
                shortcut = by_appid[record.appid]
            if shortcut is None or id(shortcut) in claimed:
                continue
            claimed.add(id(shortcut))
            new_appid = appid(shortcut)
            if new_appid is not None:
                found.setdefault(record.id, new_appid)
            previous = before.get(record.appid) if record.appid else None
            if previous is not None:
                name = vdf_field(previous, "AppName")
                if name is not None:
                    set_field(shortcut, "AppName", name)
                if keep_old_fields:
                    for name_field in ("Exe", "StartDir", "LaunchOptions"):
                        value = vdf_field(previous, name_field)
                        if value is not None:
                            set_field(shortcut, name_field, value)
                if record.appid != new_appid and record.appid in by_appid:
                    delete.add(id(by_appid[record.appid]))
            # Same launch command and ours: a leftover duplicate of this game.
            for item in shortcuts:
                if item is not shortcut and shortcut_command(item) == expected and is_ours(item):
                    delete.add(id(item))

        for record in removals:
            targets = []
            if record.appid in by_appid:
                targets.append(by_appid[record.appid])
            if record.entry:
                expected = entry_command(record.entry)
                targets.extend(item for item in shortcuts if shortcut_command(item) == expected)
            for item in targets:
                if id(item) not in claimed:
                    delete.add(id(item))

        delete -= claimed
        result = [item for item in shortcuts if id(item) not in delete]
        for record in removals:
            expected = entry_command(record.entry) if record.entry else None
            if any(
                (record.appid is not None and appid(item) == record.appid)
                or (expected is not None and shortcut_command(item) == expected)
                for item in result
            ):
                still_present.add(record.id)
        if result != original:
            write_shortcuts(path, result)
    return found, still_present


def _backup_everything(config: AppConfig, files: list[Path]) -> Path:
    backup_dir = config.state_dir / "backups" / timestamp()
    backup(parser_file(config), backup_dir / "srm")
    backup(manual_root(config), backup_dir / "srm")
    backup(config.state_dir / "games.json", backup_dir)
    for path in files:
        backup(path, backup_dir / "steam" / path.parent.parent.name)
    return backup_dir


def _write_log(config: AppConfig, report: RunReport) -> None:
    lines = [f"mode: {report.mode}", f"ok: {report.ok}", f"error: {report.error}", ""]
    for run in report.srm_runs:
        lines += [
            f"== srm {run.action} {' '.join(run.systems)} -> ok={run.ok} code={run.returncode}",
            f"command: {' '.join(run.command)}",
            run.error,
            "-- stdout --",
            run.stdout,
            "-- stderr --",
            run.stderr,
            "",
        ]
    try:
        path = config.state_dir / "logs" / "last-run.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def run(config: AppConfig, mode: str, *, progress: Progress | None = None, manage_steam: bool = True) -> RunReport:
    report = RunReport(mode=mode)
    try:
        _run(config, mode, report, progress, manage_steam)
    except SrmError as error:
        report.error = str(error)
    except Exception as error:  # noqa: BLE001 - always hand Steam back and explain
        report.error = f"Uventet feil: {error!r}"
    finally:
        if report.steam_was_running and manage_steam and not steam_running():
            _log(progress, "Starter Steam igjen …")
            report.steam_restarted = start_steam()
        _write_log(config, report)
    return report


def _run(config: AppConfig, mode: str, report: RunReport, progress: Progress | None, manage_steam: bool) -> None:
    _log(progress, "Leser SRM-oppsettet …")
    srm = load_srm(config)
    if srm.delete_disabled_shortcuts is True:
        raise SrmError(
            "SRM-innstillingen «Delete disabled shortcuts» er slått på. Da ville SRM slettet spill fra "
            "andre parsere. Slå den av under «SRM-oppsett» først."
        )
    _log(progress, "Leser rom-mappa …")
    library = scan_library(config, srm)
    records = load_games(config, library)
    plan = _plan(config, mode, records, library, srm)

    for record, reason in plan.add_failed:
        report.add_failed.append((_record_label(record), reason))
    for record, reason in plan.fix_problems:
        report.fix_problems.append((_record_label(record), reason))
    add_failed_ids = {record.id for record, _ in plan.add_failed}

    active = [s for s in plan.systems if plan.desired.get(s) or plan.removals.get(s)]
    if not active:
        records[:] = [r for r in records if r.id not in add_failed_ids]
        save_games(config, records)
        report.nothing_to_do = not plan.add_failed
        report.ok = True
        return

    if steam_running():
        if not manage_steam:
            raise SrmError("Steam kjører. Lukk Steam først.")
        if game_mode():
            raise SrmError("Dette kan ikke kjøres i Game Mode. Bytt til Desktop Mode og prøv igjen.")
        report.steam_was_running = True
        _log(progress, "Lukker Steam …")
        if not shutdown_steam(progress=progress):
            raise SrmError("Steam ville ikke lukke seg. Lukk Steam selv og prøv igjen.")

    files = shortcuts_files(config)
    report.backup_dir = str(_backup_everything(config, files))
    snapshot = _snapshot_shortcuts(files)

    # Make sure every console with games has our parser (new consoles get one now).
    preferences = load_parser_preferences(config)
    sources: dict[str, dict[str, Any]] = {}
    for system in active:
        if plan.desired.get(system):
            source = select_parser_candidate(
                parser_candidates(srm.dict_parsers, system, config.roms_dir), preferences.get(system)
            )
            if source is not None:
                sources[system] = source
    owned = srm.owned_parsers()
    missing_parser = [s for s in active if plan.desired.get(s) and s not in owned and s not in sources]
    for system in missing_parser:
        # Only new games can fail here; games already in Steam are left untouched.
        for record, _ in plan.desired[system]:
            if record.status == PENDING_ADD:
                report.add_failed.append((_record_label(record), f"Fant ingen SRM-parser for «{system}»."))
                add_failed_ids.add(record.id)
        plan.desired[system] = []
        plan.removals.pop(system, None)
    active = [s for s in active if s not in missing_parser]
    write_parsers(config, with_owned_parsers(config, srm, sources))

    add_systems = [s for s in active if plan.desired.get(s)]
    emptied = [s for s in active if not plan.desired.get(s) and plan.removals.get(s)]

    if emptied:
        for system in emptied:
            write_manifest(config, system, [r.entry for r in plan.removals[system] if r.entry])
        runnable = [s for s in emptied if any(r.entry for r in plan.removals[s]) and (s in owned or s in sources)]
        if runnable:
            _log(progress, "Fjerner spill med Steam ROM Manager …")
            report.srm_runs.append(run_srm(config, "remove", runnable))
        for system in emptied:
            write_manifest(config, system, [])

    if add_systems:
        for system in add_systems:
            write_manifest(config, system, [entry for _, entry in plan.desired[system]])
        _log(progress, "Legger til spill med Steam ROM Manager (kan ta noen minutter) …")
        add_run = run_srm(config, "add", add_systems)
        report.srm_runs.append(add_run)

    _log(progress, "Kontrollerer Steam-biblioteket …")
    found, still_present = _reconcile_steam(mode, plan, files, snapshot)

    srm_add_ok = all(run.ok for run in report.srm_runs if run.action == "add")
    desired_by_id = {record.id: entry for items in plan.desired.values() for record, entry in items}
    removal_ids = {record.id for items in plan.removals.values() for record in items}
    updated: list[GameRecord] = []
    for record in records:
        if record.id in add_failed_ids:
            continue
        if record.id in removal_ids:
            if record.id in still_present:
                report.remove_failed.append((_record_label(record), "ligger fortsatt i Steam"))
                updated.append(record)
            else:
                report.removed.append(_record_label(record))
            continue
        if record.id in desired_by_id:
            entry = desired_by_id[record.id]
            if record.status == PENDING_ADD:
                if record.id in found:
                    record.status = APPLIED
                    record.entry = entry
                    record.appid = found[record.id]
                    report.added.append(_record_label(record))
                else:
                    reason = "SRM la ikke inn spillet" if srm_add_ok else "SRM feilet"
                    report.add_failed.append((_record_label(record), reason + " (prøves igjen neste gang)"))
            else:
                if entry != record.entry:
                    report.fixed.append(_record_label(record))
                record.entry = entry
                if record.id in found:
                    record.appid = found[record.id]
        updated.append(record)
    records[:] = updated
    save_games(config, records)
    report.ok = srm_add_ok and not report.remove_failed


def export_state(config: AppConfig) -> dict[str, Any]:
    """Everything useful for troubleshooting, as plain data."""
    from .library import scan_library as _scan
    from .srm import describe_systems, load_srm_or_none
    from .srm_cli import srm_command

    srm = load_srm_or_none(config)
    library = _scan(config, srm)
    records = load_games(config, library)
    systems = sorted({g.system for g in library.games} | {r.system for r in records})
    return {
        "roms_dir": str(config.roms_dir or ""),
        "roms_source": config.roms_source,
        "srm_app": srm_command(config)[1],
        "srm_found": srm is not None,
        "srm_delete_disabled_shortcuts": srm.delete_disabled_shortcuts if srm else None,
        "srm_retrieve_current_steam_images": srm.retrieve_current_steam_images if srm else None,
        "srm_environment": srm.environment if srm else {},
        "systems": [info.__dict__ for info in describe_systems(config, srm, systems)] if srm else [],
        "library_games": len(library.games),
        "library_error": library.error,
        "games": [record.to_dict() for record in records],
        "steam_shortcuts_files": [str(path) for path in shortcuts_files(config)],
    }


def save_report(config: AppConfig) -> Path:
    path = config.state_dir / "logs" / f"report-{timestamp()}.json"
    write_json_atomic(path, export_state(config))
    return path
