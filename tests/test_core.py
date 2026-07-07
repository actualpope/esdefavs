from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from emudeck_favorites_sync.autosync import (
    autosync_once,
    autosync_status,
    esde_closed,
    favorite_signature,
    save_autosync_state,
    save_last_srm_entries,
)
from emudeck_favorites_sync.config import discover_config
from emudeck_favorites_sync.cli import main as cli_main
from emudeck_favorites_sync.models import Diagnostic, GameEntry, Manifest, SystemHealth
from emudeck_favorites_sync.planner import build_plan
from emudeck_favorites_sync.scanner import scan
from emudeck_favorites_sync.srm_apply import stage_apply
from emudeck_favorites_sync.srm_cli import SrmCliResult, find_srm_appimage, set_srm_app_path
from emudeck_favorites_sync.state import load_manifest, save_manifest_atomic
from emudeck_favorites_sync.srm_preview import build_srm_preview
from emudeck_favorites_sync.steam_shortcuts import import_to_steam, manual_entries, read_shortcuts, remove_stale_shortcuts, write_shortcuts


def gamelist(*games: str) -> str:
    return '<?xml version="1.0"?><gameList>' + "".join(games) + "</gameList>"


def game(path: str, name: str = "Game", favorite: str | None = "true", extra: str = "") -> str:
    favorite_xml = f"<favorite>{favorite}</favorite>" if favorite is not None else ""
    return f"<game><path>{path}</path><name>{name}</name>{favorite_xml}{extra}</game>"


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.esde = self.home / "ES-DE"
        self.gamelists = self.esde / "gamelists"
        self.roms = self.home / "Emulation/roms"
        self.state = self.home / "state"
        self.gamelists.mkdir(parents=True)
        self.roms.mkdir(parents=True)
        (self.esde / "es_settings.xml").write_text(
            f'<settings><string name="ROMDirectory" value="{self.roms}" />'
            '<string name="SaveGamelistsMode" value="always" /></settings>', encoding="utf-8"
        )

    def close(self) -> None:
        self.temp.cleanup()

    def add_system(self, name: str, xml: str, files: tuple[str, ...] = ()) -> None:
        system_gamelist = self.gamelists / name
        system_roms = self.roms / name
        system_gamelist.mkdir(parents=True)
        system_roms.mkdir(parents=True)
        (system_gamelist / "gamelist.xml").write_text(xml, encoding="utf-8")
        for relative in files:
            target = system_roms / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"rom")

    def config(self):
        return discover_config(
            esde_override=str(self.esde), roms_override=str(self.roms),
            state_override=str(self.state), home_override=str(self.home)
        )

    def add_steam_user(self, user: str = "123456") -> Path:
        config_dir = self.home / ".local/share/Steam/userdata" / user / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = Fixture()

    def tearDown(self) -> None:
        self.fx.close()

    def test_child_favorite_is_included(self) -> None:
        self.fx.add_system("ps2", gamelist(game("./Title.chd", "Title")), ("Title.chd",))
        result = scan(self.fx.config())
        self.assertEqual([entry.title for entry in result.entries], ["Title"])
        self.assertTrue(result.systems["ps2"].removal_safe)

    def test_false_and_missing_favorite_are_excluded(self) -> None:
        self.fx.add_system("ps2", gamelist(
            game("./One.chd", favorite="false"), game("./Two.chd", favorite=None)
        ), ("One.chd", "Two.chd"))
        self.assertEqual(scan(self.fx.config()).entries, [])

    def test_attribute_favorite_is_accepted_with_warning(self) -> None:
        xml = '<gameList><game favorite="true"><path>./One.chd</path><name>One</name></game></gameList>'
        self.fx.add_system("ps2", xml, ("One.chd",))
        result = scan(self.fx.config())
        self.assertEqual(len(result.entries), 1)
        self.assertIn("LEGACY_FAVORITE_ATTRIBUTE", {item.code for item in result.diagnostics})

    def test_subdirectories_are_preserved(self) -> None:
        self.fx.add_system("c64", gamelist(game("./cartridge/Popeye.crt")), ("cartridge/Popeye.crt",))
        entry = scan(self.fx.config()).entries[0]
        self.assertEqual(entry.relative_rom_path, "cartridge/Popeye.crt")

    def test_same_title_in_two_systems_has_different_id(self) -> None:
        for system in ("ps2", "gc"):
            self.fx.add_system(system, gamelist(game("./Game.iso")), ("Game.iso",))
        entries = scan(self.fx.config()).entries
        self.assertNotEqual(entries[0].id, entries[1].id)

    def test_missing_rom_blocks_removals(self) -> None:
        self.fx.add_system("ps2", gamelist(game("./Missing.chd")))
        result = scan(self.fx.config())
        self.assertFalse(result.systems["ps2"].removal_safe)
        self.assertIn("MISSING_ROM", {item.code for item in result.diagnostics})

    def test_malformed_xml_is_isolated(self) -> None:
        self.fx.add_system("bad", "<gameList><game>")
        self.fx.add_system("good", gamelist(game("./Good.rom", "Good")), ("Good.rom",))
        result = scan(self.fx.config())
        self.assertEqual([entry.title for entry in result.entries], ["Good"])
        self.assertFalse(result.systems["bad"].removal_safe)

    def test_junk_after_document_element_is_recovered(self) -> None:
        xml = (
            '<?xml version="1.0"?><gameList>'
            + game("./One.rom", "One")
            + "</gameList>"
            + '<gameList>'
            + game("./Two.rom", "Two")
            + "</gameList>"
        )
        self.fx.add_system("gba", xml, ("One.rom", "Two.rom"))
        result = scan(self.fx.config())
        self.assertEqual([entry.title for entry in result.entries], ["One", "Two"])
        self.assertTrue(result.systems["gba"].removal_safe)
        self.assertIn("RECOVERED_XML_FRAGMENT", {item.code for item in result.diagnostics})

    def test_top_level_game_fragment_is_recovered(self) -> None:
        xml = game("./One.rom", "One") + game("./Two.rom", "Two")
        self.fx.add_system("gba", xml, ("One.rom", "Two.rom"))
        result = scan(self.fx.config())
        self.assertEqual([entry.title for entry in result.entries], ["One", "Two"])
        self.assertTrue(result.systems["gba"].removal_safe)

    def test_cleanup_is_ignored(self) -> None:
        cleanup = self.gamelists_path("CLEANUP")
        cleanup.mkdir(parents=True)
        (cleanup / "gamelist.xml").write_text("<broken", encoding="utf-8")
        result = scan(self.fx.config())
        self.assertNotIn("CLEANUP", result.systems)

    def gamelists_path(self, name: str) -> Path:
        return self.fx.gamelists / name

    def test_traversal_is_rejected(self) -> None:
        self.fx.add_system("ps2", gamelist(game("../../outside.rom")))
        result = scan(self.fx.config())
        self.assertEqual(result.entries, [])
        self.assertIn("UNSAFE_PATH", {item.code for item in result.diagnostics})

    def test_unicode_and_xml_entities(self) -> None:
        self.fx.add_system("ps2", gamelist(game("./R&amp;D.chd", "Pokémon Æ")), ("R&D.chd",))
        result = scan(self.fx.config())
        self.assertEqual(result.entries[0].title, "Pokémon Æ")
        self.assertTrue(result.entries[0].resolved_rom_path.endswith("R&D.chd"))

    def test_alternative_emulator_is_captured(self) -> None:
        self.fx.add_system("ps2", gamelist(game(
            "./Game.chd", extra="<altemulator>PCSX2 Legacy</altemulator>"
        )), ("Game.chd",))
        self.assertEqual(scan(self.fx.config()).entries[0].alternative_emulator, "PCSX2 Legacy")

    def test_srm_preview_matches_existing_parser(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        config = self.fx.config()
        preview = build_srm_preview(config, scan(config))
        self.assertEqual(len(preview["entries"]), 1)
        self.assertEqual(preview["unmatched"], [])
        self.assertIn("Game.zip", preview["entries"][0]["launch_options"])
        self.assertIn("SRM variables", preview["entries"][0]["warning"])

    def test_srm_preview_matches_wiiu_nested_roms_parser(self) -> None:
        self.fx.add_system("wiiu", gamelist(game("./roms/Game.wua", "Game")), ("roms/Game.wua",))
        parser_dir = self.fx.home / ".config/steam-rom-manager/userData"
        parser_dir.mkdir(parents=True, exist_ok=True)
        (parser_dir / "userConfigurations.json").write_text(json.dumps([{
            "configTitle": "Nintendo Wii U - Cemu (.wud, .wux, .wua)",
            "parserType": "Glob",
            "parserId": "source-wiiu",
            "disabled": True,
            "steamDirectory": "${steamdirglobal}",
            "romDirectory": "${romsdirglobal}/wiiu/roms/",
            "steamCategories": ["Nintendo Wii U - Cemu Native"],
            "imageProviders": ["sgdb", "steamCDN"],
            "onlineImageQueries": ["${fuzzyTitle}"],
            "userAccounts": {"specifiedAccounts": ["Global"]},
            "controllers": {},
            "steamInputEnabled": "1",
            "executable": {
                "path": "/Emulation/tools/launchers/cemu.sh",
                "appendArgsToExecutable": False,
            },
            "executableArgs": "vblank_mode=0 %command% -f -g \"${filePath}\"",
            "startInDirectory": "",
            "parserInputs": {"glob": "**/${title}@(.wua|.WUA|.wud|.WUD|.wux|.WUX)"},
        }]), encoding="utf-8")
        config = self.fx.config()
        preview = build_srm_preview(config, scan(config))
        self.assertEqual(preview["unmatched"], [])
        self.assertEqual(preview["entries"][0]["parser_title"], "Nintendo Wii U - Cemu (.wud, .wux, .wua)")

    def add_srm_gba_parser(self) -> Path:
        parser_dir = self.fx.home / ".config/steam-rom-manager/userData"
        parser_dir.mkdir(parents=True, exist_ok=True)
        (parser_dir / "userConfigurations.json").write_text(json.dumps([{
            "configTitle": "Nintendo Game Boy Advance - RetroArch mGBA",
            "parserType": "Glob",
            "parserId": "source-gba",
            "disabled": True,
            "steamDirectory": "${steamdirglobal}",
            "romDirectory": "${romsdirglobal}",
            "steamCategories": ["Nintendo Game Boy Advance"],
            "imageProviders": ["sgdb", "steamCDN"],
            "onlineImageQueries": ["${fuzzyTitle}"],
            "userAccounts": {"specifiedAccounts": ["Global"]},
            "controllers": {},
            "steamInputEnabled": "1",
            "executable": {
                "path": "${retroarchpath}",
                "appendArgsToExecutable": True,
            },
            "executableArgs": "-L ${racores}${/}mgba_libretro.${os:linux|so} \"${filePath}\"",
            "startInDirectory": "",
            "parserInputs": {
                "glob": "{gba/**/!(homebrew),gba}/${title}@(.7z|.7Z|.gba|.GBA|.zip|.ZIP)"
            },
        }]), encoding="utf-8")
        (parser_dir / "userSettings.json").write_text(json.dumps({
            "previewSettings": {"deleteDisabledShortcuts": False},
            "environmentVariables": {
                "steamDirectory": str(self.fx.home / ".steam/steam"),
                "romsDirectory": str(self.fx.roms),
                "retroarchPath": "/usr/bin/retroarch",
                "raCoresDirectory": "/usr/lib/libretro",
                "localImagesDirectory": str(self.fx.home / "images"),
                "userAccounts": ["Global"],
            },
        }), encoding="utf-8")
        return parser_dir

    def test_apply_dry_run_writes_nothing(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        config = self.fx.config()
        result = stage_apply(config, scan(config), dry_run=True, steam_running=False)
        self.assertTrue(result.ok)
        self.assertFalse(result.written)
        self.assertFalse((parser_dir / "manualManifests").exists())

    def test_apply_confirm_writes_owned_manual_parser_and_manifest(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        config = self.fx.config()
        result = stage_apply(config, scan(config), dry_run=False, steam_running=False)
        self.assertTrue(result.ok)
        self.assertTrue(result.written)
        configs = json.loads((parser_dir / "userConfigurations.json").read_text(encoding="utf-8"))
        owned = [item for item in configs if item["parserId"] == "emudeck-favorites-sync:gba"]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["parserType"], "Manual")
        self.assertEqual(owned[0]["steamCategories"], ["ES-DE Favorites", "Nintendo Game Boy Advance"])
        manifest_path = parser_dir / "manualManifests/emudeck-favorites-sync/gba/favorites.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest[0]["target"], "/usr/bin/retroarch")
        self.assertIn("/usr/lib/libretro/mgba_libretro.so", manifest[0]["launchOptions"])

    def test_apply_preserves_owned_parser_when_system_has_no_current_favorites(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        config = self.fx.config()
        first = stage_apply(config, scan(config), dry_run=False, steam_running=False)
        self.assertTrue(first.ok)

        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./Game.zip", "Game", favorite="false")), encoding="utf-8"
        )
        second = stage_apply(config, scan(config), dry_run=False, steam_running=False)
        self.assertTrue(second.ok)
        configs = json.loads((parser_dir / "userConfigurations.json").read_text(encoding="utf-8"))
        owned = [item for item in configs if item["parserId"] == "emudeck-favorites-sync:gba"]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["parserType"], "Manual")
        manifest_path = parser_dir / "manualManifests/emudeck-favorites-sync/gba/favorites.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest, [])

    def test_apply_keeps_shared_esde_favorites_collection_on_preserved_parser(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game", favorite="false")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        configs = json.loads((parser_dir / "userConfigurations.json").read_text(encoding="utf-8"))
        configs.append({
            "configTitle": "ES-DE Favorites Sync - Nintendo Game Boy Advance",
            "parserType": "Manual",
            "parserId": "emudeck-favorites-sync:gba",
            "disabled": False,
            "steamDirectory": "${steamdirglobal}",
            "steamCategories": ["ES-DE Favorites", "Nintendo Game Boy Advance"],
            "parserInputs": {"manualManifests": str(parser_dir / "manualManifests/emudeck-favorites-sync/gba")},
        })
        (parser_dir / "userConfigurations.json").write_text(json.dumps(configs), encoding="utf-8")
        result = stage_apply(self.fx.config(), scan(self.fx.config()), dry_run=False, steam_running=False)
        self.assertTrue(result.ok)
        updated = json.loads((parser_dir / "userConfigurations.json").read_text(encoding="utf-8"))
        owned = [item for item in updated if item["parserId"] == "emudeck-favorites-sync:gba"]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["steamCategories"], ["ES-DE Favorites", "Nintendo Game Boy Advance"])
        manifest_path = parser_dir / "manualManifests/emudeck-favorites-sync/gba/favorites.json"
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), [])

    def test_apply_confirm_blocks_when_steam_runs(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        result = stage_apply(self.fx.config(), scan(self.fx.config()), dry_run=False, steam_running=True)
        self.assertFalse(result.ok)
        self.assertIn("STEAM_RUNNING", {item.code for item in result.diagnostics})

    def test_apply_allows_warning_when_unincluded_missing_rom_exists(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.fx.add_system("wiiu", gamelist(game("./Missing.wua", "Missing")))
        self.add_srm_gba_parser()
        result = stage_apply(self.fx.config(), scan(self.fx.config()), dry_run=True, steam_running=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.entries_written, 1)
        self.assertIn("REMOVAL_NOT_SAFE", {item.code for item in result.diagnostics})
        self.assertNotIn("SCAN_NOT_SAFE", {item.code for item in result.diagnostics})

    def test_favorite_signature_changes_when_favorite_changes(self) -> None:
        self.fx.add_system("gba", gamelist(game("./One.zip", "One")), ("One.zip", "Two.zip"))
        config = self.fx.config()
        before = favorite_signature(scan(config))
        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./Two.zip", "Two")), encoding="utf-8"
        )
        after = favorite_signature(scan(config))
        self.assertNotEqual(before, after)

    def test_autosync_status_lists_current_favorites(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        data = autosync_status(self.fx.config())
        self.assertEqual(data["current_favorites_count"], 1)
        self.assertEqual(data["favorites"][0]["title"], "Game")

    def test_autosync_waits_for_steam_then_stages_when_closed(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        self.fx.add_steam_user()
        config = self.fx.config()
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": True}}):
            waiting = autosync_once(config)
        self.assertTrue(waiting["changed"])
        self.assertFalse(waiting["synced"])
        self.assertEqual(waiting["reason"], "Steam is running")
        self.assertTrue(waiting["state"]["pending"])

        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=SrmCliResult(ok=True, attempted=True)), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=SrmCliResult(ok=True, attempted=True)):
            synced = autosync_once(config)
        self.assertTrue(synced["synced"])
        self.assertFalse(synced["state"]["pending"])
        self.assertEqual(synced["state"]["last_result"], "synced-and-srm-added")
        manifest_path = parser_dir / "manualManifests/emudeck-favorites-sync/gba/favorites.json"
        self.assertTrue(manifest_path.is_file())
        self.assertIsNone(synced["steam_import"])
        applied = load_manifest(config.state_dir / "applied.json")
        plan = build_plan(scan(config), applied)
        self.assertEqual(plan.additions, [])
        self.assertEqual(plan.removals, [])

    def test_autosync_stays_pending_if_srm_add_fails(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        config = self.fx.config()
        failed_srm = SrmCliResult(ok=False, attempted=True)
        failed_srm.diagnostics.append(Diagnostic("error", "SRM_ADD_FAILED", "nope"))
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=SrmCliResult(ok=True, attempted=True)), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=failed_srm):
            synced = autosync_once(config)
        self.assertTrue(synced["synced"])
        self.assertTrue(synced["state"]["pending"])
        self.assertTrue(synced["state"]["srm_add_pending"])
        self.assertEqual(synced["state"]["last_result"], "staged-srm-add-blocked")

    def test_autosync_continues_if_srm_remove_fails(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        config = self.fx.config()
        failed_remove = SrmCliResult(ok=False, attempted=True)
        failed_remove.diagnostics.append(Diagnostic("error", "SRM_REMOVE_FAILED", "nope"))
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=failed_remove), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=SrmCliResult(ok=True, attempted=True)):
            synced = autosync_once(config)
        self.assertTrue(synced["synced"])
        self.assertFalse(synced["state"]["pending"])
        self.assertFalse(synced["state"]["srm_remove_pending"])
        self.assertEqual(synced["state"]["last_result"], "synced-and-srm-added")

    def test_forced_autosync_reconciles_even_without_favorite_change(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        config = self.fx.config()
        manifest = scan(config)
        save_autosync_state(
            config,
            {
                "enabled": False,
                "pending": False,
                "last_signature": favorite_signature(manifest),
                "favorites": [],
            },
        )
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=SrmCliResult(ok=True, attempted=True)), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=SrmCliResult(ok=True, attempted=True)) as add_mock:
            no_change = autosync_once(config)
            forced = autosync_once(config, force=True)
        self.assertFalse(no_change["synced"])
        self.assertEqual(no_change["reason"], "no pending changes")
        self.assertFalse(no_change["forced"])
        self.assertTrue(forced["forced"])
        self.assertFalse(forced["changed"])
        self.assertTrue(forced["synced"])
        self.assertFalse(forced["state"]["pending"])
        add_mock.assert_called_once()

    def test_esde_closed_marks_last_esde_close_and_runs_one_check(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        config = self.fx.config()
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": True}}):
            result = esde_closed(config)
        self.assertEqual(result["reason"], "Steam is running")
        self.assertTrue(result["state"]["pending"])
        self.assertIsNotNone(result["state"]["last_esde_closed_at"])

    def test_autosync_reconciles_when_steam_is_missing_current_favorites(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        self.add_srm_gba_parser()
        self.fx.add_steam_user()
        config = self.fx.config()
        manifest = scan(config)
        stage_apply(config, manifest, dry_run=False, steam_running=False)
        save_autosync_state(
            config,
            {
                "enabled": True,
                "pending": False,
                "last_signature": favorite_signature(manifest),
                "favorites": [],
            },
        )
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=SrmCliResult(ok=True, attempted=True)), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=SrmCliResult(ok=True, attempted=True)) as add_mock:
            synced = autosync_once(config)
        self.assertFalse(synced["changed"])
        self.assertTrue(synced["synced"])
        self.assertEqual(synced["steam_library"]["needs_reconcile"], True)
        self.assertEqual(len(synced["steam_library"]["missing"]), 1)
        add_mock.assert_called_once()

    def test_autosync_reconciles_when_steam_has_stale_previous_favorites(self) -> None:
        self.fx.add_system(
            "gba",
            gamelist(game("./One.zip", "One"), game("./Two.zip", "Two")),
            ("One.zip", "Two.zip"),
        )
        self.add_srm_gba_parser()
        steam_config_dir = self.fx.add_steam_user()
        config = self.fx.config()
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        previous = manual_entries(config)
        import_to_steam(config, steam_running=False)

        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./One.zip", "One")),
            encoding="utf-8",
        )
        current_manifest = scan(config)
        stage_apply(config, current_manifest, dry_run=False, steam_running=False)
        save_last_srm_entries(config, previous)
        save_autosync_state(
            config,
            {
                "enabled": True,
                "pending": False,
                "last_signature": favorite_signature(current_manifest),
                "favorites": [],
            },
        )
        with patch("emudeck_favorites_sync.autosync.collect_compatibility", return_value={"steam": {"running": False}}), \
                patch("emudeck_favorites_sync.autosync.run_srm_remove_owned", return_value=SrmCliResult(ok=True, attempted=True)), \
                patch("emudeck_favorites_sync.autosync.run_srm_add_owned", return_value=SrmCliResult(ok=True, attempted=True)):
            synced = autosync_once(config)
        self.assertFalse(synced["changed"])
        self.assertTrue(synced["synced"])
        self.assertEqual(len(synced["steam_library"]["stale"]), 1)
        shortcuts = read_shortcuts(steam_config_dir / "shortcuts.vdf")
        self.assertEqual([item["AppName"] for item in shortcuts], ["One"])

    def test_stale_cleanup_matches_srm_shortcut_with_different_quoting(self) -> None:
        self.fx.add_system(
            "gba",
            gamelist(game("./One.zip", "One"), game("./Two.zip", "Two")),
            ("One.zip", "Two.zip"),
        )
        self.add_srm_gba_parser()
        steam_config_dir = self.fx.add_steam_user()
        config = self.fx.config()
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        previous = manual_entries(config)
        stale = next(entry for entry in previous if entry["title"] == "Two")
        write_shortcuts(
            steam_config_dir / "shortcuts.vdf",
            [
                {
                    "AppName": stale["title"],
                    "Exe": str(stale["target"]).strip('"'),
                    "StartDir": "",
                    "LaunchOptions": str(stale["launchOptions"]).replace('"', ""),
                    "tags": {"0": "Nintendo Game Boy Advance"},
                }
            ],
        )

        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./One.zip", "One")),
            encoding="utf-8",
        )
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        result = remove_stale_shortcuts(
            config,
            previous_entries=previous,
            current_entries=manual_entries(config),
            steam_running=False,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.removed, 1)
        self.assertEqual(read_shortcuts(steam_config_dir / "shortcuts.vdf"), [])

    def test_srm_appimage_can_be_set_manually(self) -> None:
        app = self.fx.home / "weird/place/Steam ROM Manager 2.AppImage"
        app.parent.mkdir(parents=True)
        app.write_bytes(b"appimage")
        config = self.fx.config()
        set_srm_app_path(config, str(app))
        self.assertEqual(find_srm_appimage(config), app)

    def test_srm_appimage_is_found_in_applications_with_versioned_name(self) -> None:
        app = self.fx.home / "Applications/Steam-ROM-Manager-2.5.38.AppImage"
        app.parent.mkdir(parents=True)
        app.write_bytes(b"appimage")
        self.assertEqual(find_srm_appimage(self.fx.config()), app)

    def test_srm_appimage_is_found_in_emudeck_tools_root(self) -> None:
        app = self.fx.home / "Emulation/tools/Steam-ROM-Manager.AppImage"
        app.parent.mkdir(parents=True)
        app.write_bytes(b"appimage")
        self.assertEqual(find_srm_appimage(self.fx.config()), app)

    def test_steam_import_replaces_only_owned_shortcuts(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip",))
        parser_dir = self.add_srm_gba_parser()
        steam_config_dir = self.fx.add_steam_user()
        config = self.fx.config()
        stage = stage_apply(config, scan(config), dry_run=False, steam_running=False)
        self.assertTrue(stage.ok)
        result = import_to_steam(config, steam_running=False)
        self.assertTrue(result.ok)
        shortcuts = read_shortcuts(steam_config_dir / "shortcuts.vdf")
        self.assertEqual(len(shortcuts), 1)
        self.assertEqual(shortcuts[0]["AppName"], "Game")
        self.assertEqual(shortcuts[0]["tags"]["0"], "ES-DE Favorites Sync")

        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./Other.zip", "Other")), encoding="utf-8"
        )
        (self.fx.roms / "gba/Other.zip").write_bytes(b"rom")
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        result = import_to_steam(config, steam_running=False)
        self.assertTrue(result.ok)
        shortcuts = read_shortcuts(steam_config_dir / "shortcuts.vdf")
        self.assertEqual([item["AppName"] for item in shortcuts], ["Other"])

    def test_stale_shortcut_cleanup_removes_previous_entries_not_current(self) -> None:
        self.fx.add_system("gba", gamelist(game("./Game.zip", "Game")), ("Game.zip", "Other.zip"))
        self.add_srm_gba_parser()
        steam_config_dir = self.fx.add_steam_user()
        config = self.fx.config()
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        previous = manual_entries(config)
        self.assertTrue(import_to_steam(config, steam_running=False).ok)

        (self.fx.gamelists / "gba/gamelist.xml").write_text(
            gamelist(game("./Other.zip", "Other")), encoding="utf-8"
        )
        stage_apply(config, scan(config), dry_run=False, steam_running=False)
        current = manual_entries(config)
        result = remove_stale_shortcuts(config, previous_entries=previous, current_entries=current, steam_running=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.removed, 1)
        shortcuts = read_shortcuts(steam_config_dir / "shortcuts.vdf")
        self.assertEqual([item["AppName"] for item in shortcuts], [])


class StateAndPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def entry(self, path: str = "Game.chd", title: str = "Game") -> GameEntry:
        return GameEntry("sha256:id", "ps2", title, f"./{path}", path, f"/roms/ps2/{path}")

    def manifest(self, entries, safe: bool = True) -> Manifest:
        return Manifest(
            1, "2026-01-01T00:00:00Z", {}, {"removal_safe": safe}, list(entries),
            {"ps2": SystemHealth("ps2", "/gamelist", "/roms/ps2", True, True, 1, len(entries), safe, "healthy" if safe else "broken")}, []
        )

    def test_atomic_state_roundtrip(self) -> None:
        path = self.root / "nested/desired.json"
        save_manifest_atomic(path, self.manifest([self.entry()]))
        loaded = load_manifest(path)
        self.assertEqual(loaded.entries[0].title, "Game")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_first_plan_adds_everything(self) -> None:
        plan = build_plan(self.manifest([self.entry()]), None)
        self.assertEqual(len(plan.additions), 1)

    def test_path_move_is_change(self) -> None:
        before = self.entry()
        after = GameEntry(before.id, before.system, before.title, before.source_path,
                          before.relative_rom_path, "/new/roms/ps2/Game.chd")
        plan = build_plan(self.manifest([after]), self.manifest([before]))
        self.assertEqual(len(plan.changes), 1)
        self.assertFalse(plan.additions or plan.removals)

    def test_unhealthy_system_blocks_removal(self) -> None:
        plan = build_plan(self.manifest([], safe=False), self.manifest([self.entry()]))
        self.assertEqual(len(plan.blocked_removals), 1)
        self.assertEqual(plan.removals, [])

    def test_healthy_unfavorite_allows_removal(self) -> None:
        plan = build_plan(self.manifest([], safe=True), self.manifest([self.entry()]))
        self.assertEqual(len(plan.removals), 1)


class CliTests(unittest.TestCase):
    def test_sync_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["sync", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_autosync_status_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["autosync-status", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_esde_closed_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["esde-closed", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_steam_import_now_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["steam-import-now", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_srm_add_now_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["srm-add-now", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_srm_remove_now_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["srm-remove-now", "--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_set_srm_path_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            cli_main(["set-srm-path", "--help"])
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
