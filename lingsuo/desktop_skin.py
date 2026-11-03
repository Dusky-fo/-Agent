"""LataleGuard panel visual tokens, rendered with native widgets in the host process."""
import json
import tkinter as tk
from tkinter import ttk, font
from PIL import Image, ImageDraw, ImageTk
from .paths import resource_dir

SPEC = json.loads((resource_dir() / "resources/latale_theme.json").read_text(encoding="utf-8"))
C = SPEC["tokens"].copy()
FONT = SPEC["font"]


def load_theme(directory):
    try:
        value = json.loads((directory / "appearance.json").read_text(encoding="utf-8"))["theme"]
        return value if value in SPEC["themes"] else "dark"
    except (OSError, ValueError, TypeError, KeyError): return "dark"


def set_theme(name):
    palette = SPEC["themes"].get(name, SPEC["themes"]["dark"])["tokens"]
    C.update(palette)
    if name == "light":
        C.update(input="#ffffff", selected="#e9f0ff", hover="#eef3fc")
    else:
        C.update(input=mix(C["accent"], C["bg"], 0.10),
                 selected=mix(C["accent"], C["card2"], 0.16),
                 hover=mix(C["txt"], C["card2"], 0.06))
    C.setdefault("muted", mix(C["txt"], C["bg"], 0.42))


def mix(color, base, ratio):
    """Blend color into base; ratio 0 returns base, 1 returns color."""
    left = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    right = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join("%02x" % round(b + (a - b) * ratio) for a, b in zip(left, right))


def rounded_image(width, height, radius, top, bottom, border=None):
    scale = 2
    w, h, r = max(2, width) * scale, max(2, height) * scale, radius * scale
    result = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(result)
    rgb1, rgb2 = tuple(bytes.fromhex(top[1:])), tuple(bytes.fromhex(bottom[1:]))
    for y in range(h):
        color = tuple(round(a + (b - a) * y / max(1, h - 1)) for a, b in zip(rgb1, rgb2))
        draw.line((0, y, w, y), fill=color)
    mask = Image.new("L", (w, h)); ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    result.putalpha(mask)
    if border:
        ImageDraw.Draw(result).rounded_rectangle((1, 1, w - 2, h - 2), r, outline=border, width=scale)
    return result.resize((width, height), Image.Resampling.LANCZOS)


