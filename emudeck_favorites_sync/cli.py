from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import discover_config


# Commands from versions before 1.0. The old menu window may still be open right
# after an upgrade, so answer them politely instead of failing.
LEGACY_COMMANDS = {
    "autosync-now", "autosync-on", "autosync-off", "autosync-status", "list-favorites", "reset",
    "compatibility-report", "doctor", "scan", "plan", "check", "sync", "srm-preview", "esde-closed",
    "srm-add-now", "srm-remove-now", "set-srm-path", "set-parser-preference", "apply", "steam-import",
}

RESTART_MESSAGE = (
    f"EmuDeck Favorites Sync er oppdatert til versjon {__version__}.\n"
    "Lukk dette vinduet og start programmet på nytt fra skrivebordet for å bruke den nye versjonen."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emudeck-favorites-sync", description="Legg spill fra rom-mappa inn i Steam via Steam ROM Manager.")
    parser.add_argument("--roms-dir", help=argparse.SUPPRESS)
    parser.add_argument("--state-dir", help=argparse.SUPPRESS)
    parser.add_argument("--home", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="Åpne programvinduet (standard)")
    sub.add_parser("status", help="Vis rom-mappe, SRM og spill")
    for name, text in (("update", "Lagre endringene (samme som «Oppdater»)"), ("fix", "Samme som «Fiks»")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--no-steam", action="store_true", help="Ikke lukk/start Steam (Steam må være lukket)")
    sub.add_parser("report", help="Lagre en feilsøkingsrapport")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if any(item in LEGACY_COMMANDS for item in args_list):
        print(RESTART_MESSAGE)
        return 0
    args = _parser().parse_args(args_list)
    if args.command in (None, "gui"):
        from .gui import main as gui_main

        return gui_main()

    config = discover_config(args.roms_dir, args.state_dir, args.home)
    if args.command == "status":
        from .engine import export_state

        state = export_state(config)
        games = state.pop("games")
        print(json.dumps(state, ensure_ascii=False, indent=2))
        counts: dict[str, int] = {}
        for game in games:
            counts[game["status"]] = counts.get(game["status"], 0) + 1
        print(f"Spill: {len(games)} {counts}")
        return 0
    if args.command in {"update", "fix"}:
        from .engine import FIX, UPDATE, run

        report = run(config, UPDATE if args.command == "update" else FIX, progress=print, manage_steam=not args.no_steam)
        print(report.summary())
        return 0 if report.ok else 1
    if args.command == "report":
        from .engine import save_report

        print(save_report(config))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
