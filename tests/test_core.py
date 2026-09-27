from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from srm_sync import cli
from srm_sync.config import discover_config
from srm_sync.engine import FIX, UPDATE, run
from srm_sync.games import (
    APPLIED,
    PENDING_ADD,
    PENDING_REMOVE,
    RecordIndex,
    already_in_srm,
    discard_changes,
    load_games,
    move_out_of_srm,
    move_to_srm,
    pending_changes,
    save_games,
)
from srm_sync.library import RomGame, logical_id, scan_library
from srm_sync.srm import (
    OWNED_PARSER_PREFIX,
    build_entry,
    load_srm,
    manifest_path,
    manual_root,
    new_owned_parser,
    parser_extensions,
    read_manifest,
    save_parser_preference,
)
from srm_sync.srm_cli import run_srm
from srm_sync.steam import field, read_shortcuts, set_field, shortcut_appid, write_shortcuts


FAKE_SRM = Path(__file__).resolve().parent / "fake_srm.py"


def glob_parser(system: str, title: str, exe: str, args: str, extensions: str) -> dict:
    return {
        "parserType": "Glob",
        "configTitle": title,
        "parserId": f"emudeck-{system}-{title}",
        "steamCategories": [title.split(" - ")[0]],
        "romDirectory": f"${{romsdirglobal}}/{system}",
        "executable": {"path": exe, "shortcutPassthrough": False, "appendArgsToExecutable": True},
        "executableArgs": args,
        "executableModifier": '"${exePath}"',
        "startInDirectory": "",
        "titleModifier": "${fuzzyTitle}",
        "parserInputs": {"glob": f"${{title}}@({extensions})"},
        "disabled": True,
    }


