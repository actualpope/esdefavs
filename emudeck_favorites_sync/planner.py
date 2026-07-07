from __future__ import annotations

from .models import GameEntry, Manifest, Plan


def _sync_fields(entry: GameEntry) -> tuple[str, ...]:
    return (
        entry.title,
        entry.resolved_rom_path,
        entry.entry_type,
        entry.alternative_emulator,
    )


def build_plan(desired: Manifest, applied: Manifest | None) -> Plan:
    plan = Plan()
    desired_by_id = {entry.id: entry for entry in desired.entries}
    applied_by_id = {entry.id: entry for entry in (applied.entries if applied else [])}

    for entry_id in sorted(desired_by_id.keys() - applied_by_id.keys()):
        plan.additions.append(desired_by_id[entry_id])
    for entry_id in sorted(desired_by_id.keys() & applied_by_id.keys()):
        before = applied_by_id[entry_id]
        after = desired_by_id[entry_id]
        if _sync_fields(before) != _sync_fields(after):
            plan.changes.append((before, after))
    for entry_id in sorted(applied_by_id.keys() - desired_by_id.keys()):
        entry = applied_by_id[entry_id]
        health = desired.systems.get(entry.system)
        if health is None:
            plan.blocked_removals.append((entry, "system was not present in the latest healthy scan"))
        elif not health.removal_safe:
            plan.blocked_removals.append((entry, health.reason or "source was not healthy"))
        else:
            plan.removals.append(entry)
    plan.additions.sort(key=lambda item: (item.system, item.title.casefold()))
    plan.removals.sort(key=lambda item: (item.system, item.title.casefold()))
    plan.changes.sort(key=lambda item: (item[1].system, item[1].title.casefold()))
    plan.blocked_removals.sort(key=lambda item: (item[0].system, item[0].title.casefold()))
    return plan

