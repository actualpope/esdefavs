"""Dark theme and small building blocks for the program window."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


BG = "#0b0d10"
SURFACE = "#121519"
SURFACE_2 = "#1a1e24"
SURFACE_3 = "#232830"
BORDER = "#262b33"
TEXT = "#e7e9ee"
MUTED = "#8a93a3"
SUBTLE = "#5b6372"
ACCENT = "#6e7bff"
ACCENT_HOVER = "#8792ff"
ACCENT_PRESSED = "#5a66ea"
SELECTION = "#252c52"
SUCCESS = "#4fd18b"
DANGER = "#f2706f"
WARNING = "#e8b55a"

FONT_FAMILIES = ("Inter", "Geist", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")

fonts: dict[str, tkfont.Font] = {}


def _family(root: tk.Misc) -> str:
    available = set(tkfont.families(root))
    for name in FONT_FAMILIES:
        if name in available:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def apply(root: tk.Tk) -> None:
    family = _family(root)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=10)
        except tk.TclError:
            pass
    fonts.update(
        body=tkfont.Font(root, family=family, size=10),
        small=tkfont.Font(root, family=family, size=9),
        small_bold=tkfont.Font(root, family=family, size=9, weight="bold"),
        button=tkfont.Font(root, family=family, size=10, weight="bold"),
        title=tkfont.Font(root, family=family, size=17, weight="bold"),
        heading=tkfont.Font(root, family=family, size=13, weight="bold"),
        arrow=tkfont.Font(root, family=family, size=15, weight="bold"),
    )

    root.configure(bg=BG)
    root.option_add("*Background", SURFACE)
    root.option_add("*Foreground", TEXT)
    root.option_add("*activeBackground", SURFACE_3)
    root.option_add("*activeForeground", TEXT)
    root.option_add("*selectBackground", SELECTION)
    root.option_add("*selectForeground", TEXT)
    root.option_add("*insertBackground", TEXT)
    root.option_add("*highlightBackground", BORDER)
    root.option_add("*highlightColor", ACCENT)
    root.option_add("*TCombobox*Listbox.background", SURFACE_2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECTION)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", fonts["body"])

    style = ttk.Style(root)
    style.theme_use("clam")
    flat = dict(bordercolor=BORDER, lightcolor=BG, darkcolor=BG)
    style.configure(
        ".", background=BG, foreground=TEXT, fieldbackground=SURFACE_2, troughcolor=SURFACE,
        selectbackground=SELECTION, selectforeground=TEXT, insertcolor=TEXT, font=fonts["body"],
        focuscolor=ACCENT, **flat,
    )
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED, font=fonts["small"])
    style.configure("Title.TLabel", font=fonts["title"])
    style.configure("Version.TLabel", foreground=SUBTLE, font=fonts["small"])
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardTitle.TLabel", background=SURFACE, font=fonts["heading"])
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=fonts["small"])
    style.configure("CardHead.TLabel", background=SURFACE, foreground=SUBTLE, font=fonts["small_bold"])
    style.configure("Badge.TLabel", background=SURFACE_3, foreground=MUTED, font=fonts["small_bold"], padding=(8, 1))
    for name, color in (("Ok", SUCCESS), ("Bad", DANGER), ("Warn", WARNING)):
        style.configure(f"{name}.Card.TLabel", background=SURFACE, foreground=color)
    style.configure("Status.TLabel", foreground=MUTED)

    def button(name: str, bg: str, fg: str, hover: str, pressed: str, border: str, padding: tuple[int, int], font_name: str) -> None:
        style.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                        padding=padding, font=fonts[font_name], relief="flat", focusthickness=0, anchor="center")
        style.map(
            name,
            background=[("disabled", SURFACE_2), ("pressed", pressed), ("active", hover)],
            lightcolor=[("disabled", SURFACE_2), ("pressed", pressed), ("active", hover)],
            darkcolor=[("disabled", SURFACE_2), ("pressed", pressed), ("active", hover)],
            foreground=[("disabled", SUBTLE)],
            bordercolor=[("disabled", BORDER), ("active", border if border != BORDER else "#353b46")],
        )

    button("TButton", SURFACE_2, TEXT, SURFACE_3, SURFACE, BORDER, (14, 7), "body")
    button("Accent.TButton", ACCENT, "#ffffff", ACCENT_HOVER, ACCENT_PRESSED, ACCENT, (24, 9), "button")
    button("Secondary.TButton", SURFACE_2, TEXT, SURFACE_3, SURFACE, BORDER, (18, 9), "button")
    button("Ghost.TButton", SURFACE_2, MUTED, SURFACE_3, SURFACE, BORDER, (12, 6), "small")
    button("Arrow.TButton", SURFACE_2, TEXT, ACCENT, ACCENT_PRESSED, BORDER, (14, 10), "arrow")
    style.map("Ghost.TButton", foreground=[("disabled", SUBTLE), ("active", TEXT)])

    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=30,
                    borderwidth=0, font=fonts["body"])
    style.map("Treeview", background=[("selected", SELECTION)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=SURFACE, foreground=SUBTLE, font=fonts["small_bold"],
                    relief="flat", borderwidth=0, bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", SURFACE)], foreground=[("active", MUTED)])

    for orient in ("Vertical", "Horizontal"):
        sticky = "ns" if orient == "Vertical" else "we"
        style.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {
            "sticky": sticky, "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
        })])
        style.configure(f"{orient}.TScrollbar", troughcolor=SURFACE, background=SURFACE_3, bordercolor=SURFACE,
                        lightcolor=SURFACE_3, darkcolor=SURFACE_3, arrowsize=9, gripcount=0, relief="flat")
        style.map(f"{orient}.TScrollbar", background=[("active", "#353b46")],
                  lightcolor=[("active", "#353b46")], darkcolor=[("active", "#353b46")])

    style.configure("Search.TEntry", fieldbackground=SURFACE, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, padding=(10, 7), insertcolor=TEXT)
    style.map("Search.TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=SURFACE_2, background=SURFACE_2, foreground=TEXT,
                    arrowcolor=MUTED, bordercolor=BORDER, lightcolor=SURFACE_2, darkcolor=SURFACE_2, padding=(8, 5))
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE_2)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", SURFACE_2)], selectforeground=[("readonly", TEXT)],
              bordercolor=[("focus", ACCENT), ("active", "#353b46")], arrowcolor=[("active", TEXT)])
    style.configure("Accent.Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE_2,
                    bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)


def card(parent: tk.Misc) -> tuple[tk.Frame, ttk.Frame]:
    """A panel with a thin border. Returns (outer, inner); grid/pack the outer, fill the inner."""
    outer = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1, bd=0)
    inner = ttk.Frame(outer, style="Card.TFrame")
    inner.pack(fill="both", expand=True)
    return outer, inner


def divider(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, bg=BORDER, height=1, bd=0)


class Dialog(tk.Toplevel):
    """A modal message in the same style as the window."""

    COLORS = {"info": ACCENT, "success": SUCCESS, "warning": WARNING, "error": DANGER, "confirm": ACCENT}

    def __init__(self, parent: tk.Misc, title: str, message: str, kind: str = "info",
                 ok_text: str = "OK", cancel_text: str | None = None) -> None:
        super().__init__(parent)
        self.result = False
        self.withdraw()
        self.title(title)
        self.configure(bg=BORDER)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        frame = ttk.Frame(self, style="Card.TFrame", padding=(26, 22, 26, 20))
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        head = ttk.Frame(frame, style="Card.TFrame")
        head.pack(fill="x")
        dot = tk.Canvas(head, width=12, height=12, bg=SURFACE, highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 11, 11, fill=self.COLORS.get(kind, ACCENT), outline="")
        dot.pack(side="left", padx=(0, 10))
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")

        lines = message.count("\n") + 1
        if lines > 16 or len(message) > 1400:
            box = tk.Text(frame, wrap="word", width=70, height=16, bg=SURFACE_2, fg=TEXT, relief="flat",
                          highlightthickness=0, padx=12, pady=10, font=fonts["body"])
            box.insert("1.0", message)
            box.configure(state="disabled")
            box.pack(fill="both", expand=True, pady=(14, 18))
        else:
            ttk.Label(frame, text=message, style="Card.TLabel", foreground=MUTED, wraplength=500,
                      justify="left").pack(anchor="w", pady=(12, 20))

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.pack(fill="x")
        ok = ttk.Button(buttons, text=ok_text, style="Accent.TButton", command=self._ok)
        ok.pack(side="right")
        if cancel_text:
            ttk.Button(buttons, text=cancel_text, style="Secondary.TButton", command=self.destroy).pack(
                side="right", padx=(0, 10))
        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self.destroy())

        self.update_idletasks()
        owner = parent.winfo_toplevel()
        x = owner.winfo_rootx() + max(0, (owner.winfo_width() - self.winfo_reqwidth()) // 2)
        y = owner.winfo_rooty() + max(0, (owner.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        ok.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def _ok(self) -> None:
        self.result = True
        self.destroy()


def info(parent: tk.Misc, title: str, message: str, kind: str = "info") -> None:
    Dialog(parent, title, message, kind=kind)


def confirm(parent: tk.Misc, title: str, message: str, ok_text: str = "Fortsett", cancel_text: str = "Avbryt") -> bool:
    return Dialog(parent, title, message, kind="confirm", ok_text=ok_text, cancel_text=cancel_text).result
