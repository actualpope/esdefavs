"""The program window: ROMS on the left, our SRM games on the right."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from . import __version__, theme
from .config import AppConfig, discover_config, set_roms_dir
from .engine import FIX, UPDATE, RunReport, run, save_report
from .games import APPLIED, PENDING_ADD, PENDING_REMOVE, GameRecord, load_games, move_out_of_srm, move_to_srm, save_games, undo_remove
from .library import Library, RomGame, scan_library
from .srm import (
    SrmData,
    describe_systems,
    load_parser_preferences,
    load_srm_or_none,
    parser_candidates,
    save_parser_preference,
    select_parser_candidate,
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
        self.roms_count = tk.StringVar()
        self.srm_count = tk.StringVar()

        theme.apply(root)
        root.title(TITLE)
        root.geometry("1240x760")
        root.minsize(960, 560)
        self._build()
        self.search.trace_add("write", lambda *_: self.refresh())
        root.after(100, self._poll_events)
        root.after(50, self.reload)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=(24, 20, 24, 18))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(2, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 18))
        ttk.Label(header, text=TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="Version.TLabel").pack(side="left", padx=(10, 0), pady=(6, 0))
        self.program_button = ttk.Button(header, text="Oppdater program", command=self.update_program)
        self.program_button.pack(side="right")
        search_box = tk.Frame(header, bg=theme.BG)
        search_box.pack(side="right", padx=(0, 14))
        self.search_entry = ttk.Entry(search_box, textvariable=self.search, width=34, style="Search.TEntry")
        self.search_entry.pack(side="left")
        self._placeholder(self.search_entry, "Søk etter spill eller konsoll")

        self.roms_tree, roms_actions = self._panel(outer, 0, "ROMS", self.roms_count, self.roms_info)
        ttk.Button(roms_actions, text="Velg rom-mappe", style="Ghost.TButton", command=self.choose_roms_dir).pack(side="right")
        self.srm_tree, srm_actions = self._panel(outer, 2, "SRM", self.srm_count, self.srm_info)
        ttk.Button(srm_actions, text="SRM-oppsett", style="Ghost.TButton", command=self.open_srm_setup).pack(side="right")
        self.roms_tree.bind("<Double-1>", lambda _: self.move_right())
        self.srm_tree.bind("<Double-1>", lambda _: self.move_left())
        self.roms_tree.bind("<Return>", lambda _: self.move_right())
        self.srm_tree.bind("<Return>", lambda _: self.move_left())

        middle = ttk.Frame(outer)
        middle.grid(row=1, column=1, padx=16)
        self.right_button = ttk.Button(middle, text="→", width=3, style="Arrow.TButton", command=self.move_right)
        self.right_button.pack(pady=(0, 10))
        self.left_button = ttk.Button(middle, text="←", width=3, style="Arrow.TButton", command=self.move_left)
        self.left_button.pack()

        footer = ttk.Frame(outer)
        footer.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        self.status_dot = tk.Canvas(footer, width=10, height=10, bg=theme.BG, highlightthickness=0, bd=0)
        self.status_dot.pack(side="left", padx=(2, 10))
        ttk.Label(footer, textvariable=self.status, style="Status.TLabel").pack(side="left")
        self.update_button = ttk.Button(footer, text="Oppdater", style="Accent.TButton", command=self.start_update)
        self.update_button.pack(side="right")
        self.fix_button = ttk.Button(footer, text="Fiks", style="Secondary.TButton", command=self.start_fix)
        self.fix_button.pack(side="right", padx=(0, 10))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=160, style="Accent.Horizontal.TProgressbar")

    def _panel(self, parent: ttk.Frame, column: int, title: str, count: tk.StringVar,
               info: tk.StringVar) -> tuple[ttk.Treeview, ttk.Frame]:
        outer, inner = theme.card(parent)
        outer.grid(row=1, column=column, sticky="nsew")
        inner.rowconfigure(3, weight=1)
        inner.columnconfigure(0, weight=1)

        head = ttk.Frame(inner, style="Card.TFrame", padding=(18, 16, 14, 2))
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")
        ttk.Label(head, textvariable=count, style="Badge.TLabel").pack(side="left", padx=(10, 0), pady=(3, 0))
        ttk.Label(inner, textvariable=info, style="CardMuted.TLabel", padding=(18, 0, 18, 12)).grid(
            row=1, column=0, columnspan=2, sticky="w")
        theme.divider(inner).grid(row=2, column=0, columnspan=2, sticky="ew")

        tree = ttk.Treeview(inner, columns=("system", "name", "status"), show="headings", selectmode="extended")
        tree.heading("system", text="KONSOLL", anchor="w")
        tree.heading("name", text="SPILL", anchor="w")
        tree.heading("status", text="", anchor="w")
        tree.column("system", width=96, stretch=False, anchor="w")
        tree.column("name", width=280, minwidth=200, stretch=True, anchor="w")
        tree.column("status", width=132, stretch=False, anchor="w")
        tree.tag_configure("new", foreground=theme.SUCCESS)
        tree.tag_configure("remove", foreground=theme.DANGER)
        tree.tag_configure("warn", foreground=theme.WARNING)
        vertical = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(inner, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=3, column=0, sticky="nsew", padx=(8, 0), pady=(4, 0))
        vertical.grid(row=3, column=1, sticky="ns", padx=(2, 4), pady=(4, 4))
        horizontal.grid(row=4, column=0, sticky="ew", padx=(8, 0), pady=(0, 4))
        return tree, head

    def _placeholder(self, entry: ttk.Entry, text: str) -> None:
        hint = ttk.Label(entry, text=text, style="Muted.TLabel", background=theme.SURFACE, cursor="xterm")
        hint.bind("<Button-1>", lambda _: entry.focus_set())

        def update(*_: object) -> None:
            if self.search.get() or entry.focus_get() is entry:
                hint.place_forget()
            else:
                hint.place(x=11, rely=0.5, anchor="w")

        entry.bind("<FocusIn>", update, add="+")
        entry.bind("<FocusOut>", update, add="+")
        self.search.trace_add("write", update)
        self.root.after(10, update)

    # ------------------------------------------------------------------- data

    def reload(self) -> None:
        self._set_status("Leser rom-mappa og SRM …", theme.ACCENT)
        self.root.update_idletasks()
        self.srm = load_srm_or_none(self.config)
        self.library = scan_library(self.config, self.srm)
        first_run = not (self.config.state_dir / "games.json").exists()
        self.records = load_games(self.config, self.library)
        self.refresh()
        if first_run and self.records:
            theme.info(
                self.root, "Velkommen",
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
            status, tag = ("●  fjernes", "remove") if game.id in removing else ("", "")
            left_rows.append((game.id, game.system, game.filename, status, tag))
        for record in removing.values():
            if record.id not in games:
                left_rows.append((record.id, record.system, record.display_name, "●  fjernes", "remove"))

        right_rows: list[tuple[str, str, str, str, str]] = []
        for record in self.records:
            if record.status == PENDING_ADD:
                right_rows.append((record.id, record.system, record.display_name, "●  ny", "new"))
            elif record.status == APPLIED:
                missing = not record.rel_path or record.id not in games
                status, tag = ("●  rom mangler", "warn") if missing and not self.library.error else ("", "")
                right_rows.append((record.id, record.system, record.display_name, status, tag))

        self._fill(self.roms_tree, left_rows, needle)
        self._fill(self.srm_tree, right_rows, needle)

        systems = len({game.system for game in self.library.games})
        self.roms_count.set(str(len(left_rows)))
        if self.library.error:
            self.roms_info.set(self.library.error)
        else:
            self.roms_info.set(f"{self.config.roms_dir}  ·  {systems} konsoller")
        in_steam = sum(1 for r in self.records if r.in_steam)
        self.srm_count.set(str(len(right_rows)))
        if self.srm is None:
            self.srm_info.set("Fant ikke SRM-oppsettet. Åpne «SRM-oppsett».")
        else:
            self.srm_info.set(f"{in_steam} spill i Steam via Steam ROM Manager")
        adds = sum(1 for r in self.records if r.status == PENDING_ADD)
        removes = len(removing)
        if not self.busy:
            if adds or removes:
                self._set_status(f"Ikke lagret: {adds} legges til, {removes} fjernes. Trykk «Oppdater».", theme.WARNING)
            else:
                self._set_status("Alt er lagret.", theme.SUCCESS)

    def _set_status(self, text: str, color: str) -> None:
        self.status.set(text)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def _fill(self, tree: ttk.Treeview, rows: list[tuple[str, str, str, str, str]], needle: str) -> None:
        top = tree.yview()[0]
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        rows.sort(key=lambda row: _sort_key(row[1], row[2]))
        for row_id, system, name, status, tag in rows:
            if needle and needle not in f"{system} {name}".casefold():
                continue
            tree.insert("", "end", iid=row_id, values=(system.upper(), name, status), tags=(tag,) if tag else ())
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
            if game is None or any(r.id == item and r.status == PENDING_REMOVE for r in self.records):
                undo_remove(self.records, item)
                continue
            reason = self._addable(game)
            if reason:
                refused.append(f"{game.system}/{game.filename}: {reason}")
                continue
            move_to_srm(self.records, game)
        self._save_and_refresh()
        if refused:
            theme.info(self.root, "Kan ikke legges til", "\n".join(refused[:20]), kind="warning")

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
        if busy:
            self.progress.pack(side="right", padx=(0, 18))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

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
        self.events.put(lambda: self._set_status(text, theme.ACCENT))

    def start_update(self) -> None:
        adds = sum(1 for r in self.records if r.status == PENDING_ADD)
        removes = sum(1 for r in self.records if r.status == PENDING_REMOVE)
        if not adds and not removes:
            theme.info(self.root, "Ingen endringer", "Flytt spill mellom listene først, og trykk så «Oppdater».")
            return
        text = f"{adds} spill legges til og {removes} fjernes.\n\nSteam lukkes mens dette pågår, og startes igjen etterpå."
        if removes:
            text += "\n\nSpill som fjernes forsvinner fra Steam, sammen med spilletid og bilder der."
        if theme.confirm(self.root, "Oppdater Steam", text, ok_text="Oppdater"):
            self._run_engine(UPDATE)

    def start_fix(self) -> None:
        count = sum(1 for r in self.records if r.in_steam)
        if not count:
            theme.info(self.root, "Fiks", "Det er ingen spill i SRM å fikse ennå.")
            return
        if theme.confirm(
            self.root, "Fiks startinnstillinger",
            f"Fiks oppdaterer startinnstillingene (emulator og argumenter) for alle {count} spill "
            "med dagens oppsett. Ingen spill legges til eller fjernes, og navn du har endret i Steam beholdes.\n\n"
            "Hvis et spill får ny emulator, kan Steam se det som et nytt spill (spilletid starter på nytt).\n\n"
            "Steam lukkes mens dette pågår.",
            ok_text="Fiks",
        ):
            self._run_engine(FIX)

    def _run_engine(self, mode: str) -> None:
        def done(result: object) -> None:
            self.reload()
            if isinstance(result, RunReport):
                failed = bool(result.error or not result.ok)
                theme.info(self.root, "Noe trenger oppmerksomhet" if failed else "Ferdig", result.summary(),
                           kind="warning" if failed else "success")
            else:
                theme.info(self.root, "Uventet feil", repr(result), kind="error")

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
            theme.info(self.root, "Oppdater program", f"Fant ikke oppdateringsskriptet:\n{script}", kind="error")
            return
        if not theme.confirm(self.root, "Oppdater program", "Laste ned og installere siste versjon fra GitHub?",
                             ok_text="Oppdater"):
            return
        self._set_status("Henter siste versjon fra GitHub …", theme.ACCENT)

        def work() -> object:
            return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=600)

        def done(result: object) -> None:
            if isinstance(result, subprocess.CompletedProcess) and result.returncode == 0:
                if theme.confirm(self.root, "Oppdatert", "Programmet er oppdatert. Starte det på nytt nå?",
                                 ok_text="Start på nytt", cancel_text="Senere"):
                    os.execv(sys.executable, [sys.executable, "-m", "emudeck_favorites_sync.gui"])
                self.refresh()
                return
            if isinstance(result, subprocess.CompletedProcess):
                output = (result.stdout + "\n" + result.stderr).strip()
            else:
                output = repr(result)
            self.refresh()
            theme.info(self.root, "Oppdatering feilet", output, kind="error")

        self._in_background(work, done)

    # ------------------------------------------------------------- SRM setup

    def open_srm_setup(self) -> None:
        if self.busy:
            return
        SrmSetup(self)


class SrmSetup(tk.Toplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app.root)
        self.app = app
        self.title("SRM-oppsett")
        self.geometry("1160x700")
        self.configure(bg=theme.BG)
        self.transient(app.root)
        self.body = ttk.Frame(self, padding=(24, 20, 24, 18))
        self.body.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.render()

    def render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        config = self.app.config
        srm = load_srm_or_none(config)
        self.app.srm = srm
        body = self.body

        header = ttk.Frame(body)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text="SRM-oppsett", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Lukk", command=self.close).pack(side="right")
        ttk.Button(header, text="Lagre feilsøkingsrapport", style="Ghost.TButton",
                   command=self.save_report).pack(side="right", padx=(0, 10))

        top = ttk.Frame(body)
        top.pack(fill="x", pady=(0, 16))
        top.columnconfigure(0, weight=1, uniform="top")
        top.columnconfigure(1, weight=1, uniform="top")

        app_outer, app_card = theme.card(top)
        app_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        app_card.configure(padding=(18, 16))
        ttk.Label(app_card, text="Steam ROM Manager", style="CardTitle.TLabel").pack(anchor="w")
        command, label = srm_command(config)
        self._check(app_card, label if command else "Fant ikke SRM-programmet.", ok=bool(command))
        ttk.Button(app_card, text="Velg SRM AppImage", style="Ghost.TButton",
                   command=self.choose_appimage).pack(anchor="w", pady=(10, 0))

        checks_outer, checks = theme.card(top)
        checks_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        checks.configure(padding=(18, 16))
        ttk.Label(checks, text="Kontroll", style="CardTitle.TLabel").pack(anchor="w")
        if srm is None:
            self._check(checks, "Fant ikke SRM sitt oppsett. Åpne Steam ROM Manager via EmuDeck én gang.", ok=False)
            return
        delete_disabled = srm.delete_disabled_shortcuts is True
        self._check(
            checks,
            "«Delete disabled shortcuts» er av" if not delete_disabled else "«Delete disabled shortcuts» er på – må være av",
            ok=not delete_disabled,
            fix=("Slå av", lambda: self.set_setting("deleteDisabledShortcuts", False)) if delete_disabled else None,
        )
        keep_images = srm.retrieve_current_steam_images is not False
        self._check(
            checks,
            "Eksisterende bilder i Steam beholdes" if keep_images else "SRM henter nye bilder for alle spill hver gang",
            ok=keep_images,
            fix=None if keep_images else ("Slå på", lambda: self.set_setting("retrieveCurrentSteamImages", True)),
        )
        environment = srm.environment
        for key in ("retroarchPath", "raCoresDirectory"):
            value = environment.get(key, "")
            self._check(checks, f"{key}: {value or 'tom – RetroArch-spill vil ikke starte'}", ok=bool(value))

        list_outer, list_card = theme.card(body)
        list_outer.pack(fill="both", expand=True)
        head = ttk.Frame(list_card, style="Card.TFrame", padding=(18, 16, 18, 4))
        head.pack(fill="x")
        ttk.Label(head, text="Emulator per konsoll", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(head, text="Gjelder nye spill. Trykk «Fiks» etterpå hvis spill som allerede er lagt til skal bytte emulator.",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(2, 10))
        theme.divider(list_card).pack(fill="x")

        container = ttk.Frame(list_card, style="Card.TFrame")
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0, bd=0, bg=theme.SURFACE)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        grid = ttk.Frame(canvas, style="Card.TFrame", padding=(18, 10, 18, 10))
        grid.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=grid, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)
        for sequence, delta in (("<Button-4>", -1), ("<Button-5>", 1)):
            canvas.bind_all(sequence, lambda _event, d=delta: canvas.yview_scroll(d, "units"))
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))

        for column, heading in enumerate(("KONSOLL", "SRM-PARSER (KILDE)", "EMULATOR", "VÅR PARSER I SRM")):
            ttk.Label(grid, text=heading, style="CardHead.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 22), pady=(0, 8))
        systems = sorted(
            {game.system for game in self.app.library.games} | {r.system for r in self.app.records},
            key=str.casefold,
        )
        for index, info in enumerate(describe_systems(config, srm, systems), start=1):
            ttk.Label(grid, text=info.system.upper(), style="Card.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 22), pady=4)
            if info.candidates:
                value = tk.StringVar(value=info.chosen)
                box = ttk.Combobox(grid, textvariable=value, values=info.candidates, state="readonly", width=36)
                box.grid(row=index, column=1, sticky="w", padx=(0, 22), pady=4)
                box.bind("<<ComboboxSelected>>", lambda _event, s=info.system, v=value: self.choose_parser(s, v.get()))
            else:
                ttk.Label(grid, text="ingen parser", style="Bad.Card.TLabel").grid(row=index, column=1, sticky="w", padx=(0, 22))
            ttk.Label(grid, text=info.problem or info.target,
                      style="Bad.Card.TLabel" if info.problem else "CardMuted.TLabel").grid(
                row=index, column=2, sticky="w", padx=(0, 22))
            ttk.Label(grid, text=info.owned_title or "lages ved første spill", style="CardMuted.TLabel").grid(
                row=index, column=3, sticky="w")

    def _check(self, parent: ttk.Frame, text: str, ok: bool, fix: tuple[str, Callable[[], None]] | None = None) -> None:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="✓" if ok else "✕", style="Ok.Card.TLabel" if ok else "Bad.Card.TLabel",
                  width=2).pack(side="left")
        ttk.Label(row, text=text, style="Card.TLabel" if ok else "Bad.Card.TLabel", wraplength=460,
                  justify="left").pack(side="left")
        if fix:
            ttk.Button(row, text=fix[0], style="Ghost.TButton", command=fix[1]).pack(side="right")

    def close(self) -> None:
        for sequence in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.unbind_all(sequence)
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
        if not theme.confirm(self, "SRM-oppsett", "Endre denne innstillingen i SRM? Det tas sikkerhetskopi først.",
                             ok_text="Endre"):
            return
        try:
            set_preview_setting(self.app.config, key, value)
        except Exception as error:  # noqa: BLE001
            theme.info(self, "SRM-oppsett", str(error), kind="error")
        self.render()

    def choose_parser(self, system: str, title: str) -> None:
        save_parser_preference(self.app.config, system, title)
        self.render()

    def save_report(self) -> None:
        try:
            path = save_report(self.app.config)
        except Exception as error:  # noqa: BLE001
            theme.info(self, "Feilsøkingsrapport", str(error), kind="error")
            return
        theme.info(self, "Feilsøkingsrapport", f"Rapporten er lagret her:\n{path}", kind="success")


def main(argv: list[str] | None = None) -> int:
    config = discover_config()
    root = tk.Tk(className="emudeck-favorites-sync")
    App(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
