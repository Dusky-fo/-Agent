"""Overview cards: one conclusion line, at most three aligned rows, one text entry.

The card face answers "what is happening now" instead of embedding a small table:
no headings, no scrollbars, no fixed 240px box that clips its own content.
"""
import tkinter as tk
from collections import Counter
from pathlib import PureWindowsPath

from PIL import ImageTk

from .desktop_skin import C, FONT, SPEC, rounded_image


def overview_states(status, cfg):
    names = cfg["monitor"]["process_names"]
    monitor = ("未启动", "尚未添加监控程序。添加后保存，即可开始观察。", "添加监控", "sub")
    if names:
        if not cfg["monitor"]["enabled"]:
            monitor = ("已暂停", "已添加 %s 个程序，监控已暂停。" % len(names), "管理监控", "warn")
        elif status["monitor_error"]:
            monitor = ("状态未知", status["monitor_error"], "检查监控", "warn")
        elif not status["local_running"]:
            monitor = ("未运行", "已保存监控程序，本地监控服务尚未运行。", "查看监控", "warn")
        else:
            monitor = ("监控中", "已添加 %s 个程序，当前 %s 个运行实例。" % (len(names), len(status["processes"])), "管理监控", "ok")
    users = len(status["users"])
    if status["qq"]["connected"]:
        qq = ("已连接", "机器人已连接，已授权 %s 个接收人。" % users if users else "机器人已连接；尚未绑定本人，请继续完成绑定。", "管理连接" if users else "完成绑定", "ok" if users else "warn")
    else:
        qq = ("未连接", status["qq"]["message"], "去连接", "warn")
    roots = len(status["roots"])
    files = ("已授权" if roots else "未设置", "已授权 %s 个 QQ 共享目录，单文件上限 %s MB。" % (roots, cfg["files"]["max_mb"]) if roots else "尚未授权 QQ 共享目录。绑定机器人不会自动开放文件。", "管理授权" if roots else "去设置", "ok" if roots else "sub")
    return {"monitor": monitor, "qq": qq, "files": files}


def overview_items(status, cfg):
    """Only saved program names and effective shared roots, never broadened drives."""
    programs=[name+" · "+note for name,note in overview_program_rows(status,cfg)]
    roots=[]
    for value in status["roots"]:
        path=str(value)
        drive=PureWindowsPath(path).drive
        roots.append((drive.rstrip(":")+" 盘 · " if len(drive)==2 and drive.endswith(":") else "")+path)
    return {"monitor": programs or ["尚未添加监控程序"], "files": roots or ["尚未授权共享目录"]}


def overview_program_rows(status,cfg):
    counts=Counter(row["name"].casefold() for row in status["processes"])
    active=cfg["monitor"]["enabled"] and status["local_running"] and not status["monitor_error"]
    programs=[]
    seen=set()
    for name in cfg["monitor"]["process_names"]:
        if name.casefold() in seen:continue
        seen.add(name.casefold())
        note=("%s 个实例"%counts[name.casefold()] if active else "已暂停" if not cfg["monitor"]["enabled"] else "状态未知")
        programs.append((name,note))
    return programs


def overview_tables(status,cfg):
    roots=[]
    for value in status["roots"]:
        path=str(value);drive=PureWindowsPath(path).drive
        roots.append((drive if drive else "本机",path))
    return {"monitor":overview_program_rows(status,cfg),
            "qq":[("运行环境","沙箱" if cfg["qq"]["is_sandbox"] else "正式"),
                  ("本人授权","%s 人"%len(status["users"]) if status["users"] else "待绑定"),
                  ("通知订阅","%s 人"%sum(bool(user.get("subscribed")) for user in status["users"]))],
            "files":roots}


