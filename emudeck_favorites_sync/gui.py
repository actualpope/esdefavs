"""The program window: ROMS on the left, our SRM games on the right."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from . import __version__
from .config import AppConfig, discover_config, set_roms_dir
from .engine import FIX, UPDATE, RunReport, run, save_report
from .games import APPLIED, PENDING_ADD, PENDING_REMOVE, GameRecord, load_games, move_out_of_srm, move_to_srm, save_games, undo_remove
from .library import Library, RomGame, scan_library
from .srm import (
    SrmData,
    describe_systems,
    load_srm_or_none,
    parser_candidates,
    save_parser_preference,
    select_parser_candidate,
    load_parser_preferences,
    set_preview_setting,
)
from .srm_cli import set_srm_app_path, srm_command


TITLE = "EmuDeck Favorites Sync"
PROGRAM_DIR = Path(__file__).resolve().parent.parent


def _sort_key(system: str, name: str) -> tuple[str, str]:
    return system.casefold(), name.casefold()


class App:
    def __init__(self, root: tk.Tk, config: AppConfig) -> None:
        self.root = root
        self.config = config
        self.srm: SrmData | None = None
        self.library = Library(roms_dir=None)
        self.records: list[GameRecord] = []
        self.busy = False
        self.events: queue.Queue[Callable[[], None]] = queue.Queue()
        self.search = tk.StringVar()
        self.status = tk.StringVar()
        self.roms_info = tk.StringVar()
        self.srm_info = tk.StringVar()

        root.title(f"{TITLE} {__version__}")
        root.geometry("1200x740")
        root.minsize(900, 520)
        self._build()
        self.search.trace_add("write", lambda *_: self.refresh())
        root.after(100, self._poll_events)
        root.after(50, self.reload)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Pane.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Big.TButton", font=("TkDefaultFont", 12, "bold"), padding=(18, 8))
        style.configure("Treeview", rowheight=24)

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(2, weight=1)
        outer.rowconfigure(2, weight=1)

        left_head = ttk.Frame(outer)
        left_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(left_head, text="ROMS", style="Pane.TLabel").pack(side="left")
        ttk.Button(left_head, text="Velg rom-mappe …", command=self.choose_roms_dir).pack(side="right")
        ttk.Label(outer, textvariable=self.roms_info, foreground="#666").grid(row=1, column=0, sticky="w")

        right_head = ttk.Frame(outer)
        right_head.grid(row=0, column=2, sticky="ew", pady=(0, 4))
        ttk.Label(right_head, text="SRM", style="Pane.TLabel").pack(side="left")
        ttk.Button(right_head, text="SRM-oppsett …", command=self.open_srm_setup).pack(side="right")
        ttk.Label(outer, textvariable=self.srm_info, foreground="#666").grid(row=1, column=2, sticky="w")

        self.roms_tree = self._tree(outer, column=0)
        self.srm_tree = self._tree(outer, column=2)
        self.roms_tree.bind("<Double-1>", lambda _: self.move_right())
        self.srm_tree.bind("<Double-1>", lambda _: self.move_left())
        self.roms_tree.bind("<Return>", lambda _: self.move_right())
        self.srm_tree.bind("<Return>", lambda _: self.move_left())

        middle = ttk.Frame(outer)
        middle.grid(row=2, column=1, padx=10)
        self.right_button = ttk.Button(middle, text="→", width=4, style="Big.TButton", command=self.move_right)
        self.right_button.pack(pady=6)
        self.left_button = ttk.Button(middle, text="←", width=4, style="Big.TButton", command=self.move_left)
        self.left_button.pack(pady=6)

        search_row = ttk.Frame(outer)
        search_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(search_row, text="Søk:").pack(side="left")
        ttk.Entry(search_row, textvariable=self.search, width=40).pack(side="left", padx=(6, 0))
        ttk.Label(search_row, textvariable=self.status).pack(side="left", padx=(16, 0))

        bottom = ttk.Frame(outer)
        bottom.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.update_button = ttk.Button(bottom, text="Oppdater", style="Big.TButton", command=self.start_update)
        self.update_button.pack(side="left")
        self.fix_button = ttk.Button(bottom, text="Fiks", command=self.start_fix)
        self.fix_button.pack(side="left", padx=(10, 0))
        self.program_button = ttk.Button(bottom, text="Oppdater program", command=self.update_program)
        self.program_button.pack(side="right")

    def _tree(self, parent: ttk.Frame, column: int) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=column, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=("system", "name", "status"), show="headings", selectmode="extended")
        tree.heading("system", text="Konsoll")
        tree.heading("name", text="Spill")
        tree.heading("status", text="")
        tree.column("system", width=90, stretch=False)
        tree.column("name", width=280, minwidth=200, stretch=True)
        tree.column("status", width=120, stretch=False)
        tree.tag_configure("new", foreground="#1a7f37")
        tree.tag_configure("remove", foreground="#b42318")
        tree.tag_configure("warn", foreground="#9a6700")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    # ------------------------------------------------------------------- data

    def reload(self) -> None:
        self.status.set("Leser rom-mappa og SRM …")
        self.root.update_idletasks()
        self.srm = load_srm_or_none(self.config)
        self.library = scan_library(self.config, self.srm)
        first_run = not (self.config.state_dir / "games.json").exists()
        self.records = load_games(self.config, self.library)
        self.refresh()
        if first_run and self.records:
            messagebox.showinfo(
                TITLE,
                f"Fant {len(self.records)} spill som allerede ligger i SRM fra før. "
                "De vises i SRM-lista og blir liggende i Steam som før.",
            )

    def refresh(self) -> None:
        needle = self.search.get().strip().casefold()
        games = self.library.by_id()
        in_srm = {r.id for r in self.records if r.status in {APPLIED, PENDING_ADD}}
        removing = {r.id: r for r in self.records if r.status == PENDING_REMOVE}

        left_rows: list[tuple[str, str, str, str, str]] = []
        for game in self.library.games:
            if game.id in in_srm:
                continue
            status, tag = ("fjernes", "remove") if game.id in removing else ("", "")
            left_rows.append((game.id, game.system, game.filename, status, tag))
        for record in removing.values():
            if record.id not in games:
                left_rows.append((record.id, record.system, record.display_name, "fjernes", "remove"))

        right_rows: list[tuple[str, str, str, str, str]] = []
        for record in self.records:
            if record.status == PENDING_ADD:
                right_rows.append((record.id, record.system, record.display_name, "ny", "new"))
            elif record.status == APPLIED:
                missing = not record.rel_path or record.id not in games
                status, tag = ("rom ikke funnet", "warn") if missing and not self.library.error else ("", "")
                right_rows.append((record.id, record.system, record.display_name, status, tag))

        self._fill(self.roms_tree, left_rows, needle)
        self._fill(self.srm_tree, right_rows, needle)

        systems = len({game.system for game in self.library.games})
        if self.library.error:
            self.roms_info.set(self.library.error)
        else:
            self.roms_info.set(f"{self.config.roms_dir}  ·  {len(self.library.games)} spill i {systems} konsoller")
        in_steam = sum(1 for r in self.records if r.in_steam)
        if self.srm is None:
            self.srm_info.set("Fant ikke SRM-oppsettet. Åpne «SRM-oppsett».")
        else:
            self.srm_info.set(f"{in_steam} spill i Steam via SRM")
        adds = sum(1 for r in self.records if r.status == PENDING_ADD)
        removes = len(removing)
        if not self.busy:
            if adds or removes:
                self.status.set(f"Ikke lagret: {adds} legges til, {removes} fjernes. Trykk «Oppdater».")
            else:
                self.status.set("Ingen ulagrede endringer.")

    def _fill(self, tree: ttk.Treeview, rows: list[tuple[str, str, str, str, str]], needle: str) -> None:
        top = tree.yview()[0]
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        rows.sort(key=lambda row: _sort_key(row[1], row[2]))
        for row_id, system, name, status, tag in rows:
            if needle and needle not in f"{system} {name}".casefold():
                continue
            tree.insert("", "end", iid=row_id, values=(system, name, status), tags=(tag,) if tag else ())
        keep = [item for item in selected if tree.exists(item)]
        if keep:
            tree.selection_set(keep)
        tree.yview_moveto(top)

    # ------------------------------------------------------------------ moving

    def _addable(self, game: RomGame) -> str:
        if self.srm is None:
            return "SRM-oppsettet ble ikke funnet"
        preferences = load_parser_preferences(self.config)
        if select_parser_candidate(
            parser_candidates(self.srm.dict_parsers, game.system, self.config.roms_dir), preferences.get(game.system)
        ) is None:
            return f"ingen SRM-parser for «{game.system}»"
        return ""

    def move_right(self) -> None:
        if self.busy:
            return
        games = self.library.by_id()
        refused: list[str] = []
        for item in self.roms_tree.selection():
            game = games.get(item)
            if game is None:
                undo_remove(self.records, item)
                continue
            if any(r.id == item and r.status == PENDING_REMOVE for r in self.records):
                undo_remove(self.records, item)
                continue
            reason = self._addable(game)
            if reason:
                refused.append(f"{game.system}/{game.filename}: {reason}")
                continue
            move_to_srm(self.records, game)
        self._save_and_refresh()
        if refused:
            messagebox.showwarning(TITLE, "Kan ikke legges til:\n\n" + "\n".join(refused[:20]))

    def move_left(self) -> None:
        if self.busy:
            return
        for item in self.srm_tree.selection():
            move_out_of_srm(self.records, item)
        self._save_and_refresh()

    def _save_and_refresh(self) -> None:
        save_games(self.config, self.records)
        self.refresh()

    # ------------------------------------------------------------- operations

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.update_button, self.fix_button, self.program_button, self.right_button, self.left_button):
            button.configure(state=state)
        self.root.configure(cursor="watch" if busy else "")

    def _poll_events(self) -> None:
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _in_background(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        self._set_busy(True)

        def target() -> None:
            try:
                result = work()
            except Exception as error:  # noqa: BLE001
                result = error
            self.events.put(lambda: self._finish(done, result))

        threading.Thread(target=target, daemon=True).start()

    def _finish(self, done: Callable[[object], None], result: object) -> None:
        self._set_busy(False)
        done(result)

    def _progress(self, text: str) -> None:
        self.events.put(lambda: self.status.set(text))

    def start_update(self) -> None:
        adds = sum(1 for r in self.records if r.status == PENDING_ADD)
        removes = sum(1 for r in self.records if r.status == PENDING_REMOVE)
        if not adds and not removes:
            messagebox.showinfo(TITLE, "Ingen endringer å lagre. Flytt spill mellom listene først.")
            return
        text = f"{adds} spill legges til og {removes} fjernes.\n\nSteam lukkes mens dette pågår, og startes igjen etterpå."
        if removes:
            text += "\n\nSpill som fjernes forsvinner fra Steam, sammen med spilletid og bilder der."
        if not messagebox.askokcancel(TITLE, text):
            return
        self._run_engine(UPDATE)

    def start_fix(self) -> None:
        count = sum(1 for r in self.records if r.in_steam)
        if not count:
            messagebox.showinfo(TITLE, "Det er ingen spill i SRM å fikse ennå.")
            return
        if not messagebox.askokcancel(
            TITLE,
            f"Fiks oppdaterer startinnstillingene (emulator og argumenter) for alle {count} spill "
            "med dagens oppsett. Ingen spill legges til eller fjernes, og navn du har endret i Steam beholdes.\n\n"
            "Hvis et spill får ny emulator, kan Steam se det som et nytt spill (spilletid starter på nytt).\n\n"
            "Steam lukkes mens dette pågår.",
        ):
            return
        self._run_engine(FIX)

    def _run_engine(self, mode: str) -> None:
        def done(result: object) -> None:
            self.reload()
            if isinstance(result, RunReport):
                if result.error or not result.ok:
                    messagebox.showwarning(TITLE, result.summary())
                else:
                    messagebox.showinfo(TITLE, result.summary())
            else:
                messagebox.showerror(TITLE, f"Uventet feil: {result!r}")

        self._in_background(lambda: run(self.config, mode, progress=self._progress), done)

    # ------------------------------------------------------------ ROM folder

    def choose_roms_dir(self) -> None:
        if self.busy:
            return
        start = str(self.config.roms_dir) if self.config.roms_dir and self.config.roms_dir.is_dir() else str(self.config.home)
        chosen = filedialog.askdirectory(title="Velg rom-mappa (den med én mappe per konsoll)", initialdir=start)
        if not chosen:
            return
        set_roms_dir(self.config, chosen)
        self.reload()

    # --------------------------------------------------------- program update

    def update_program(self) -> None:
        script = PROGRAM_DIR / "update.sh"
        if not script.is_file():
            messagebox.showerror(TITLE, f"Fant ikke oppdateringsskriptet:\n{script}")
            return
        if not messagebox.askokcancel(TITLE, "Laste ned og installere siste versjon fra GitHub?"):
            return

        def work() -> object:
            completed = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=600)
            return completed

        def done(result: object) -> None:
            if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
                if messagebox.askyesno(TITLE, "Programmet er oppdatert. Starte det på nytt nå?"):
                    os.execv(sys.executable, [sys.executable, "-m", "emudeck_favorites_sync.gui"])
                return
            output = ""
            if isinstance(result, subprocess.CompletedProcess):
                output = (result.stdout + "\n" + result.stderr).strip()
            else:
                output = repr(result)
            TextWindow(self.root, "Oppdatering feilet", output)

        self._in_background(work, done)

    # ------------------------------------------------------------- SRM setup

    def open_srm_setup(self) -> None:
        if self.busy:
            return
        SrmSetup(self)


class TextWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, text: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("800x500")
        box = tk.Text(self, wrap="word")
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)
        ttk.Button(self, text="Lukk", command=self.destroy).pack(pady=6)


class SrmSetup(tk.Toplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app.root)
        self.app = app
        self.title("SRM-oppsett")
        self.geometry("1150x660")
        self.transient(app.root)
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.render()

    def render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        config = self.app.config
        srm = load_srm_or_none(config)
        self.app.srm = srm
        body = self.body

        ttk.Label(body, text="Steam ROM Manager", style="Pane.TLabel").pack(anchor="w")
        command, label = srm_command(config)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(4, 10))
        ttk.Label(row, text=label if command else "Fant ikke SRM-programmet.",
                  foreground="#1a7f37" if command else "#b42318").pack(side="left")
        ttk.Button(row, text="Velg SRM AppImage …", command=self.choose_appimage).pack(side="right")

        if srm is None:
            ttk.Label(
                body,
                text="Fant ikke SRM sitt oppsett (~/.config/steam-rom-manager/userData/userConfigurations.json).\n"
                     "Åpne Steam ROM Manager via EmuDeck én gang, og prøv igjen.",
                foreground="#b42318",
            ).pack(anchor="w")
            self._footer()
            return

        checks = ttk.Frame(body)
        checks.pack(fill="x", pady=(0, 10))
        delete_disabled = srm.delete_disabled_shortcuts is True
        self._check_row(
            checks,
            "«Delete disabled shortcuts» er av" if not delete_disabled else "«Delete disabled shortcuts» er PÅ – må være av",
            ok=not delete_disabled,
            fix=("Slå av", lambda: self.set_setting("deleteDisabledShortcuts", False)) if delete_disabled else None,
        )
        keep_images = srm.retrieve_current_steam_images is not False
        self._check_row(
            checks,
            "Eksisterende bilder i Steam beholdes" if keep_images else "SRM henter nye bilder for alle spill hver gang",
            ok=keep_images,
            fix=None if keep_images else ("Slå på", lambda: self.set_setting("retrieveCurrentSteamImages", True)),
        )
        environment = srm.environment
        for key in ("retroarchPath", "raCoresDirectory"):
            value = environment.get(key, "")
            self._check_row(checks, f"{key}: {value or '(tom – RetroArch-spill vil ikke starte)'}", ok=bool(value))

        ttk.Label(body, text="Emulator per konsoll", style="Pane.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(
            body,
            text="Gjelder nye spill. Trykk «Fiks» etterpå hvis spill som allerede er lagt til skal bytte emulator.",
            foreground="#666",
        ).pack(anchor="w")

        container = ttk.Frame(body)
        container.pack(fill="both", expand=True, pady=(6, 0))
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        grid = ttk.Frame(canvas)
        grid.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=grid, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for column, heading in enumerate(("Konsoll", "SRM-parser (kilde)", "Emulator", "Vår parser i SRM")):
            ttk.Label(grid, text=heading, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=column, sticky="w", padx=4)
        systems = sorted(
            {game.system for game in self.app.library.games} | {r.system for r in self.app.records},
            key=str.casefold,
        )
        infos = describe_systems(config, srm, systems)
        for index, info in enumerate(infos, start=1):
            ttk.Label(grid, text=info.system).grid(row=index, column=0, sticky="w", padx=4, pady=2)
            if info.candidates:
                value = tk.StringVar(value=info.chosen)
                box = ttk.Combobox(grid, textvariable=value, values=info.candidates, state="readonly", width=38)
                box.grid(row=index, column=1, sticky="w", padx=4)
                box.bind("<<ComboboxSelected>>", lambda _event, s=info.system, v=value: self.choose_parser(s, v.get()))
            else:
                ttk.Label(grid, text="ingen parser", foreground="#b42318").grid(row=index, column=1, sticky="w", padx=4)
            target = info.problem or info.target
            ttk.Label(grid, text=target, foreground="#b42318" if info.problem else "#444").grid(
                row=index, column=2, sticky="w", padx=4
            )
            ttk.Label(grid, text=info.owned_title or "(lages ved første spill)", foreground="#666").grid(
                row=index, column=3, sticky="w", padx=4
            )
        self._footer()

    def _check_row(self, parent: ttk.Frame, text: str, ok: bool, fix: tuple[str, Callable[[], None]] | None = None) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=("✓ " if ok else "✗ ") + text, foreground="#1a7f37" if ok else "#b42318").pack(side="left")
        if fix:
            ttk.Button(row, text=fix[0], command=fix[1]).pack(side="right")

    def _footer(self) -> None:
        footer = ttk.Frame(self.body)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Lagre feilsøkingsrapport", command=self.save_report).pack(side="left")
        ttk.Button(footer, text="Lukk", command=self.close).pack(side="right")

    def close(self) -> None:
        self.destroy()
        self.app.reload()

    def choose_appimage(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self,
            title="Velg Steam ROM Manager AppImage",
            initialdir=str(self.app.config.home),
            filetypes=[("AppImage", "*.AppImage"), ("Alle filer", "*")],
        )
        if chosen:
            set_srm_app_path(self.app.config, chosen)
            self.render()

    def set_setting(self, key: str, value: bool) -> None:
        if not messagebox.askokcancel("SRM-oppsett", "Endre denne innstillingen i SRM? (Det tas sikkerhetskopi.)", parent=self):
            return
        try:
            set_preview_setting(self.app.config, key, value)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("SRM-oppsett", str(error), parent=self)
        self.render()

    def choose_parser(self, system: str, title: str) -> None:
        save_parser_preference(self.app.config, system, title)
        self.render()

    def save_report(self) -> None:
        try:
            path = save_report(self.app.config)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("SRM-oppsett", str(error), parent=self)
            return
        messagebox.showinfo("SRM-oppsett", f"Rapporten er lagret her:\n{path}", parent=self)


def main(argv: list[str] | None = None) -> int:
    config = discover_config()
    root = tk.Tk()
    App(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
