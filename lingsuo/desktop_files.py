"""File page: one folder line on top, one roomy file table, actions only when a file is picked."""
import json
import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog
from .desktop_skin import C, FONT, CheckToggle, MaskedSecretEntry, MenuButton, RoundedPanel


class FileScreens:
    def _files(self):
        host = self.pages["files"]
        page = ttk.Frame(host); page.pack(fill="both", expand=True)
        self.file_rows = []; self.file_choice = None; self.file_task_id = None
        self.vars["desktop"] = tk.BooleanVar(value=self.app.config.get()["files"]["allow_desktop"])
        self.vars["file_user"] = tk.StringVar()
        self.vars["manager_query"] = tk.StringVar()
        self.file_selection = tk.StringVar(value="")
        self.file_transfer_note = tk.StringVar(value="")
        self.file_note = tk.StringVar()
        self.file_last_note = tk.StringVar(value="还没有发送记录")
        self.file_root_summary = tk.StringVar()
        self.file_folder = tk.StringVar()
        self.file_folder_label = tk.StringVar(value="还没有授权目录")
        self.file_path_note = tk.StringVar()
        self.file_count = tk.StringVar()
        self.file_ops_note = tk.StringVar()
        self.file_sel_name = tk.StringVar()
        self.file_sel_meta = tk.StringVar()
        self.manager_rows = []
        self.folder_map = {}

        context = RoundedPanel(page, pad=16, height=64); context.pack(fill="x", pady=(0, 12))
        line = tk.Frame(context.body, bg=C["card2"]); line.pack(fill="x")
        tk.Label(line, text="目录", font=(FONT, 9), fg=C["sub"], bg=C["card2"]).pack(side="left")
        self.folder_menu = MenuButton(line, self.file_folder_label, [], self.pick_folder, width=228, height=30)
        self.folder_menu.pack(side="left", padx=(10, 0))
        tk.Label(line, textvariable=self.file_path_note, font=(FONT, 9), fg=C["sub"], bg=C["card2"], anchor="w").pack(side="left", padx=(12, 0))
        tk.Label(line, textvariable=self.file_count, font=(FONT, 9), fg=C["sub"], bg=C["card2"], anchor="w").pack(side="left", padx=(10, 0))
        roots = self.button(line, "file_roots", "目录与权限 ›", self.roots_dialog)
        roots.primary = False; roots.ghost = True; roots.configure(background=C["card2"], width=104, height=26)
        roots.pack(side="right"); roots.draw()
        tk.Label(line, textvariable=self.file_note, font=(FONT, 9), fg=C["sub"], bg=C["card2"], anchor="e").pack(side="right", padx=(0, 14))

        table_card = RoundedPanel(page, pad=14, height=260); table_card.pack(fill="both", expand=True)
        self.manager_tree = self.table(table_card.body, [("name", "文件"), ("size", "大小"), ("mtime", "修改")],
                                       height=8, style="Roomy.Treeview")
        self.manager_tree.column("name", width=300, minwidth=140)
        self.manager_tree.column("size", width=88, minwidth=72, stretch=False, anchor="e")
        self.manager_tree.column("mtime", width=112, minwidth=100, stretch=False, anchor="e")
        self.manager_tree.empty_note.configure(text="先选一个目录，这里会列出里面的文件")
        self.manager_tree.bind("<<TreeviewSelect>>", self.file_selected)

        self.selbar = RoundedPanel(page, pad=12, height=58)
        bar = tk.Frame(self.selbar.body, bg=C["card2"]); bar.pack(fill="x")
        tk.Label(bar, textvariable=self.file_sel_name, font=(FONT, 10, "bold"), fg=C["txt"], bg=C["card2"], anchor="w").pack(side="left")
        tk.Label(bar, textvariable=self.file_sel_meta, font=(FONT, 9), fg=C["sub"], bg=C["card2"], anchor="w").pack(side="left", padx=(10, 0))
        self.button(bar, "file_send_selected", "发到 QQ", self.send_selected).pack(side="right")
        edit = self.button(bar, "manager_edit", "文本编辑", self.data_dialog)
        edit.tone = "soft"; edit.configure(background=C["card2"], width=82, height=30)
        edit.pack(side="right", padx=(0, 8)); edit.draw()
        for key, text, mode in (("convert", "转换", "convert"), ("organize", "整理", "organize"),
                                ("move", "移动", "move"), ("rename", "重命名", "rename")):
            btn = self.button(bar, "manager_" + key, text, lambda m=mode: self.manager_action(m))
            btn.primary = False; btn.ghost = True; btn.configure(background=C["card2"], width=58, height=28)
            btn.pack(side="right", padx=(0, 6)); btn.draw()

        foot = tk.Frame(page, bg=C["bg"]); foot.pack(fill="x", pady=(12, 0)); self.foot = foot
        tk.Label(foot, text="在 QQ 里说", font=(FONT, 9), fg=C["sub"], bg=C["bg"]).pack(side="left")
        tk.Label(foot, text="「把操作手册发我」", font=(FONT, 9), fg=C["txt"], bg=C["bg"]).pack(side="left", padx=(6, 0))
        copy = self.button(foot, "file_copy_example", "复制", self.copy_example)
        copy.primary = False; copy.ghost = True; copy.configure(background=C["bg"], width=42, height=26)
        copy.pack(side="left", padx=(6, 0)); copy.draw()
        self.file_test_note = tk.StringVar()
        tk.Label(foot, textvariable=self.file_test_note, font=(FONT, 9), fg=C["sub"], bg=C["bg"]).pack(side="left", padx=(10, 0))
        self.button(foot, "file_test_send", "发一次测试文件", self.send_test_file).pack(side="right")
        for key, text, fn in (("file_recovery", "恢复记录", self.recovery_dialog),
                              ("setting_inbound", "接收设置", self.inbox_dialog),
                              ("file_open_folder", "打开目录", self.open_current_folder)):
            link = self.button(foot, key, text, fn)
            link.primary = False; link.ghost = True; link.configure(background=C["bg"], width=58, height=26)
            link.pack(side="right", padx=(0, 4)); link.draw()
        tk.Label(page, textvariable=self.file_ops_note, font=(FONT, 9), fg=C["sub"], bg=C["bg"], anchor="w").pack(fill="x", pady=(6, 0))
        self.selbar_shown = False

    # ---- 目录切换 --------------------------------------------------------
    def pick_folder(self, value):
        folder = self.folder_map.get(value, "")
        if folder: self.load_folder(folder)

    def show_selbar(self, visible):
        if visible and not self.selbar_shown:
            self.selbar.pack(fill="x", pady=(12, 0), before=self.foot)
            self.selbar_shown = True
        elif not visible and self.selbar_shown:
            self.selbar.pack_forget(); self.selbar_shown = False

    def load_folder(self, folder=None):
        folder = str(folder or self.file_folder.get() or "")
        if not folder: return
        self.file_folder.set(folder)
        label = Path(folder).name or folder
        self.file_folder_label.set(label + "  ▾")
        self.file_path_note.set(folder if len(folder) <= 46 else folder[:3] + "…" + folder[-38:])
        rows = []
        try:
            with os.scandir(folder) as listing:
                for entry in listing:
                    try:
                        if not entry.is_file(): continue
                        info = entry.stat()
                    except OSError:
                        continue
                    rows.append({"name": entry.name, "path": entry.path, "size": info.st_size, "mtime": info.st_mtime})
        except OSError as exc:
            self.manager_rows = []
            self.fill(self.manager_tree, [])
            self.file_count.set("")
            self.file_note.set("这个目录现在打不开：%s" % exc)
            return
        rows.sort(key=lambda item: item["mtime"], reverse=True)
        self.manager_rows = rows[:400]
        self.fill(self.manager_tree, [(str(index), (item["name"], self.size(item["size"]),
                    time.strftime("%m-%d %H:%M", time.localtime(item["mtime"])))) for index, item in enumerate(self.manager_rows)])
        self.file_count.set("%s 个文件" % len(self.manager_rows))
        if len(rows) > len(self.manager_rows):
            self.file_ops_note.set("只列出最近修改的 400 个；还有 %s 个没显示" % (len(rows) - len(self.manager_rows)))
        self.show_selbar(False)
        self.file_sel_name.set(""); self.file_sel_meta.set("")

    def open_current_folder(self):
        folder = self.file_folder.get()
        if not folder: return
        try: os.startfile(folder)
        except OSError as exc: raise ValueError("打不开这个目录：%s" % exc)

    def file_selected(self, _=None):
        tree = getattr(self, "manager_tree", None)
        if tree is None or not tree.winfo_exists() or not tree.selection():
            self.show_selbar(False); return
        item = self.manager_rows[int(self.selected(tree))]
        self.file_sel_name.set(item["name"])
        self.file_sel_meta.set("%s · %s" % (self.size(item["size"]), time.strftime("%m-%d %H:%M", time.localtime(item["mtime"]))))
        self.file_selection.set("%s · %s" % (item["name"], self.size(item["size"])))
        self.show_selbar(True)

    def send_selected(self):
        selection = self.manager_tree.selection()
        if not selection: raise ValueError("先在表里选一个文件")
        item = self.manager_rows[int(self.selected(self.manager_tree))]
        self.file_choice = dict(item)
        self.prepare_file()

    def search_files(self):
        keyword = self.vars["manager_query"].get().strip().lower()
        if not keyword:
            self.load_folder(); return
        folder = self.file_folder.get()
        self.load_folder(folder)
        rows = [item for item in self.manager_rows if keyword in item["name"].lower()]
        self.manager_rows = rows
        self.fill(self.manager_tree, [(str(index), (item["name"], self.size(item["size"]),
                    time.strftime("%m-%d %H:%M", time.localtime(item["mtime"])))) for index, item in enumerate(rows)])
        self.file_count.set("%s 个文件" % len(rows))
        self.file_note.set("没有含“%s”的文件" % keyword if not rows else "筛选“%s”：%s 个" % (keyword, len(rows)))

    # ---- 刷新 -----------------------------------------------------------
    def refresh_file_page(self, status, cfg, tasks):
        folders = []
        for value in [str(v) for v in status["roots"]] + [str(v) for v in cfg["file_manager"]["roots"]]:
            if value not in folders: folders.append(value)
        names = []
        for folder in folders:
            name = Path(folder).name or folder
            if names.count(name): name = "%s · %s" % (name, folder[-4:])
            names.append(name)
        self.folder_map = dict(zip(names, folders))
        self.folder_menu.values = names
        self.file_root_summary.set("%s 个" % len(folders) if folders else "")
        last = {}
        for task in tasks:
            if task.get("kind") == "qq.file.send":
                folder = self.file_folder_of(task)
                if folder and folder not in last: last[folder] = task
        self.file_note.set("机器人只读" if folders else "还没有授权目录")
        transfers = [t for t in tasks if t.get("kind") in {"qq.file.send", "qq.attachment.receive"}]
        latest = transfers[0] if transfers else None
        self.file_last_note.set("最近：%s · %s" % (self.file_label(latest), self.file_state(latest)) if latest else "还没有发送记录")
        current = self.file_folder.get()
        if folders and current not in folders:
            self.load_folder(folders[0])
        elif folders:
            self.file_folder_label.set((Path(current).name or current) + "  ▾")
        if not folders:
            self.fill(self.manager_tree, []); self.manager_rows = []
            self.file_folder_label.set("还没有授权目录")
            self.file_path_note.set(""); self.file_count.set("")
        if latest and not self.selbar_shown:
            self.file_ops_note.set(self.file_last_note.get())

    @staticmethod
    def file_folder_of(task):
        try: return json.loads(task.get("result") or "{}").get("folder") or ""
        except ValueError: return ""

    @staticmethod
    def file_label(task):
        if task is None: return "文件"
        try: name = json.loads(task.get("result") or "{}").get("file")
        except ValueError: name = None
        return name or ("收到附件" if task.get("kind") == "qq.attachment.receive" else "文件")

    @staticmethod
    def file_state(task):
        if task.get("received"): return "已确认收到"
        return {"queued": "等待发送", "running": "发送中", "platform_accepted": "平台已接受", "uncertain": "待核对",
                "failed": "失败", "cancelled": "已取消", "timed_out": "超时", "interrupted": "上次中断",
                "completed": "已完成"}.get(task.get("state"), task.get("state") or "")

    # ---- 目录与权限 ------------------------------------------------------
    def roots_dialog(self):
        limit = self.app.config.get()["files"]["max_mb"]
        dialog, body, footer = self.dialog_shell(
            "目录与权限",
            "机器人只能读这里列出的目录，改不了也删不了；单文件上限 %s MB。你在这台电脑上可以整理这些目录里的文件。" % limit,
            width=780, height=560)
        self.file_roots_tree = self.table(body, [("folder", "目录"), ("used", "最近发送")], height=10)
        self.file_roots_tree.column("folder", width=470); self.file_roots_tree.column("used", width=120, stretch=False, anchor="e")
        self.file_roots_tree.empty_note.configure(text="还没有授权目录。点右下角添加一个文件夹。")
        self.refresh_roots_dialog()
        close = self.button(footer, "roots_dialog_close", "关闭", lambda: self.close_dialog(dialog))
        close.primary = False; close.ghost = True; close.configure(background=C["bg"], width=72, height=30)
        close.pack(side="left"); close.draw()
        remove = self.button(footer, "file_remove_root", "撤销选中", self.remove_selected_root)
        remove.tone = "soft"; remove.configure(background=C["bg"], width=96, height=30)
        remove.pack(side="right", padx=(8, 0)); remove.draw()
        self.button(footer, "file_add_root", "添加文件夹", self.add_root).pack(side="right")
        return dialog

    def refresh_roots_dialog(self):
        tree = getattr(self, "file_roots_tree", None)
        if tree is None or not tree.winfo_exists(): return
        cfg = self.app.config.get()
        folders = []
        for value in list(cfg["files"]["roots"]) + list(cfg["file_manager"]["roots"]):
            if str(value) not in folders: folders.append(str(value))
        self.fill(tree, [(folder, (folder, "—")) for folder in folders])

    def add_root(self):
        folder = filedialog.askdirectory(parent=self.root, title="选择要授权给机器人读取的文件夹")
        if not folder: return
        if not self.confirm("授权目录", "机器人将能读取并发送这个目录中的文件（只读，不能修改或删除）：\n\n" + folder + "\n\n同时你可以在文件页里整理它。确认授权？"): return
        roots = list(self.app.config.get()["files"]["roots"])
        managed = list(self.app.config.get()["file_manager"]["roots"])
        if folder not in roots: roots.append(folder)
        if folder not in managed: managed.append(folder)
        def save():
            self.app.save_section("files", {"roots": roots})
            self.app.save_section("file_manager", {"roots": managed})
        self.submit(save)

    def remove_selected_root(self):
        tree = getattr(self, "file_roots_tree", None)
        if tree is None or not tree.winfo_exists(): raise ValueError("先在“目录与权限”里打开列表")
        if not tree.selection(): raise ValueError("先选一个要撤销的目录")
        folder = self.selected(tree)
        if not self.confirm("撤销目录授权", "撤销后机器人将不能再读取这个目录，也不再在这里整理：\n" + folder): return
        roots = [r for r in self.app.config.get()["files"]["roots"] if r != folder]
        managed = [r for r in self.app.config.get()["file_manager"]["roots"] if r != folder]
        def save():
            self.app.save_section("files", {"roots": roots})
            self.app.save_section("file_manager", {"roots": managed})
        self.submit(save)

    def open_selected_root(self):
        tree = getattr(self, "file_roots_tree", None)
        if tree is None or not tree.winfo_exists() or not tree.selection(): return
        try: os.startfile(self.selected(tree))
        except OSError as exc: raise ValueError("打不开这个目录：%s" % exc)

    def remove_root(self):
        roots = list(self.app.config.get()["files"]["roots"])
        if not roots: raise ValueError("当前没有已授权的共享目录。")
        tree = getattr(self, "file_roots_tree", None)
        folder = self.selected(tree) if tree is not None and tree.winfo_exists() and tree.selection() else roots[0]
        managed = [r for r in self.app.config.get()["file_manager"]["roots"] if r != folder]
        def save():
            self.app.save_section("files", {"roots": [r for r in roots if r != folder]})
            self.app.save_section("file_manager", {"roots": managed})
        self.submit(save)

    def folder_summary(self, roots, limit=2):
        names = []
        for value in roots:
            path = Path(str(value))
            names.append(path.name or path.drive or str(value))
        if not names: return "无"
        head = " · ".join(names[:limit])
        return head if len(names) <= limit else "%s 等 %s 个" % (head, len(names))

    def save_desktop(self, value=None):
        value = self.vars["desktop"].get() if value is None else value
        if value and not self.confirm("授权桌面", "确认将 Windows 实际桌面加入共享范围？机器人将能读取并发送桌面上的文件。"):
            self.vars["desktop"].set(False); return
        self.submit(lambda: self.app.save_section("files", {"allow_desktop": value}))

    # ---- 示例与测试 ------------------------------------------------------
    def copy_example(self):
        self.root.clipboard_clear(); self.root.clipboard_append("把桌面的操作手册发给我")
        self.message.set("示例已复制，粘贴到 QQ 私聊里就行。")

    def send_test_file(self):
        users = self.app.config.get()["qq"]["allowed_users"]
        if not users: raise ValueError("先在“设置 → QQ 接入”里绑定本人，再发测试文件")
        if not self.app.files.roots(): raise ValueError("先添加一个文件夹，测试文件会放在那里")
        self.file_test_note.set("正在生成并发送…")
        self.submit(lambda: self.app.send_test_file(users[0]),
                    lambda task: (self.file_test_note.set("已提交：任务 %s" % task["id"]),
                                  self.events.put(("file-task", task["id"]))),
                    resource="file-send")

    # ---- 发送 ------------------------------------------------------------
    def files_loaded(self, rows):
        self.file_rows = list(rows)
        if not self.file_rows:
            self.file_choice = None; self.file_selection.set("")
            self.message.set("授权目录里没有可发送的文件。")
            return
        self.file_choice = dict(self.file_rows[0])
        self.file_selection.set("%s · %s" % (self.file_choice["name"], self.size(self.file_choice["size"])))

    @staticmethod
    def size(value):
        return "%.1f KB" % (value / 1024) if value < 1024 ** 2 else "%.2f MB" % (value / 1024 ** 2)

    def prepare_file(self):
        choice = self.file_choice
        if not choice: raise ValueError("先在表里选一个要发送的文件。")
        if not self.app.gateway.connected: raise ValueError("QQ 尚未连接，请先应用已保存配置并连接机器人。")
        users = self.app.config.get()["qq"]["allowed_users"]
        user = self.vars["file_user"].get() if self.vars.get("file_user") and self.vars["file_user"].get() in users else (users[0] if users else "")
        if not user: raise ValueError("请先到“设置 → QQ 接入”绑定本人，再发送文件。")
        self.vars["file_user"].set(user)
        path = choice["path"]

        def ready(fingerprint):
            if not self.confirm("确认发送文件", "文件：%s\n大小：%s\n来自：%s\n\n发送给你的 QQ。文件会上传到 QQ 平台，确认发送？" % (
                    fingerprint["name"], self.size(fingerprint["size"]), Path(fingerprint["path"]).parent)): return
            self.submit(lambda: self.app.send_file(user, fingerprint, task_started=lambda ident: self.events.put(("file-task", ident))),
                        lambda _: self.file_ops_note.set("已提交给 QQ 平台，请在手机上核对是否收到。"),
                        action="file_send_selected", resource="file-send")

        self.submit(lambda: self.app.files.fingerprint(Path(path)), ready, action="file_send_selected", resource="file-send")

    def cancel_file_send(self):
        if not self.file_task_id: raise ValueError("还没有正在进行的发送任务。")
        self.app.tasks.cancel(self.file_task_id)
        self.file_ops_note.set("已请求取消；正在等待的网络请求返回后停止后续提交。")