class Home:
    """A fake Steam Deck home with EmuDeck-like SRM parsers and a fake SRM."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.roms = self.home / "Emulation/roms"
        self.user_data = self.home / ".config/steam-rom-manager/userData"
        self.vdf = self.home / ".local/share/Steam/userdata/12345/config/shortcuts.vdf"
        self.state = self.home / ".local/state/srm-sync"
        self._roms()
        self._srm()
        write_shortcuts(self.vdf, [{
            "appid": shortcut_appid('"/usr/bin/firefox"', "Firefox"), "AppName": "Firefox",
            "Exe": '"/usr/bin/firefox"', "StartDir": "", "LaunchOptions": "", "tags": {},
        }])
        os.environ["FAKE_SRM_HOME"] = str(self.home)

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def touch(self, relative: str, text: str = "x") -> Path:
        path = self.roms / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _roms(self) -> None:
        self.touch("snes/Super Game (USA).sfc")
        self.touch("snes/Other Game (Europe).smc")
        self.touch("snes/Third.sfc")
        self.touch("snes/readme.txt")
        self.touch("snes/systeminfo.txt")
        self.touch("psx/Multi (USA).m3u/Multi (USA).m3u", "Multi (Disc 1).chd\nMulti (Disc 2).chd\n")
        self.touch("psx/Multi (USA).m3u/Multi (Disc 1).chd")
        self.touch("psx/Multi (USA).m3u/Multi (Disc 2).chd")
        self.touch("psx/Two.m3u", "Two (Disc 1).cue\nTwo (Disc 2).cue\n")
        self.touch("psx/Two (Disc 1).cue", 'FILE "Two (Disc 1).bin" BINARY\n')
        self.touch("psx/Two (Disc 1).bin")
        self.touch("psx/Two (Disc 2).cue", 'FILE "Two (Disc 2).bin" BINARY\n')
        self.touch("psx/Two (Disc 2).bin")
        self.touch("psx/Single.cue", 'FILE "Single.bin" BINARY\n')
        self.touch("psx/Single.bin")
        self.touch("psx/Sub/Nested.chd")
        self.touch("ps2/Big.m3u/Big.m3u", "Big (Disc 1).chd\nBig (Disc 2).chd\n")
        self.touch("ps2/Big.m3u/Big (Disc 1).chd")
        self.touch("ps2/Big.m3u/Big (Disc 2).chd")
        self.touch("ps2/Solo.chd")
        self.touch("gba/Mini.gba")
        self.touch("n64/NoParser.z64")
        self.touch("gc/Cube Game.iso")
        self.touch("gamecube/Cube Game.iso")
        self.touch("gbc/Pocket.gbc")
        self.touch("wiiu/Zelda.wua")
        self.touch("wiiu/Other/code/app.rpx")
        self.touch("wiiu/Loose.wud")
        self.touch("desktop/thing.desktop")

    def _srm(self) -> None:
        parsers = [
            glob_parser("snes", "Nintendo SNES - RetroArch Snes9x", "${retroarchpath}",
                        '-L ${racores}${/}snes9x_libretro.so "${filePath}"', ".sfc|.SFC|.smc|.zip"),
            glob_parser("psx", "Sony PlayStation - DuckStation", "/usr/bin/duckstation",
                        '-batch "${filePath}"', ".cue|.chd|.m3u|.CHD"),
            glob_parser("ps2", "Sony PlayStation 2 - PCSX2", "/usr/bin/pcsx2", '-batch "${filePath}"', ".chd|.iso"),
            glob_parser("gba", "Nintendo GBA - RetroArch mGBA", "${retroarchpath}",
                        '-L ${racores}${/}mgba_libretro.so "${filePath}"', ".gba|.zip"),
            {"parserType": "Glob", "configTitle": "Something else", "parserId": "other", "romDirectory": "/nowhere",
             "executable": {"path": "/x"}, "disabled": False},
        ]
        self.user_data.mkdir(parents=True)
        self.write_parsers(parsers)
        (self.user_data / "userSettings.json").write_text(json.dumps({
            "environmentVariables": {
                "retroarchPath": "/usr/bin/retroarch", "raCoresDirectory": "/cores",
                "romsDirectory": str(self.roms), "steamDirectory": str(self.home / ".local/share/Steam"),
            },
            "previewSettings": {"deleteDisabledShortcuts": False, "retrieveCurrentSteamImages": True},
        }), encoding="utf-8")
        app = self.home / "Applications/Steam-ROM-Manager.AppImage"
        app.parent.mkdir(parents=True)
        app.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SRM}" "$@"\n', encoding="utf-8")
        app.chmod(app.stat().st_mode | stat.S_IXUSR)

    # -- helpers

    def parsers(self) -> list:
        return json.loads((self.user_data / "userConfigurations.json").read_text(encoding="utf-8"))

    def write_parsers(self, parsers: list) -> None:
        (self.user_data / "userConfigurations.json").write_text(json.dumps(parsers), encoding="utf-8")

    def config(self):
        return discover_config(home_override=str(self.home))

    def shortcuts(self) -> list[dict]:
        return read_shortcuts(self.vdf)

    def names(self) -> list[str]:
        return sorted(str(field(item, "AppName")) for item in self.shortcuts())

    def srm_calls(self) -> list[dict]:
        path = self.user_data / "fake-srm-calls.log"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def old_install(self, favorites: dict[str, list[tuple[str, str]]]) -> None:
        """Recreate what 0.7.5 left behind: owned parsers, manifests, applied.json and Steam shortcuts."""
        config = self.config()
        srm = load_srm(config)
        parsers = self.parsers()
        applied = []
        for system, games in favorites.items():
            source = next(p for p in parsers if p.get("parserId", "").startswith(f"emudeck-{system}-"))
            entries = []
            for esde_name, rel_path in games:
                launch = self.roms / system / rel_path
                if launch.is_dir():
                    launch = launch / launch.name
                entries.append(build_entry(esde_name, str(launch.resolve()), system, source, srm.environment).entry)
                applied.append({"system": system, "title": esde_name, "relative_rom_path": rel_path,
                                "resolved_rom_path": str(launch.resolve())})
            path = manifest_path(config, system)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entries), encoding="utf-8")
            parsers.append(new_owned_parser(source, system, manual_root(config) / system))
        self.write_parsers(parsers)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "applied.json").write_text(json.dumps({"schema_version": 1, "entries": applied}), encoding="utf-8")
        # 0.7.5 ran SRM add with all owned parsers enabled.
        result = run_srm(config, "add", list(favorites))
        assert result.ok, result


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Home()
        self.addCleanup(self.env.cleanup)

    def library(self):
        config = self.env.config()
        return config, scan_library(config, load_srm(config))

    def add(self, *items: tuple[str, str]) -> None:
        config, library = self.library()
        records = load_games(config, library)
        games = {(g.system, g.rel_path): g for g in library.games}
        for key in items:
            move_to_srm(records, games[key])
        save_games(config, records)

    def remove(self, *items: tuple[str, str]) -> None:
        config, library = self.library()
        records = load_games(config, library)
        for system, rel_path in items:
            move_out_of_srm(records, logical_id(system, rel_path))
        save_games(config, records)

    def update(self, mode: str = UPDATE):
        report = run(self.env.config(), mode)
        self.assertEqual(report.error, "", report.summary())
        return report


class LibraryTests(Base):
    def test_scan_lists_only_games(self) -> None:
        _, library = self.library()
        found = {(g.system, g.rel_path) for g in library.games}
        self.assertEqual(found, {
            ("snes", "Other Game (Europe).smc"), ("snes", "Super Game (USA).sfc"), ("snes", "Third.sfc"),
            ("psx", "Multi (USA).m3u"), ("psx", "Two.m3u"), ("psx", "Single.cue"), ("psx", "Sub/Nested.chd"),
            ("ps2", "Big.m3u/Big (Disc 1).chd"), ("ps2", "Big.m3u/Big (Disc 2).chd"), ("ps2", "Solo.chd"),
            ("gba", "Mini.gba"), ("n64", "NoParser.z64"),
            ("gc", "Cube Game.iso"), ("gbc", "Pocket.gbc"), ("wiiu", "Zelda.wua"),
        })

    def test_sorted_by_console_then_name(self) -> None:
        _, library = self.library()
        keys = [(g.system.casefold(), g.rel_path.casefold()) for g in library.games]
        self.assertEqual(keys, sorted(keys))

    def test_m3u_folder_launches_inner_playlist(self) -> None:
        _, library = self.library()
        game = next(g for g in library.games if g.rel_path == "Multi (USA).m3u")
        self.assertTrue(game.launch_path.endswith("Multi (USA).m3u/Multi (USA).m3u"))
        self.assertEqual(game.title, "Multi (USA)")
        self.assertEqual(game.id, logical_id("psx", "Multi (USA).m3u"))

    def test_parser_extensions(self) -> None:
        parser = {"parserInputs": {"glob": "**/${title}@(.sfc|.SFC|.zip)"}}
        self.assertEqual(parser_extensions(parser), {".sfc", ".zip"})


class MigrationTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.env.old_install({
            "snes": [("Super Game", "Super Game (USA).sfc"), ("Other Game", "Other Game (Europe).smc")],
            "psx": [("Multi", "Multi (USA).m3u")],
        })
        self.before = self.env.shortcuts()

    def test_existing_games_are_imported_verbatim(self) -> None:
        config, library = self.library()
        old_manifests = {s: read_manifest(config, s) for s in ("snes", "psx")}
        records = load_games(config, library)
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r.status == APPLIED and r.appid for r in records))
        by_rel = {r.rel_path: r for r in records}
        self.assertEqual(set(by_rel), {"Super Game (USA).sfc", "Other Game (Europe).smc", "Multi (USA).m3u"})
        self.assertIn(by_rel["Super Game (USA).sfc"].entry, old_manifests["snes"])
        self.assertEqual(by_rel["Multi (USA).m3u"].id, logical_id("psx", "Multi (USA).m3u"))
        self.assertEqual(self.env.shortcuts(), self.before)

    def test_orphans_are_kept(self) -> None:
        (self.env.roms / "snes/Other Game (Europe).smc").unlink()
        (self.env.state / "applied.json").unlink()
        config, library = self.library()
        records = load_games(config, library)
        self.assertEqual(len(records), 3)
        orphan = next(r for r in records if r.title == "Other Game")
        self.assertEqual(orphan.rel_path, "")
        self.assertTrue(orphan.id.startswith("orphan:"))

    def test_mapping_without_applied_json_uses_rom_path(self) -> None:
        (self.env.state / "applied.json").unlink()
        config, library = self.library()
        records = load_games(config, library)
        self.assertEqual({r.rel_path for r in records}, {"Super Game (USA).sfc", "Other Game (Europe).smc", "Multi (USA).m3u"})


class UpdateTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.env.old_install({"snes": [("Super Game", "Super Game (USA).sfc"), ("Other Game", "Other Game (Europe).smc")],
                              "psx": [("Multi", "Multi (USA).m3u")]})
        self.appids_before = {field(s, "AppName"): field(s, "appid") for s in self.env.shortcuts()}
        (self.env.user_data / "fake-srm-calls.log").unlink()

    def test_add_game_keeps_existing_and_only_touches_that_console(self) -> None:
        self.add(("snes", "Third.sfc"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(len(report.added), 1)
        self.assertIn("1 spill ble lagt til", report.summary())
        self.assertEqual(self.env.names(), ["Firefox", "Multi", "Other Game", "Super Game", "Third"])
        after = {field(s, "AppName"): field(s, "appid") for s in self.env.shortcuts()}
        for name in ("Super Game", "Other Game", "Multi", "Firefox"):
            self.assertEqual(after[name], self.appids_before[name])
        calls = self.env.srm_calls()
        self.assertEqual(calls, [{"action": "add", "enabled": [f"{OWNED_PARSER_PREFIX}snes"]}])
        # Our parsers are switched back on afterwards, EmuDeck's stay as they were.
        parsers = self.env.parsers()
        self.assertTrue(all(not p["disabled"] for p in parsers if p["parserId"].startswith(OWNED_PARSER_PREFIX)))
        emudeck = [p for p in parsers if p["parserId"].startswith("emudeck-") and not p["parserId"].startswith(OWNED_PARSER_PREFIX)]
        self.assertTrue(emudeck and all(p["disabled"] for p in emudeck))
        self.assertFalse(next(p for p in parsers if p["parserId"] == "other")["disabled"])

    def test_new_console_gets_its_own_parser(self) -> None:
        self.add(("gba", "Mini.gba"))
        report = self.update()
        self.assertEqual(len(report.added), 1)
        owned = [p for p in self.env.parsers() if p["parserId"] == f"{OWNED_PARSER_PREFIX}gba"]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["configTitle"], "SRM Sync - Nintendo GBA")
        self.assertIn("Mini", self.env.names())

    def test_renamed_game_keeps_its_name_and_is_not_duplicated(self) -> None:
        config, library = self.library()
        load_games(config, library)
        shortcuts = self.env.shortcuts()
        for item in shortcuts:
            if field(item, "AppName") == "Super Game":
                set_field(item, "AppName", "Mitt Supre Spill")
        write_shortcuts(self.env.vdf, shortcuts)
        self.add(("snes", "Third.sfc"))
        self.update()
        self.assertEqual(self.env.names(), ["Firefox", "Mitt Supre Spill", "Multi", "Other Game", "Third"])
        renamed = next(s for s in self.env.shortcuts() if field(s, "AppName") == "Mitt Supre Spill")
        self.assertEqual(field(renamed, "appid"), self.appids_before["Super Game"])

    def test_launch_options_changed_by_hand_are_kept_by_update(self) -> None:
        config, library = self.library()
        load_games(config, library)
        shortcuts = self.env.shortcuts()
        for item in shortcuts:
            if field(item, "AppName") == "Other Game":
                set_field(item, "LaunchOptions", "--my-own-flag")
        write_shortcuts(self.env.vdf, shortcuts)
        self.add(("snes", "Third.sfc"))
        self.update()
        other = [s for s in self.env.shortcuts() if field(s, "AppName") == "Other Game"]
        self.assertEqual(len(other), 1)
        self.assertEqual(field(other[0], "LaunchOptions"), "--my-own-flag")

    def test_remove_game(self) -> None:
        self.remove(("snes", "Other Game (Europe).smc"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.removed, ["Other Game (Europe).smc (snes)"])
        self.assertEqual(self.env.names(), ["Firefox", "Multi", "Super Game"])
        config, library = self.library()
        self.assertEqual(len(load_games(config, library)), 2)

    def test_remove_last_game_of_console_uses_srm_remove(self) -> None:
        self.remove(("psx", "Multi (USA).m3u"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(self.env.names(), ["Firefox", "Other Game", "Super Game"])
        self.assertEqual(self.env.srm_calls(), [{"action": "remove", "enabled": [f"{OWNED_PARSER_PREFIX}psx"]}])
        config = self.env.config()
        self.assertEqual(read_manifest(config, "psx"), [])
        self.assertTrue(any(p["parserId"] == f"{OWNED_PARSER_PREFIX}psx" for p in self.env.parsers()))

    def test_renamed_game_can_be_removed(self) -> None:
        config, library = self.library()
        load_games(config, library)
        shortcuts = self.env.shortcuts()
        for item in shortcuts:
            if field(item, "AppName") == "Multi":
                set_field(item, "AppName", "Flerdisk")
        write_shortcuts(self.env.vdf, shortcuts)
        self.remove(("psx", "Multi (USA).m3u"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(self.env.names(), ["Firefox", "Other Game", "Super Game"])

    def test_add_and_undo_before_update_changes_nothing(self) -> None:
        self.add(("snes", "Third.sfc"))
        self.remove(("snes", "Third.sfc"))
        report = self.update()
        self.assertTrue(report.nothing_to_do)
        self.assertEqual(self.env.srm_calls(), [])

    def test_console_without_parser_is_refused(self) -> None:
        self.add(("n64", "NoParser.z64"))
        report = self.update()
        self.assertEqual(len(report.add_failed), 1)
        self.assertEqual(self.env.srm_calls(), [])
        config, library = self.library()
        self.assertFalse(any(r.system == "n64" for r in load_games(config, library)))

    def test_delete_disabled_shortcuts_blocks(self) -> None:
        settings = json.loads((self.env.user_data / "userSettings.json").read_text(encoding="utf-8"))
        settings["previewSettings"]["deleteDisabledShortcuts"] = True
        (self.env.user_data / "userSettings.json").write_text(json.dumps(settings), encoding="utf-8")
        self.add(("snes", "Third.sfc"))
        report = run(self.env.config(), UPDATE)
        self.assertIn("Delete disabled shortcuts", report.error)
        self.assertEqual(self.env.srm_calls(), [])

    def test_backup_is_made(self) -> None:
        self.add(("snes", "Third.sfc"))
        report = self.update()
        backup_dir = Path(report.backup_dir)
        self.assertTrue((backup_dir / "steam/12345/shortcuts.vdf").is_file())
        self.assertTrue((backup_dir / "srm/userConfigurations.json").is_file())


class FolderGameTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.env.touch("ps3/God of War 3/PS3_GAME/USRDIR/EBOOT.BIN")
        self.env.touch("ps3/God of War 3/PS3_DISC.SFB")
        self.env.touch("ps3/Uncharted 2/PS3_GAME/USRDIR/EBOOT.BIN")

    def test_folder_without_extension_is_one_game(self) -> None:
        _, library = self.library()
        found = {(g.system, g.rel_path, g.title) for g in library.games if g.system == "ps3"}
        self.assertEqual(found, {("ps3", "God of War 3", "God of War 3"), ("ps3", "Uncharted 2", "Uncharted 2")})
        game = next(g for g in library.games if g.rel_path == "God of War 3")
        self.assertTrue(game.launch_path.endswith("ps3/God of War 3"))

    def test_folder_game_can_be_added(self) -> None:
        parsers = self.env.parsers()
        parsers.append(glob_parser("ps3", "PS3 - RPCS3", "/usr/bin/rpcs3", '--no-gui "${filePath}"', ""))
        self.env.write_parsers(parsers)
        self.add(("ps3", "God of War 3"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertIn("God of War 3", self.env.names())



class NameAndStateTests(Base):
    def test_old_parser_title_gets_new_name_but_same_id(self) -> None:
        self.env.old_install({"snes": [("Super Game", "Super Game (USA).sfc")]})
        parsers = self.env.parsers()
        for parser in parsers:
            if parser["parserId"] == f"{OWNED_PARSER_PREFIX}snes":
                parser["configTitle"] = "ES-DE Favorites Sync - Nintendo SNES"
        self.env.write_parsers(parsers)
        self.add(("snes", "Third.sfc"))
        self.update()
        owned = next(p for p in self.env.parsers() if p["parserId"] == f"{OWNED_PARSER_PREFIX}snes")
        self.assertEqual(owned["configTitle"], "SRM Sync - Nintendo SNES")

    def test_state_moves_from_old_name(self) -> None:
        old = self.env.home / ".local/state/emudeck-favorites-sync"
        old.mkdir(parents=True)
        (old / "games.json").write_text('{"version": 1, "games": []}', encoding="utf-8")
        config = self.env.config()
        self.assertEqual(config.state_dir, self.env.state)
        self.assertTrue((self.env.state / "games.json").is_file())
        self.assertFalse(old.exists())


class RemovalShowsInRomsTests(Base):
    def test_game_moved_out_is_shown_in_roms(self) -> None:
        parsers = self.env.parsers()
        parsers.append(glob_parser("gc", "Nintendo GameCube - Dolphin", "/usr/bin/dolphin-emu", '-e "${filePath}"', ".iso|.rvz"))
        self.env.write_parsers(parsers)
        self.env.old_install({"gc": [("Spyro", "Cube Game.iso")]})
        config, library = self.library()
        records = load_games(config, library)
        game = next(g for g in library.games if g.system == "gc")
        self.assertTrue(already_in_srm(records, game))
        move_out_of_srm(records, records[0].id)
        self.assertFalse(already_in_srm(records, game))
        self.assertIs(RecordIndex(records).find(game, {PENDING_REMOVE}), records[0])

    def test_stale_id_is_relinked_to_the_rom_file(self) -> None:
        parsers = self.env.parsers()
        parsers.append(glob_parser("gc", "Nintendo GameCube - Dolphin", "/usr/bin/dolphin-emu", '-e "${filePath}"', ".iso|.rvz"))
        self.env.write_parsers(parsers)
        self.env.old_install({"gc": [("Spyro", "Cube Game.iso")]})
        config, library = self.library()
        records = load_games(config, library)
        records[0].id = "orphan:stale"
        records[0].rel_path = ""
        save_games(config, records)
        records = load_games(config, library)
        self.assertEqual(records[0].id, logical_id("gc", "Cube Game.iso"))
        self.assertEqual(records[0].rel_path, "Cube Game.iso")


class AlreadyInSrmTests(unittest.TestCase):
    def test_matches_by_launch_path_even_if_id_differs(self) -> None:
        from srm_sync.games import GameRecord

        game = RomGame(id="fresh-id", system="snes", rel_path="Game (Europe).sfc", title="Game",
                       launch_path="/roms/snes/Game (Europe).sfc")
        record = GameRecord(id="stale-id", system="snes", rel_path="Game (E).sfc", title="Game", status=APPLIED,
                            entry={"title": "Game", "target": "/x", "launchOptions": '"/roms/snes/Game (Europe).sfc"'})
        self.assertTrue(already_in_srm([record], game))

    def test_no_match_for_different_console_or_path(self) -> None:
        from srm_sync.games import GameRecord

        game = RomGame(id="g", system="snes", rel_path="Other.sfc", title="Other", launch_path="/roms/snes/Other.sfc")
        record = GameRecord(id="r", system="snes", rel_path="Game.sfc", title="Game", status=APPLIED,
                            entry={"title": "Game", "target": "/x", "launchOptions": '"/roms/snes/Game.sfc"'})
        self.assertFalse(already_in_srm([record], game))


class NoParserTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.env.touch("gc/Old Cube.iso")
        self.env.touch("gc/New Cube.rvz")
        parsers = self.env.parsers()
        parsers.append(glob_parser("gc", "Nintendo GameCube - Dolphin", "/usr/bin/dolphin-emu",
                                   '-b -e "${filePath}"', ".iso|.rvz"))
        self.env.write_parsers(parsers)
        self.env.old_install({"gc": [("Old Cube", "Old Cube.iso")]})
        # EmuDeck later changed its parser so we no longer recognise it for "gc".
        parsers = [p for p in self.env.parsers() if not p["parserId"].startswith("emudeck-gc-")]
        self.env.write_parsers(parsers)

    def test_new_game_copies_settings_from_existing_game(self) -> None:
        self.add(("gc", "New Cube.rvz"))
        report = self.update()
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(len(report.added), 1)
        new = next(s for s in self.env.shortcuts() if field(s, "AppName") == "New Cube")
        self.assertEqual(field(new, "Exe"), f'"/usr/bin/dolphin-emu" -b -e "{self.env.roms / "gc/New Cube.rvz"}"')
        self.assertIn("Old Cube", self.env.names())

    def test_global_root_parser_with_folder_in_glob_is_recognised(self) -> None:
        parsers = self.env.parsers()
        parser = glob_parser("gc", "Dolphin", "/usr/bin/dolphin-emu", '"${filePath}"', ".iso|.rvz")
        parser["romDirectory"] = "${romsdirglobal}"
        parser["parserInputs"] = {"glob": "${/}gc${/}**${/}${title}@(.iso|.rvz)"}
        parser["steamCategories"] = ["Dolphin"]
        parsers.append(parser)
        self.env.write_parsers(parsers)
        config = self.env.config()
        from srm_sync.srm import parser_candidates

        candidates = parser_candidates(load_srm(config).dict_parsers, "gc", config.roms_dir)
        self.assertEqual([p["configTitle"] for p, _ in candidates], ["Dolphin"])


class DiscardTests(Base):
    def test_discard_undoes_unsaved_moves(self) -> None:
        self.env.old_install({"snes": [("Super Game", "Super Game (USA).sfc")]})
        self.add(("snes", "Third.sfc"))
        self.remove(("snes", "Super Game (USA).sfc"))
        config, library = self.library()
        records = load_games(config, library)
        self.assertEqual(pending_changes(records), (1, 1))
        discard_changes(records)
        self.assertEqual(pending_changes(records), (0, 0))
        self.assertEqual([(r.rel_path, r.status) for r in records], [("Super Game (USA).sfc", APPLIED)])


class FixTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.env.old_install({"snes": [("Super Game", "Super Game (USA).sfc"), ("Other Game", "Other Game (Europe).smc")]})

    def _switch_snes_core(self) -> None:
        parsers = self.env.parsers()
        for parser in parsers:
            if parser["parserId"].startswith("emudeck-snes-"):
                parser["executableArgs"] = '-L ${racores}${/}bsnes_libretro.so "${filePath}"'
        self.env.write_parsers(parsers)

    def test_fix_updates_launch_options_and_keeps_names(self) -> None:
        config, library = self.library()
        load_games(config, library)
        shortcuts = self.env.shortcuts()
        for item in shortcuts:
            if field(item, "AppName") == "Other Game":
                set_field(item, "AppName", "Eget navn")
        write_shortcuts(self.env.vdf, shortcuts)
        self._switch_snes_core()
        self.add(("snes", "Third.sfc"))  # pending, must not be added by Fiks
        report = self.update(FIX)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(len(report.fixed), 2)
        self.assertEqual(self.env.names(), ["Eget navn", "Firefox", "Super Game"])
        for item in self.env.shortcuts():
            if field(item, "AppName") != "Firefox":
                self.assertIn("bsnes_libretro.so", field(item, "Exe"))
        records = load_games(*self.library())
        self.assertEqual(sorted(r.status for r in records), [APPLIED, APPLIED, PENDING_ADD])

    def test_fix_without_changes_keeps_appids(self) -> None:
        before = {field(s, "AppName"): field(s, "appid") for s in self.env.shortcuts()}
        report = self.update(FIX)
        self.assertEqual(report.fixed, [])
        after = {field(s, "AppName"): field(s, "appid") for s in self.env.shortcuts()}
        self.assertEqual(before, after)

    def test_parser_preference_is_used(self) -> None:
        parsers = self.env.parsers()
        parsers.append(glob_parser("snes", "Nintendo SNES - bsnes standalone", "/usr/bin/bsnes", '"${filePath}"', ".sfc|.smc"))
        self.env.write_parsers(parsers)
        config = self.env.config()
        save_parser_preference(config, "snes", "bsnes standalone")
        self.update(FIX)
        exes = {field(s, "Exe") for s in self.env.shortcuts() if field(s, "AppName") != "Firefox"}
        self.assertTrue(all(exe.startswith('"/usr/bin/bsnes"') for exe in exes), exes)


class EntryTests(unittest.TestCase):
    def test_ps2_m3u_is_refused(self) -> None:
        parser = glob_parser("ps2", "PCSX2", "/usr/bin/pcsx2", '"${filePath}"', ".chd")
        result = build_entry("Big", "/roms/ps2/Big.m3u/Big.m3u", "ps2", parser, {})
        self.assertIsNone(result.entry)
        self.assertIn("m3u", result.problem)

    def test_unresolved_emulator_is_refused(self) -> None:
        parser = glob_parser("gba", "mGBA", "${retroarchpath}", '"${filePath}"', ".gba")
        result = build_entry("Mini", "/roms/gba/Mini.gba", "gba", parser, {"retroarchPath": ""})
        self.assertIsNone(result.entry)
        self.assertIn("retroarchPath", result.problem)

    def test_entry_resolves_variables(self) -> None:
        parser = glob_parser("snes", "Snes9x", "${retroarchpath}", '-L ${racores}${/}snes9x_libretro.so "${filePath}"', ".sfc")
        result = build_entry("Game", "/roms/snes/Game.sfc", "snes", parser,
                             {"retroarchPath": "/usr/bin/retroarch", "raCoresDirectory": "/cores"})
        self.assertEqual(result.entry, {
            "title": "Game", "target": "/usr/bin/retroarch", "startIn": "",
            "launchOptions": '-L /cores/snes9x_libretro.so "/roms/snes/Game.sfc"', "appendArgsToExecutable": True,
        })


class VdfTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shortcuts.vdf"
            shortcuts = [{"appid": 3000000000, "AppName": "Ø Spill", "Exe": '"/x"', "tags": {"0": "A", "1": "B"}}]
            write_shortcuts(path, shortcuts)
            self.assertEqual(read_shortcuts(path), shortcuts)
            self.assertTrue(path.read_bytes().endswith(b"\x08\x08"))


class CliTests(unittest.TestCase):
    def test_legacy_command_explains_restart(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["autosync-now", "--summary"])
        self.assertEqual(code, 0)
        self.assertIn("start programmet på nytt", output.getvalue())


if __name__ == "__main__":
    unittest.main()