class RoundButton(tk.Canvas):
    """Keyboard-accessible native button with the original rounded blue treatment."""
    def __init__(self, parent, text, command, *, primary=False, background=None, compact=False, ghost=False):
        self.text, self.command, self.primary, self.ghost = text, command, primary, ghost
        self.active = False
        self.tone = None
        self.disabled=False
        self.font = font.Font(family=FONT, size=9 if compact else 10)
        self.hover = False
        self.focused = False
        self.height = 29 if compact else 35
        width = self.font.measure(text) + (22 if compact else 28)
        super().__init__(parent, width=width, height=self.height, bd=0, highlightthickness=0,
                         background=background or C["bg"], cursor="hand2", takefocus=True)
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Map>", lambda _: self.draw())
        self.bind("<Enter>", lambda _: self.set_hover(True))
        self.bind("<Leave>", lambda _: self.set_hover(False))
        self.bind("<Button-1>", lambda _: self.focus_set())
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Return>", lambda _: self.invoke())
        self.bind("<space>", lambda _: self.invoke())
        self.bind("<FocusIn>", lambda _: self.set_focus(True))
        self.bind("<FocusOut>", lambda _: self.set_focus(False))

    def release(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height(): self.invoke()

    def invoke(self):
        if not self.disabled:return self.command()


    def set_hover(self, value):
        self.hover = value; self.draw()

    def set_focus(self, value):
        self.focused = value; self.draw()

    def draw(self):
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        if self.ghost:
            # Quiet entry on a card face: no fill until hover, accent text.
            top = bottom = C["hover"] if (self.hover or self.focused) else self["background"]
            border = C["accent"] if (self.hover or self.focused) else None
            text = C["sub"] if self.disabled else C["accent"]
        elif self.tone == "soft":
            # Secondary blue: a tinted face with accent text, no rim so it does not read as a black edge.
            top = bottom = mix(C["accent"], C["card2"], 0.26 if (self.hover or self.focused) else 0.15)
            border = C["accent"] if self.focused else None
            text = C["sub"] if self.disabled else C["accent"]
        else:
            top = C["accent2"] if self.primary and self.hover else C["accent"] if self.primary else C["hover"] if self.hover else C["card2"]
            bottom = C["accent2"] if self.primary else top
            border = C["accent"] if (self.focused or self.hover or self.active) else None if self.primary else C["line"]
            text = C["sub"] if self.disabled else "#ffffff" if self.primary else C["accent"] if self.active else C["txt"]
        self.photo = ImageTk.PhotoImage(rounded_image(w, h, 9, top, bottom, border), master=self)
        self.delete("all"); self.create_image(0, 0, anchor="nw", image=self.photo)
        self.create_text(w / 2, h / 2, text=self.text, fill=text, font=self.font)


class RoundedPanel(tk.Canvas):
    """A card face with rounded corners and a padded content frame."""
    def __init__(self, parent, *, pad=20, height=240, fill="card2", border=None):
        super().__init__(parent, bg=C["bg"], bd=0, highlightthickness=0, height=height)
        self.pad, self.fill, self.border = pad, fill, border
        self._size = None
        self.photo = None
        self.body = tk.Frame(self, bg=C[fill])
        self.window = self.create_window(pad, pad, anchor="nw", window=self.body)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 40 or height < 40 or (width, height) == self._size:
            return
        self._size = (width, height)
        self.photo = ImageTk.PhotoImage(rounded_image(width, height, SPEC["card_radius"], C[self.fill], C[self.fill], C[self.border] if self.border else None), master=self)
        self.delete("bg")
        self.create_image(0, 0, anchor="nw", image=self.photo, tags="bg")
        self.tag_lower("bg")
        self.coords(self.window, self.pad, self.pad)
        self.itemconfigure(self.window, width=width - 2 * self.pad, height=height - 2 * self.pad)


class Segmented(tk.Canvas):
    """Joined segments with the selected one on the primary blue."""
    def __init__(self, parent, items, command, *, height=30, radius=9, background=None):
        self.items = list(items)
        self.command = command
        self.selected = self.items[0][0] if self.items else None
        self.font = font.Font(family=FONT, size=9)
        self.pad = 12
        self.radius = radius
        self._piece = None
        super().__init__(parent, height=height, bg=background or C["card2"], bd=0, highlightthickness=0, cursor="hand2")
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Button-1>", self.click)

    def labels(self, values):
        self.items = [(key, values.get(key, text)) for key, text in self.items]
        self.draw()

    def select(self, key):
        if key != self.selected:
            self.selected = key
            self.draw()

    def widths(self):
        return [self.font.measure(text) + self.pad * 2 for _, text in self.items]

    def click(self, event):
        left = 0
        for (key, _), width in zip(self.items, self.widths()):
            if left <= event.x < left + width:
                if key != self.selected:
                    self.selected = key
                    self.draw()
                    self.command(key)
                return
            left += width

    def draw(self):
        widths = self.widths()
        total = sum(widths)
        height = int(self.cget("height"))
        if total <= 0 or height <= 0:
            return
        self.configure(width=total)
        self.photo = ImageTk.PhotoImage(rounded_image(total, height, self.radius, C["card2"], C["card2"], None), master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self.photo)
        left = 0
        for index, ((key, text), width) in enumerate(zip(self.items, widths)):
            if key == self.selected:
                self._piece = ImageTk.PhotoImage(rounded_image(width - 4, height - 4, max(2, self.radius - 3), C["accent"], C["accent"]), master=self)
                self.create_image(left + 2, 2, anchor="nw", image=self._piece)
                self.create_text(left + width / 2, height / 2, text=text, fill="#ffffff", font=self.font)
            else:
                if index:
                    self.create_line(left, 8, left, height - 8, fill=C["line"])
                self.create_text(left + width / 2, height / 2, text=text, fill=C["txt"], font=self.font)
            left += width


class MenuButton(tk.Canvas):
    """Rounded trigger with a custom popup list, matching the button language."""
    ROW = 32
    PAD = 6

    def __init__(self, parent, variable, values, command=None, *, width=112, height=30):
        self.variable = variable
        self.values = list(values)
        self.command = command
        self.font = font.Font(family=FONT, size=9)
        self.box_width, self.box_height = width, height
        self.popup = None
        self.menu = None
        self.menu_photo = None
        self.hover = None
        super().__init__(parent, width=width, height=height, bg=C["card2"], bd=0, highlightthickness=0, cursor="hand2", takefocus=True)
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Button-1>", lambda _: self.toggle())
        self.bind("<Return>", lambda _: self.toggle())
        self.bind("<space>", lambda _: self.toggle())
        self.bind("<FocusIn>", lambda _: self.draw())
        self.bind("<FocusOut>", lambda _: self.draw())
        variable.trace_add("write", lambda *_: self.draw())

    def draw(self):
        width, height = max(2, self.winfo_width()), max(2, self.winfo_height())
        border = C["accent"] if (self.popup or self.focus_get() is self) else C["line"]
        self.photo = ImageTk.PhotoImage(rounded_image(width, height, 9, C["card2"], C["card2"], border), master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self.photo)
        self.create_text(11, height / 2, anchor="w", text=self.variable.get(), fill=C["txt"], font=self.font)
        x = width - 13
        if self.popup:
            self.create_polygon(x - 4, height / 2 + 2, x + 4, height / 2 + 2, x, height / 2 - 3, fill=C["accent"], outline="")
        else:
            self.create_polygon(x - 4, height / 2 - 2, x + 4, height / 2 - 2, x, height / 2 + 3, fill=C["sub"], outline="")

    def toggle(self):
        if self.popup:
            self.close()
        else:
            self.open()

    def open(self):
        if self.popup:
            return
        width = max(self.box_width, 190)
        height = self.PAD * 2 + self.ROW * len(self.values)
        self.popup = tk.Toplevel(self)
        self.popup.overrideredirect(True)
        self.popup.attributes("-topmost", True)
        self.popup.configure(bg=mix(C["txt"], C["bg"], 0.10))
        self.menu = tk.Canvas(self.popup, width=width, height=height, bg=mix(C["txt"], C["bg"], 0.10), highlightthickness=0, bd=0, takefocus=True)
        self.menu.pack(padx=1, pady=1)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 6
        self.popup.geometry("%dx%d+%d+%d" % (width + 2, height + 2, x - 1, y))
        self.menu.bind("<Motion>", self.motion)
        self.menu.bind("<Leave>", lambda _: self.set_hover(None))
        self.menu.bind("<Button-1>", self.click)
        self.popup.bind("<Button-1>", self.outside_click, add=True)
        self.popup.bind("<Escape>", lambda _: self.close())
        self.popup.bind("<Destroy>", lambda _: self.reset())
        self.popup.grab_set()
        self.menu.focus_set()
        self.draw_menu()
        self.draw()

    def outside_click(self, event):
        """A grab sends clicks outside the list here; close instead of ignoring them."""
        if event.widget is not self.menu:
            self.close()
        return "break"

    def draw_menu(self):
        if self.menu is None:
            return
        width, height = int(self.menu.cget("width")), int(self.menu.cget("height"))
        self.menu_photo = ImageTk.PhotoImage(rounded_image(width, height, 10, C["card2"], C["card2"], None), master=self.menu)
        self.menu.delete("all")
        self.menu.create_image(0, 0, anchor="nw", image=self.menu_photo)
        for index, text in enumerate(self.values):
            top = self.PAD + index * self.ROW
            if index == self.hover:
                tint = mix(C["accent"], C["card2"], .12)
                self.menu._piece = ImageTk.PhotoImage(rounded_image(width - 2 * self.PAD, self.ROW - 2, 7, tint, tint), master=self.menu)
                self.menu.create_image(self.PAD, top + 1, anchor="nw", image=self.menu._piece)
            current = text == self.variable.get()
            self.menu.create_text(self.PAD + 10, top + self.ROW / 2, anchor="w", text=text,
                                  fill=C["accent"] if current else C["txt"], font=self.font)
            if current:
                self.menu.create_text(width - self.PAD - 12, top + self.ROW / 2, anchor="e", text="✓", fill=C["accent"], font=self.font)

    def index_at(self, y):
        position = y - self.PAD
        if 0 <= position < self.ROW * len(self.values):
            return int(position // self.ROW)
        return None

    def motion(self, event):
        self.set_hover(self.index_at(event.y))

    def set_hover(self, index):
        if index != self.hover:
            self.hover = index
            self.draw_menu()

    def click(self, event):
        if not (0 <= event.x < int(self.menu.cget("width")) and 0 <= event.y < int(self.menu.cget("height"))):
            self.close()
            return
        index = self.index_at(event.y)
        if index is not None:
            self.choose(self.values[index])

    def choose(self, value):
        self.variable.set(value)
        self.close()
        try: self.focus_set()
        except tk.TclError: pass
        if self.command is not None:
            self.command(value)

    def close(self):
        popup = self.popup
        self.reset()
        if popup is not None:
            popup.destroy()

    def reset(self):
        if self.popup is not None and self.popup.winfo_exists():
            try:
                self.popup.grab_release()
            except tk.TclError:
                pass
        self.popup = None
        self.menu = None
        self.hover = None
        self.draw()


class MaskedSecretEntry(ttk.Frame):
    """Show a saved-secret marker without putting the stored secret in the entry."""
    def __init__(self, parent, variable, *, saved=False):
        super().__init__(parent)
        self.variable = variable
        self.saved = bool(saved)
        self.mask_visible = None
        self.entry = ttk.Entry(self, textvariable=variable, show="●", exportselection=False)
        self.entry.pack(fill="x", expand=True)
        self.placeholder = tk.Label(self, text="●" * 12, font=(FONT, 10), background=C["input"],
                                    foreground=C["sub"], bd=0, padx=0, pady=0, cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda _: self.entry.focus_set())
        # Block widget and platform copy/cut paths, including keyboard aliases.
        for event in ("<<Copy>>", "<<Cut>>", "<Control-c>", "<Control-C>",
                      "<Control-x>", "<Control-X>", "<Control-Insert>", "<Shift-Delete>", "<Button-3>"):
            self.entry.bind(event, lambda _: "break")
            self.placeholder.bind(event, lambda _: "break")
        self.trace = variable.trace_add("write", lambda *_: self.update_mask())
        self.bind("<Destroy>", self.cleanup)
        self.update_mask()

    def set_saved(self, saved):
        self.saved = bool(saved)
        self.update_mask()

    def update_mask(self):
        visible = self.saved and not self.variable.get()
        if visible == self.mask_visible: return
        self.mask_visible = visible
        if visible: self.placeholder.place(x=10, rely=.5, anchor="w")
        else: self.placeholder.place_forget()

    def cleanup(self, event):
        if event.widget == self: self.variable.trace_remove("write", self.trace)


class CheckToggle(tk.Canvas):
    """Checkbox with a drawn tick, independent of Windows theme/font indicators."""
    def __init__(self, parent, text, variable, command=None, *, background=None):
        self.variable, self.command, self.text = variable, command, text
        self.font = font.Font(family=FONT, size=10)
        super().__init__(parent, width=self.font.measure(text) + 32, height=34, background=background or C["bg"],
                         bd=0, highlightthickness=0, cursor="hand2", takefocus=True)
        self.trace = variable.trace_add("write", lambda *_: self.draw())
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Button-1>", lambda _: self.focus_set())
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<space>", lambda _: self.invoke())
        self.bind("<Return>", lambda _: self.invoke())
        self.bind("<FocusIn>", lambda _: self.draw())
        self.bind("<FocusOut>", lambda _: self.draw())
        self.bind("<Destroy>", self.cleanup)
        self.draw()

    def release(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height(): self.invoke()

    def invoke(self):
        self.variable.set(not self.variable.get())
        if self.command: return self.command()

    def cleanup(self, event):
        if event.widget == self: self.variable.trace_remove("write", self.trace)

    def draw(self):
        self.delete("all")
        checked = bool(self.variable.get())
        self.create_rectangle(3, 9, 19, 25, fill=C["accent"] if checked else C["card"],
                              outline=C["accent"] if checked or self.focus_get() == self else C["sub"])
        if checked:
            self.create_line(6, 17, 10, 21, 17, 12, width=2, fill="white", capstyle="round", joinstyle="round", tags="tick")
        self.create_text(27, 17, text=self.text, anchor="w", fill=C["txt"], font=self.font)


class ActionCard(tk.Canvas):
    def __init__(self, parent, title, summary, command, *, action="编辑", height=104, swatch=None):
        super().__init__(parent, background=C["bg"], bd=0, highlightthickness=0, height=height, width=200)
        self.title, self.summary = title, summary
        self.swatch = swatch
        self.photo = None
        self.title_font = font.Font(family=FONT, size=10, weight="bold")
        self.summary_font = font.Font(family=FONT, size=9)
        self.edit = RoundButton(self, action, command, primary=True, compact=True, background=C["card2"])
        self.summary_trace = summary.trace_add("write", lambda *_: self.update_summary())
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Destroy>", self.cleanup)

    def cleanup(self, event):
        if event.widget == self:
            self.summary.trace_remove("write", self.summary_trace)

    def draw(self):
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        self.photo = ImageTk.PhotoImage(rounded_image(w, h, SPEC["card_radius"], C["card2"], C["card"], None), master=self)
        self.delete("all"); self.create_image(0, 0, image=self.photo, anchor="nw")
        self.create_text(16, 22, text=self.title, fill=C["txt"], font=self.title_font, anchor="w", width=max(20, w - 100))
        self.summary_id = self.create_text(16, 46, text=self.summary.get(), fill=C["sub"], font=self.summary_font,
                                            anchor="nw", width=max(20, w - 32))
        self.create_window(w - 16, 14, window=self.edit, anchor="ne")
        if self.swatch: self.create_rectangle(w - 66, h - 26, w - 18, h - 19, fill=self.swatch, outline="")

    def update_summary(self):
        if hasattr(self, "summary_id"): self.itemconfigure(self.summary_id, text=self.summary.get())


class NavItem(tk.Canvas):
    def __init__(self, parent, text, command):
        super().__init__(parent, height=41, width=186, background=C["card2"], highlightthickness=0, cursor="hand2", takefocus=True)
        self.text, self.command, self.active, self.hover = text, command, False, False
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<ButtonRelease-1>", lambda _: self.invoke())
        self.bind("<Return>", lambda _: self.invoke())
        self.bind("<space>", lambda _: self.invoke())
        self.bind("<Enter>", lambda _: self.enter(True))
        self.bind("<Leave>", lambda _: self.enter(False))

    def invoke(self):return self.command()
    def enter(self,value):self.hover=value;self.draw()
    def select(self,active):
        if active!=self.active:self.active=active;self.draw()
    def draw(self):
        self.delete("all");w=max(2,self.winfo_width())
        if self.active or self.hover:
            self.photo=ImageTk.PhotoImage(rounded_image(w,41,9,C["selected"] if self.active else C["hover"],C["selected"] if self.active else C["hover"]),master=self)
            self.create_image(0,0,image=self.photo,anchor="nw")
        if self.active:self.create_line(2,8,2,33,fill=C["accent"],width=3)
        self.create_oval(13,18,19,24,fill=C["accent"],outline="")
        self.create_text(29,21,text=self.text,anchor="w",fill=C["accent"] if self.active else C["txt"] if self.hover else C["sub"],font=(FONT,10))


class Badge(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, height=30, width=90, background=C["bg"], highlightthickness=0)
        self.font = font.Font(family=FONT, size=9)
        self.current = None

    def set(self, text, tone=None):
        if self.current == (text, tone): return
        self.current = text, tone
        w = self.font.measure(text) + (34 if tone else 24)
        self.configure(width=w)
        self.photo = ImageTk.PhotoImage(rounded_image(w, 30, 15, C["card2"], C["card2"], None), master=self)
        self.delete("all"); self.create_image(0, 0, image=self.photo, anchor="nw")
        if tone: self.create_oval(12, 12, 18, 18, fill=C[tone], outline="")
        self.create_text(23 if tone else 12, 15, text=text, font=self.font, anchor="w", fill=C["txt"])


def apply_styles(root):
    if "input" not in C: set_theme("dark")
    C["header"] = mix(C["accent"], C["card"], 0.10)
    C["select"] = mix(C["accent"], C["card2"], 0.34)
    root.configure(background=C["bg"])
    style = ttk.Style(root); style.theme_use("clam")
    style.configure(".", background=C["bg"], foreground=C["txt"], font=(FONT, 10), bordercolor=C["line"], lightcolor=C["line"], darkcolor=C["line"], focuscolor=C["accent"])
    style.configure("TFrame", background=C["bg"])
    style.configure("TLabel", background=C["bg"], foreground=C["sub"])
    style.configure("Title.TLabel", font=(FONT, 16, "bold"), foreground=C["txt"])
    style.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 0, 0, 8))
    style.layout("Shell.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    style.configure("TNotebook.Tab", background=C["bg"], foreground=C["sub"], padding=(14, 9), borderwidth=0, lightcolor=C["bg"], darkcolor=C["bg"], bordercolor=C["bg"])
    style.map("TNotebook.Tab", background=[("selected", C["select"])], foreground=[("selected", C["accent"]), ("active", C["txt"])])
    style.configure("TEntry", fieldbackground=C["input"], foreground=C["txt"], insertcolor=C["txt"], padding=8, bordercolor=mix(C["accent"], C["line"], 0.35))
    style.map("TEntry", bordercolor=[("focus", C["accent"])])
    style.configure("TCombobox", fieldbackground=C["input"], background=C["card2"], foreground=C["txt"], arrowcolor=C["sub"], padding=7)
    style.map("TCombobox", fieldbackground=[("readonly", C["input"])], foreground=[("readonly", C["txt"])], selectbackground=[("readonly", C["input"])], selectforeground=[("readonly", C["txt"])])
    root.option_add("*TCombobox*Listbox.background", C["card"])
    root.option_add("*TCombobox*Listbox.foreground", C["txt"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure("TCheckbutton", background=C["bg"], foreground=C["txt"], padding=5, indicatorbackground=C["card2"], indicatorforeground="#ffffff")
    style.map("TCheckbutton", background=[("active", C["bg"])], indicatorbackground=[("selected", C["accent"]), ("!selected", C["card2"])])
    style.configure("Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["txt"], rowheight=30, borderwidth=0, bordercolor=C["line"], lightcolor=C["card"], darkcolor=C["card"])
    style.configure("Roomy.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["txt"], rowheight=40, borderwidth=0,
                    bordercolor=C["line"], lightcolor=C["card"], darkcolor=C["card"])
    style.map("Roomy.Treeview", background=[("selected", C["select"])], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=C["header"], foreground=C["sub"], font=(FONT, 9), padding=(8, 9), relief="flat", bordercolor=C["line"], lightcolor=C["header"], darkcolor=C["header"])
    style.map("Treeview", background=[("selected", C["select"])], foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", mix(C["accent"], C["card"], 0.24))], foreground=[("active", C["txt"])])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=mix(C["accent"], C["card"], 0.20), troughcolor=C["bg"], borderwidth=0, arrowsize=12, arrowcolor=C["sub"])
        style.map(name, background=[("active", mix(C["accent"], C["card"], 0.38))])
    style.configure("TPanedwindow", background=C["bg"])


class PageStack(ttk.Frame):
    """Sidebar-controlled page stack: no duplicate native notebook tabs."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.pages = []
        self.current = None

    def add(self, frame, text=""):
        self.pages.append(frame)
        if self.current is None: self.select(frame)

    def select(self, frame=None):
        if frame is None: return str(self.current) if self.current else ""
        if isinstance(frame, int): frame = self.pages[frame]
        if frame not in self.pages: raise ValueError("未知页面")
        if self.current is not frame:
            if self.current is not None: self.current.pack_forget()
            frame.pack(fill="both", expand=True)
            self.current = frame
            self.update_idletasks()
            self.event_generate("<<NotebookTabChanged>>", when="tail")


def dark_titlebar(window):
    """Theme this application's own Windows title bar when supported."""
    try:
        import ctypes
        from ctypes import wintypes
        api = ctypes.windll.user32
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]; api.GetAncestor.restype = wintypes.HWND
        handle = api.GetAncestor(window.winfo_id(), 2)
        enabled = ctypes.c_int(int(sum(bytes.fromhex(C["bg"][1:])) / 3 < 128))
        fn = ctypes.windll.dwmapi.DwmSetWindowAttribute
        fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        fn(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (AttributeError, OSError, tk.TclError):
        pass