class OverviewCard(tk.Canvas):
    """Rounded card: title, state dot, one conclusion line, up to three rows, one entry."""

    PAD = 14
    ROW = 24
    MAX_ROWS = 3
    EMPTY = {"monitor": "尚未添加监控程序", "qq": "尚无接入信息", "files": "尚未授权共享目录"}
    TONE = {"ok": "ok", "warn": "warn", "bad": "bad"}

    def __init__(self, parent, title, description, button_factory, key, command, *, kind):
        super().__init__(parent, bg=C["bg"], bd=0, highlightthickness=0, height=176, takefocus=0)
        self.kind = kind
        self.description = description
        self.state = tk.StringVar()
        self.detail = tk.StringVar(value=description)
        self.rows = []
        self.table_rows = None
        self._size = None
        self._tone = "sub"
        self.photo = None

        self.body = tk.Frame(self, bg=C["card2"])
        self.body.columnconfigure(0, weight=1)
        self.item = self.create_window(self.PAD, self.PAD, anchor="nw", window=self.body)

        header = tk.Frame(self.body, bg=C["card2"])
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text=title, font=(FONT, 9), bg=C["card2"], fg=C["sub"], anchor="w").pack(side="left")
        self.dot = tk.Canvas(header, width=8, height=8, bg=C["card2"], highlightthickness=0, bd=0)
        self.dot.pack(side="right", padx=(6, 0), pady=(5, 0))
        self.state_label = tk.Label(self.body, textvariable=self.state, font=(FONT, 17, "bold"), bg=C["card2"], fg=C["txt"], anchor="w")
        self.state_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        self.lead_label = tk.Label(self.body, textvariable=self.detail, font=(FONT, 9), bg=C["card2"], fg=C["sub"], anchor="w", justify="left")
        self.lead_label.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.list_body = tk.Frame(self.body, bg=C["card2"])
        self.list_body.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.body.rowconfigure(3, weight=1)
        self.empty_label = tk.Label(self.list_body, text=self.EMPTY[kind], font=(FONT, 9), bg=C["card2"], fg=C["sub"], anchor="w")

        footer = tk.Frame(self.body, bg=C["card2"])
        footer.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.more_label = tk.Label(footer, text="", font=(FONT, 9), bg=C["card2"], fg=C["sub"], anchor="w")
        self.more_label.pack(side="left")
        self.action = button_factory(footer, key, "去设置", command)
        self.action.primary = False
        self.action.ghost = True
        self.action.configure(background=C["card2"], width=max(96, self.action.font.measure("管理监控") + 34), height=30)
        self.action.pack(side="right")
        self.action.draw()

        self.empty_label.pack(anchor="w")
        self.bind("<Configure>", self._redraw)

    # ---- geometry and rounded face -------------------------------------
    def _redraw(self, event=None):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 40 or height < 40 or (width, height) == self._size:
            return
        self._size = (width, height)
        self.photo = ImageTk.PhotoImage(rounded_image(width, height, SPEC["card_radius"], C["card2"], C["card2"], None), master=self)
        self.delete("bg")
        self.create_image(0, 0, anchor="nw", image=self.photo, tags="bg")
        self.tag_lower("bg")
        self.coords(self.item, self.PAD, self.PAD)
        self.itemconfigure(self.item, width=width - 2 * self.PAD, height=height - 2 * self.PAD)
        self.lead_label.configure(wraplength=max(120, width - 2 * self.PAD))

    def _draw_dot(self):
        color = C[self.TONE.get(self._tone, "sub")]
        self.dot.delete("all")
        self.dot.create_oval(0, 0, 7, 7, fill=color, outline="")

    # ---- data ----------------------------------------------------------
    def update_state(self, state, detail, action, tone):
        self.state.set(state)
        self.detail.set(detail or self.description)
        self._tone = tone
        self.state_label.configure(fg=C[self.TONE.get(tone, "sub")] if tone in ("ok", "warn", "bad") else C["txt"])
        self._draw_dot()
        if self.action.text != action:
            self.action.text = action
            self.action.draw()

    def update_table(self, rows):
        rows = [(str(name), str(note)) for name, note in rows]
        if rows == self.table_rows:
            return
        self.table_rows = list(rows)
        self.rows = list(rows)
        for child in self.list_body.winfo_children():
            if child is not self.empty_label:
                child.destroy()
        shown = self._present(rows)[:self.MAX_ROWS]
        for index, (left, right) in enumerate(shown):
            line = tk.Frame(self.list_body, bg=C["card2"], height=self.ROW)
            line.pack(fill="x")
            line.pack_propagate(False)
            tk.Label(line, text=right, font=(FONT, 9), bg=C["card2"], fg=C["muted"], anchor="e").pack(side="right")
            tk.Label(line, text=left, font=(FONT, 9), bg=C["card2"], fg=C["sub"], anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 10))
        hidden = len(rows) - len(shown)
        self.more_label.configure(text="还有 %s 项" % hidden if hidden > 0 else "")
        if shown:
            self.empty_label.pack_forget()
        else:
            self.empty_label.pack(anchor="w")

    def _present(self, rows):
        if self.kind != "files":
            return [(name, note) for name, note in rows]
        shown = []
        for drive, path in rows:
            label = drive.rstrip(":") + " 盘" if drive.rstrip(":") else "本机"
            shown.append((label, self._short(path)))
        return shown

    @staticmethod
    def _short(path, limit=30):
        return path if len(path) <= limit else path[:11] + "…" + path[-15:]
