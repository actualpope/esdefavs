"""A small stand-in for the Steam ROM Manager CLI, used by the tests.

It follows the parts of SRM's real behaviour that matter to us (see
src/lib/vdf-manager.ts and vdf-shortcuts-file.ts in steam-rom-manager):

* only enabled parsers take part;
* ``add`` adds new apps, overwrites apps it can find, and deletes apps an enabled
  parser added earlier but no longer lists ("extraneous");
* shortcuts are found by an id computed from their *current* Exe and name, so a
  shortcut renamed in Steam is not found;
* a run with zero apps never finishes (here: exits with code 3).
"""

from __future__ import annotations

import binascii
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emudeck_favorites_sync.steam import field, read_shortcuts, set_field, tags, write_shortcuts  # noqa: E402


def app_key(exe: str, title: str) -> int:
    return (binascii.crc32((exe + title).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def fuzzy(title: str) -> str:
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title).strip() or title


def main() -> int:
    action = sys.argv[1]
    home = Path(os.environ["FAKE_SRM_HOME"])
    user_data = home / ".config/steam-rom-manager/userData"
    with (user_data / "fake-srm-calls.log").open("a", encoding="utf-8") as log:
        parsers = json.loads((user_data / "userConfigurations.json").read_text(encoding="utf-8"))
        enabled = [p for p in parsers if isinstance(p, dict) and not p.get("disabled")]
        log.write(json.dumps({"action": action, "enabled": [p.get("parserId") for p in enabled]}) + "\n")

    apps: dict[int, dict] = {}
    for parser in enabled:
        if parser.get("parserType") != "Manual":
            continue
        directory = Path(parser["parserInputs"]["manualManifests"])
        for manifest in sorted(directory.glob("*.json")):
            for entry in json.loads(manifest.read_text(encoding="utf-8")):
                exe = f'"{entry["target"]}"'
                launch = entry.get("launchOptions", "")
                if entry.get("appendArgsToExecutable"):
                    exe = f"{exe} {launch}".strip()
                    launch = ""
                title = fuzzy(entry["title"])
                apps[app_key(exe, title)] = {
                    "appname": title, "exe": exe, "StartDir": entry.get("startIn", ""),
                    "LaunchOptions": launch, "tags": parser.get("steamCategories", []),
                    "parserId": parser["parserId"],
                }
    if action == "add" and not apps:
        return 3

    added_path = user_data / "fake-added.json"
    added = json.loads(added_path.read_text(encoding="utf-8")) if added_path.is_file() else {}
    enabled_ids = {p.get("parserId") for p in enabled}
    for vdf in sorted((home / ".local/share/Steam/userdata").glob("*/config/shortcuts.vdf")):
        shortcuts = read_shortcuts(vdf)
        index = {app_key(str(field(s, "Exe")), str(field(s, "AppName"))): i for i, s in enumerate(shortcuts)}
        drop: set[int] = set()
        if action == "remove":
            for key in apps:
                if key in index:
                    drop.add(index[key])
                added.pop(str(key), None)
        else:
            for key_text, parser_id in list(added.items()):
                if parser_id in enabled_ids and int(key_text) not in apps:
                    if int(key_text) in index:
                        drop.add(index[int(key_text)])
                    added.pop(key_text)
            for key, app in apps.items():
                added[str(key)] = app["parserId"]
                if key in index:
                    item = shortcuts[index[key]]
                    set_field(item, "appid", key)
                    set_field(item, "AppName", app["appname"])
                    set_field(item, "Exe", app["exe"])
                    set_field(item, "StartDir", app["StartDir"])
                    set_field(item, "LaunchOptions", app["LaunchOptions"])
                    merged = list(dict.fromkeys([*app["tags"], *tags(item)]))
                    set_field(item, "tags", {str(i): t for i, t in enumerate(merged)})
                else:
                    shortcuts.append({
                        "appid": key, "appname": app["appname"], "exe": app["exe"], "StartDir": app["StartDir"],
                        "icon": "", "LaunchOptions": app["LaunchOptions"],
                        "tags": {str(i): t for i, t in enumerate(app["tags"])},
                    })
                    index[key] = len(shortcuts) - 1
        write_shortcuts(vdf, [s for i, s in enumerate(shortcuts) if i not in drop])
    added_path.write_text(json.dumps(added), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
