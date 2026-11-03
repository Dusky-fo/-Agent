"""Native Tk desktop; one host process, worker threads, no HTTP or WebView."""
import json
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from .paths import resource_dir
from .qq import QQError
from .desktop_skin import C, FONT, SPEC, ActionCard, Badge, CheckToggle, MaskedSecretEntry, NavItem, PageStack, RoundButton, RoundedPanel, apply_styles, dark_titlebar, load_theme, set_theme
from . import __version__
from .build_info import REVISION
from .desktop_services import ServiceScreens
from .tasks import TaskCancelled,TaskTimedOut
from .desktop_ux import ExperienceScreens,KeepDraft,task_sentence
from .desktop_permissions import PermissionScreens
from .desktop_jobs import DesktopJobs
from .desktop_records import RecordScreens
from .desktop_qq import QQScreens
from .desktop_files import FileScreens
from .desktop_monitor import MonitorScreens


class DesktopApp(DesktopJobs,RecordScreens,QQScreens,FileScreens,MonitorScreens,ExperienceScreens,PermissionScreens,ServiceScreens):
    def __init__(self, root, runtime, *, tray=True, confirm=None, open_url=None, hotkey_factory=None,draft_choice=None):
        self.root, self.app = root, runtime
        self.confirm = confirm or (lambda title, text: messagebox.askyesno(title, text, parent=root))
        self.open_url = open_url or self._open_official_url
        self.events = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop-action")
        self.snapshot_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="desktop-state")
        self.pending_actions={};self.resources=set();self._active_action_key=None
        self.snapshot_future=None;self.next_snapshot=0;self.last_snapshot=None;self.state_epoch=0
        self.draft_choice=draft_choice or self.default_draft_choice
        self.busy = False
        self.closing = False
        self.icon = None
        self.tray_ready = False
        self.hotkey_factory = hotkey_factory
        self.click_hotkey = None; self.armed_plan = None; self.automation_generation = 0
        self.automation_preferences = {}
        try:
            preferences = runtime.directory / "automation_settings.json"
            if preferences.is_file() and preferences.stat().st_size <= 65536:
                data = json.loads(preferences.read_text(encoding="utf-8"))
                if isinstance(data,dict):
                    self.automation_preferences = {k:v for k,v in data.items() if k == "image" and isinstance(v,dict) and all(isinstance(key,str) and isinstance(value,str) and len(value)<=1024 for key,value in v.items())}
        except (OSError,ValueError):pass
        self.pages, self.buttons, self.vars = {}, {}, {}
        self.scroll_frames = {}
        self.last_tables = {}
        self.process_rows, self.file_rows, self.plugin_rows = [], [], []
        self.chosen = set(runtime.config.get()["monitor"]["process_names"])
        self.theme = load_theme(runtime.directory); set_theme(self.theme)
        self.root.title("灵梭 %s · 本地桌面助手" % __version__)
        from .window_layout import initial_size
        width,height=initial_size(root.winfo_screenwidth(),root.winfo_screenheight())
        self.root.geometry("%sx%s"%(width,height))
        self.root.resizable(False,False)
        self.root.minsize(width,height)
        self.root.maxsize(width,height)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<MouseWheel>", self._scroll)
        apply_styles(root)
        root.bind("<Map>", lambda event: dark_titlebar(root) if event.widget == root else None, add=True)
        body = ttk.Frame(root); body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, background=C["card2"], width=SPEC["sidebar_width"])
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, background=C["card2"]); brand.pack(fill="x", padx=22, pady=(26, 25))
        from PIL import ImageTk
        from .desktop_skin import rounded_image
        logo = tk.Canvas(brand, width=35, height=35, background=C["card2"], highlightthickness=0)
        logo.pack(side="left", padx=(0, 10))
        self.logo_image = ImageTk.PhotoImage(rounded_image(35, 35, 10, C["accent"], C["accent2"]), master=root)
        logo.create_image(0, 0, anchor="nw", image=self.logo_image)
        logo.create_text(18, 18, text="LS", fill="white", font=(FONT, 11, "bold"))
        root.iconphoto(True, self.logo_image)
        brand_text = tk.Frame(brand, background=C["card2"]); brand_text.pack(side="left")
        tk.Label(brand_text, text="灵梭", font=(FONT, 12, "bold"), fg=C["txt"], bg=C["card2"], anchor="w").pack(anchor="w")
        tk.Label(brand_text, text="监控 · 收发 · 整理", font=(FONT, 8), fg=C["sub"], bg=C["card2"]).pack(anchor="w")
        self.nav = {}; self.page_labels = {}
        for key, label in (("home", "总览"), ("monitor", "监控"), ("files", "文件"),
                           ("tasks","记录"),("automation", "工具"), ("plugins", "插件"), ("settings", "设置")):
            item = NavItem(self.sidebar, label, lambda p=key: self.show(p))
            item.pack(fill="x", padx=14, pady=2); self.nav[key] = item; self.page_labels[key] = label
        self.side_status = tk.StringVar(value="正在读取本机状态")
        tk.Label(self.sidebar, textvariable=self.side_status, fg=C["sub"], bg=C["card2"], font=(FONT, 8), anchor="w").pack(side="bottom", fill="x", padx=22, pady=20)
        content = ttk.Frame(body, padding=(24, 20, 24, 14)); content.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(content); header.pack(fill="x", pady=(0, 10))
        self.page_title = tk.StringVar(value="总览")
        ttk.Label(header, textvariable=self.page_title, style="Title.TLabel").pack(side="left")
        self.badges = [Badge(header) for _ in range(4)]
        for badge in reversed(self.badges): badge.pack(side="right", padx=(8, 0))
        self.banner = tk.StringVar(value="正在读取本机状态…")
        ttk.Label(content, textvariable=self.banner, font=(FONT, 9)).pack(anchor="w", pady=(0, 8))
        self.error_bar=ttk.Frame(content);self.error_var=tk.StringVar()
        ttk.Label(self.error_bar,textvariable=self.error_var,wraplength=700).pack(side="left",fill="x",expand=True)
        self.button(self.error_bar,"error_dismiss","关闭提示",self.error_bar.pack_forget).pack(side="right",padx=6)
        self.setup_subnav(content)
        self.tabs = PageStack(content); self.tabs.pack(fill="both", expand=True)
        for key, label in (("home", "首页"), ("monitor", "监控"), ("files", "文件"),
                           ("tasks","任务记录"),
                           ("notifications", "绑定 QQ 机器人"), ("automation", "鼠标工具"), ("plugins", "插件"),
                           ("settings", "设置")):
            page = ttk.Frame(self.tabs)
            self.tabs.add(page, text=label); self.pages[key] = page
        self.tabs.bind("<<NotebookTabChanged>>", self.page_changed)
        self.message = tk.StringVar(value="本地监控可独立使用。首次使用请从首页待办开始。")
        footer = ttk.Frame(content); footer.pack(side="bottom", fill="x", pady=(12, 0), before=self.tabs)
        self.button(footer, "exit", "退出灵梭", self.close).pack(side="right")
        self.button(footer, "tray", "收起到托盘", self.hide).pack(side="right", padx=8)
        note = ttk.Label(footer, textvariable=self.message, wraplength=530, font=(FONT, 9))
        note.pack(side="left", fill="x", expand=True)
        note.bind("<Configure>", lambda event: note.configure(wraplength=max(160, event.width - 12)))
        self._home(); self._monitor(); self._files(); self._notifications()
        self._automation(); self._plugins(); self._settings()
        self._task_page()
        if tray:
            self._start_tray()
        self.poll_id = self.root.after(50, self._poll)
        self.refresh()


    @staticmethod
    def _open_official_url(url):
        # Only an explicit help action opens the external official site.
        import webbrowser
        webbrowser.open(url)

    def button(self, parent, key, label, fn):
        top=parent.winfo_toplevel()
        if isinstance(top,tk.Toplevel) and getattr(fn,"__name__",None)=="destroy" and getattr(fn,"__self__",None) is top:
            fn=lambda d=top:self.close_dialog(d)
        guarded=self.guard_action(key,fn)
        primary = key.startswith("setting_") or key in {"read_processes","home_qq","home_monitor","home_files","home_records","home_activity_toggle","quick_connect","quick_pair_start","quick_pair_confirm","permissions_save","save_monitor", "connect_qq", "qq_save", "send_file", "add_root", "advanced_save", "first_help", "test_notify"}
        widget = RoundButton(parent, label, guarded, primary=primary)
        self.buttons[key] = widget
        if isinstance(top,tk.Toplevel) and (key.endswith("_save") or key in {"private_save","permissions_save"}):top.save_action=(key,guarded)
        return widget

    def guard_action(self,key,fn):
        def guarded():
            if self.closing or key in self.pending_actions:
                self.message.set("这项操作正在处理，其他不冲突的功能仍可使用。")
                return
            previous=self._active_action_key;self._active_action_key=key
            try:
                fn()
            except KeepDraft:pass
            except (ValueError, PermissionError, OSError, QQError) as exc:
                self.message.set(str(exc)); self.show_error(str(exc))
            except Exception:
                self.show_error("操作未完成，请查看通知记录或对应帮助。")
            finally:self._active_action_key=previous
        return guarded

    def show_error(self, text):
        self.error_var.set(text);self.message.set(text)
        dialog=getattr(self,"active_dialog",None)
        if dialog is not None and dialog.winfo_exists() and hasattr(dialog,"notice_var"):dialog.notice_var.set(text)
        self.error_bar.pack(fill="x",pady=(0,8),before=self.subnav)

    def label(self, parent, text):
        label = ttk.Label(parent, text=text, wraplength=990)
        label.pack(anchor="w", fill="x", pady=(0, 10))
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(200, event.width)))

    def scroll_area(self, parent):
        """Keep onboarding actions reachable on small laptop displays."""
        canvas = tk.Canvas(parent, highlightthickness=0, background=C["bg"])
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        bar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=bar.set)
        frame = ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))
        self.scroll_frames[frame] = canvas
        return frame

    def _scroll(self, event):
        widget = event.widget
        # Lists and text widgets retain their own scroll handling.
        if isinstance(widget, (ttk.Treeview, tk.Text, tk.Listbox)): return
        while widget is not None:
            if widget in self.scroll_frames:
                self.scroll_frames[widget].yview_scroll(int(-event.delta / 120), "units")
                return
            widget = getattr(widget, "master", None)

    def row(self, parent):
        row = ttk.Frame(parent); row.pack(fill="x", pady=5)
        return row

    def entry(self, parent, key, label, value="", secret=False):
        row = self.row(parent)
        ttk.Label(row, text=label, width=21).pack(side="left")
        var = tk.StringVar(value=value); self.vars[key] = var
        widget = ttk.Entry(row, textvariable=var, show="•" if secret else "")
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def table(self, parent, names, height=7, expand=True, style=None):
        box = ttk.Frame(parent); box.pack(fill="both" if expand else "x", expand=expand, pady=5)
        tree = ttk.Treeview(box, columns=tuple(k for k, _ in names), show="headings", height=height,
                            style=style or "Treeview")
        for key, text in names:
            tree.heading(key, text=text); tree.column(key, width=180, minwidth=90)
        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew"); scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1); box.columnconfigure(0, weight=1)
        keys = tuple(k for k, _ in names)
        hints = {("name", "count"): "点击“读取当前进程”后选择程序", ("name", "state"): "尚未添加监控程序",
                 ("path",): "尚未授权目录", ("name", "size", "path"): "搜索授权范围，或选择文件",
                 ("id", "user", "state", "error"): "绑定接收人后，可发送带编号的测试通知",
                 ("time", "kind", "text"): "暂无运行记录 · 请先添加监控程序",
                 ("name", "permissions", "state"): "尚无插件 · 基础功能无需额外插件"}
        tree.empty_note = tk.Label(tree, text=hints.get(keys, "暂无内容"), background=C["card"], foreground=C["sub"], font=(FONT, 9), wraplength=350)
        tree.empty_note.place(relx=.5, rely=.53, anchor="center")
        return tree

    def fill(self, tree, rows):
        normalized = [(str(key), tuple(str(v) for v in values)) for key, values in rows]
        if self.last_tables.get(tree) == normalized:
            return
        selection = tree.selection()
        tree.delete(*tree.get_children())
        for key, values in normalized:
            tree.insert("", "end", iid=key, values=values)
        tree.selection_set([key for key in selection if tree.exists(key)])
        if normalized: tree.empty_note.place_forget()
        else: tree.empty_note.place(relx=.5, rely=.53, anchor="center")
        self.last_tables[tree] = normalized

    def selected(self, tree):
        selection = tree.selection()
        if not selection:
            raise ValueError("请先选择列表中的一项。")
        return selection[0]

    def show(self, page):
        self.tabs.select(self.pages[page])
        self.page_changed()

    def reset_page_scroll(self):
        """Keep every switch at the top of the page it opens."""
        for canvas in list(getattr(self, "scroll_frames", {}).values()):
            try:
                if canvas.winfo_exists():
                    canvas.yview_moveto(0)
            except tk.TclError:
                continue

    def page_changed(self, _=None):
        selected = self.tabs.select()
        for key, frame in self.pages.items():
            active = str(frame) == selected
            if active:self.update_navigation(key)
        # A page opened after another one starts at its own top, never at the old scroll offset.
        self.reset_page_scroll()
        self.tabs.update_idletasks()
        # A message belongs to the page that produced it; do not carry it across navigation.
        if self.message.get() and getattr(self, "_message_page", None) not in (None, self.current_page()):
            self.message.set("")
        self._message_page = self.current_page()
        self.next_snapshot=0

    def open_notifications(self, index=0):
        self.show("notifications")
        if hasattr(self,"qq_wizard_tabs"):self.qq_wizard_tabs.select(0)

    def _home(self):
        self.home_page()

    def _automation(self):
        page=self.pages["automation"]
        self.automation_status=tk.StringVar(value="图片识别：未启动 · 未设快捷键")
        self.automation_tool_state=tk.StringVar(value="未配置")
        head=tk.Frame(page);head.pack(fill="x",pady=(0,10))
        tk.Label(head,text="三个工具各自独立，都需要你先配置一次；软件不会替你点击、抓取或提交任何东西。",
                 font=(FONT,9),fg=C["sub"],bg=C["bg"],anchor="w").pack(side="left",fill="x",expand=True)
        tk.Label(head,textvariable=self.automation_status,font=(FONT,9),fg=C["muted"],bg=C["bg"],anchor="e").pack(side="right")

        grid=tk.Frame(page,bg=C["bg"]);grid.pack(fill="x",pady=(0,14))
        for column in range(3):grid.columnconfigure(column,weight=1,uniform="tools")
        specs=(("图片识别点击","在指定屏幕区域里找一张目标图片，只有唯一匹配时才点击。可以先只识别一次，不点。",
                "打开设置",lambda:self.automation_dialog("image"),True,self.automation_tool_state),
               ("网页搜索","输入关键词，用系统浏览器打开 Bing 或百度的结果页。软件只负责打开，不读取、不总结。",
                "搜索",self.web_search_dialog,False,None),
               ("授权网页读取","读一次你指定的 HTTPS 网页纯文本，授权两分钟内有效，不带 Cookie、不执行脚本。",
                "读取",self.web_task_dialog,False,None))
        for column,(title,desc,action,command,primary,state) in enumerate(specs):
            card=RoundedPanel(grid,pad=16,height=178)
            card.grid(row=0,column=column,sticky="nsew",padx=(0,14) if column<2 else 0)
            tk.Label(card.body,text=title,font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(fill="x")
            body=tk.Label(card.body,text=desc,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="nw",justify="left",
                          wraplength=286)
            body.pack(fill="both",expand=True,pady=(8,0))
            card.bind("<Configure>",lambda event,label=body:label.configure(wraplength=max(120,event.width-2*16)),add="+")
            foot=tk.Frame(card.body,bg=C["card2"]);foot.pack(fill="x",pady=(10,0))
            key="tool_image" if primary else ("setting_web" if command==self.web_search_dialog else "setting_webtasks")
            button=self.button(foot,key,action,command)
            if primary:
                button.primary=True;button.draw()
            else:
                button.tone="soft";button.configure(background=C["card2"],width=72,height=30);button.draw()
            button.pack(side="left")
            tk.Label(foot,textvariable=state if state else tk.StringVar(value="无需配置" if command==self.web_search_dialog else "每次单独授权"),
                     font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="e").pack(side="right")

        notes=RoundedPanel(page,pad=16,height=104);notes.pack(fill="x",pady=(0,12))
        row=tk.Frame(notes.body,bg=C["card2"]);row.pack(fill="both",expand=True)
        for index,(headline,text) in enumerate((("停止方式","同一个快捷键开始 / 停止，Esc 是紧急停止；改任何参数都会自动解除已应用的配置。"),
                                               ("识别失败","缩放、分辨率、画面颜色变了都会失效；多个匹配时不点，先缩小识别区域。"),
                                               ("权限","以管理员运行灵梭时，插件也拿到管理员权限；插件清单不是沙箱，只装信得过的。"))):
            block=tk.Frame(row,bg=C["card2"]);block.pack(side="left",fill="both",expand=True,padx=(0,20) if index<2 else 0)
            tk.Label(block,text=headline,font=(FONT,9,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(fill="x")
            note=tk.Label(block,text=text,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="nw",justify="left",wraplength=300)
            note.pack(fill="x",pady=(3,0))
            notes.bind("<Configure>",lambda event,label=note:label.configure(wraplength=max(100,(event.width-2*16)//3-14)),add="+")

        tail=tk.Frame(page,bg=C["bg"]);tail.pack(fill="x")
        tk.Label(tail,text="插件放在本机插件目录，审查并信任后重启生效",font=(FONT,9),fg=C["muted"],bg=C["bg"],anchor="w").pack(side="left")
        info=self.button(tail,"tool_runtime","程序数据位置",self.runtime_info)
        info.primary=False;info.ghost=True;info.configure(background=C["bg"],width=96,height=26)
        info.pack(side="right");info.draw()
        open_plugins=self.button(tail,"tool_plugins","插件页 ›",lambda:self.show("plugins"))
        open_plugins.primary=False;open_plugins.ghost=True;open_plugins.configure(background=C["bg"],width=68,height=26)
        open_plugins.pack(side="right",padx=(0,8));open_plugins.draw()

    def automation_dialog(self, mode="image"):
        if mode!="image":raise ValueError("普通鼠标连点功能已移除")
        self.disarm_automation()
        dialog,body=self.dialog("图片识别点击",width=780,height=720)
        self.automation_mode="image"
        footer=ttk.Frame(body);footer.pack(side="bottom",fill="x",pady=(10,0))
        def close():self.disarm_automation();dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW",close)
        dialog.bind("<Destroy>",lambda event:self.disarm_automation() if event.widget==dialog else None,add=True)
        self.button(footer,"automation_toggle","开始",self.toggle_automation).pack(side="right")
        self.button(footer,"automation_apply","应用配置",self.apply_automation).pack(side="right",padx=8)
        self.button(footer,"automation_close","关闭",close).pack(side="left")
        ttk.Label(body,textvariable=self.automation_status,wraplength=700).pack(side="bottom",anchor="w",pady=6)
        page=self.scroll_area(body)
        self.label(page,"先填写图片、识别范围和启停快捷键，再应用配置；应用不会点击。只有唯一匹配才点击，Esc 紧急停止。")
        row=self.row(page);ttk.Label(row,text="启停快捷键（必选）",width=18).pack(side="left")
        self.vars["click_hotkey"]=tk.StringVar()
        ttk.Combobox(row,textvariable=self.vars["click_hotkey"],values=tuple("F%s"%n for n in range(2,12)),state="readonly",width=12).pack(side="left")
        self.entry(page,"click_template","目标图片")
        def choose():
            path=filedialog.askopenfilename(parent=dialog,title="选择与当前显示比例一致的目标小图",filetypes=[("目标图片","*.png *.jpg *.jpeg *.bmp")])
            if path:self.vars["click_template"].set(path)
        row=self.row(page)
        self.button(row,"choose_template","选择目标图片",choose).pack(side="left")
        self.button(row,"probe_template","只识别一次（不点击）",self.probe_automation).pack(side="left",padx=8)
        self.label(page,"缩放和颜色变化会影响匹配。多个匹配时不点击，请缩小范围。")
        for key,label,value in (("region_x","区域 X",""),("region_y","区域 Y",""),("region_w","区域宽",""),("region_h","区域高",""),("click_threshold","相似度（0.80–1.00）","0.98"),("click_interval","识别间隔（毫秒）","500"),("click_count","最多点击次数","10"),("click_duration","最长运行（秒）","60"),("click_delay","启动延时（秒）","3")):
            self.entry(page,key,label,value)
        row=self.row(page);ttk.Label(row,text="鼠标按键",width=18).pack(side="left")
        self.vars["click_button"]=tk.StringVar(value="左键")
        ttk.Combobox(row,textvariable=self.vars["click_button"],values=("左键","右键"),state="readonly",width=12).pack(side="left")
        fields=["click_interval","click_count","click_duration","click_delay","click_button","click_hotkey","click_template","click_threshold","region_x","region_y","region_w","region_h"]
        self.automation_fields=fields
        saved=self.automation_preferences.get("image",{})
        for key in fields:
            if key in saved:self.vars[key].set(saved[key])
        self.automation_status.set("图片识别尚未启动。设置后手动开始。")
        traces=[(self.vars[key],self.vars[key].trace_add("write",lambda *_:self.disarm_automation())) for key in fields]
        def cleanup(event):
            if event.widget==dialog:
                for variable,trace in traces:variable.trace_remove("write",trace)
        dialog.bind("<Destroy>",cleanup,add=True)


    def automation_plan(self):
        from .automation import ClickPlan
        try:
            values = {"mode":self.automation_mode,"interval_ms":int(self.vars["click_interval"].get()),
                      "count":int(self.vars["click_count"].get()),"duration_s":int(self.vars["click_duration"].get()),
                      "delay_s":float(self.vars["click_delay"].get()),"button":"left" if self.vars["click_button"].get() == "左键" else "right"}
            values.update(template=self.vars["click_template"].get(),threshold=float(self.vars["click_threshold"].get()),region=tuple(int(self.vars[k].get()) for k in ("region_x","region_y","region_w","region_h")))
        except ValueError:raise ValueError("请检查数字参数，不要留空或填写文字。") from None
        return ClickPlan(**values).validate()

    def disarm_automation(self):
        self.automation_generation += 1
        if self.click_hotkey:self.click_hotkey.stop(); self.click_hotkey = None
        self.app.automation.stop(); self.armed_plan = None
        if hasattr(self,"automation_status"):self.automation_status.set("图片识别：未启动")
        if hasattr(self,"automation_tool_state"):self.automation_tool_state.set("未配置")

    def apply_automation(self):
        plan = self.automation_plan()
        key = self.vars["click_hotkey"].get()
        if key not in {"F%s" % n for n in range(2,12)}:raise ValueError("请先设置 F2–F11 中的一个启停快捷键")
        self.disarm_automation()
        from .automation import HotkeyToggle
        listener = HotkeyToggle(**({"factory":self.hotkey_factory} if self.hotkey_factory else {}))
        generation = self.automation_generation
        try:
            listener.bind(key,lambda:self.events.put(("automation-toggle",generation)))
            preferences = {k:v for k,v in self.automation_preferences.items() if k=="image"}
            preferences[self.automation_mode] = {field:self.vars[field].get() for field in self.automation_fields}
            target = self.app.directory / "automation_settings.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(preferences,ensure_ascii=False,indent=2),encoding="utf-8")
            temporary.replace(target);self.automation_preferences = preferences
        except Exception:listener.stop();raise
        self.click_hotkey = listener; self.armed_plan = plan
        self.app.automation.update(message="已就绪，按 %s 或开始按钮启动；同一个键 / 按钮停止。"%key)
        self.automation_status.set(self.app.automation.status()["message"])
        if hasattr(self,"automation_tool_state"):self.automation_tool_state.set("已就绪 · %s"%key)

    def toggle_automation(self):
        if self.app.automation.status()["running"]:
            self.app.automation.stop();return
        if self.armed_plan is None:raise ValueError("尚未启用。请填写目标参数与快捷键，再点击应用配置；不会默认启动。")
        if "mouse-probe" in self.resources:raise ValueError("当前识别操作尚未完成，请稍候再开始")
        self.app.automation.start(self.armed_plan)

    def probe_automation(self):
        if self.app.automation.status()["running"]:raise ValueError("请先停止当前任务，再执行单次识别")
        plan = self.automation_plan()
        def probe():
            from .automation import WindowsMouse
            engine = self.app.automation; template = engine.matcher.load(plan.template)
            image = (engine.backend or WindowsMouse()).capture(plan.region)
            point,note = engine.matcher.locate(image,template,plan.threshold)
            return note + ("；目标中心：%s, %s（没有点击）" % (point[0]+plan.region[0],point[1]+plan.region[1]) if point else "；没有点击")
        self.submit(probe,self.automation_status.set,resource="mouse-probe")

    def _plugins(self):
        page = self.pages["plugins"]
        head=tk.Frame(page);head.pack(fill="x",pady=(0,12))
        tk.Label(head,text="受信插件与灵梭同权限；以管理员运行时它也是管理员。清单不是沙箱，启用必须先审查再信任，重启生效。",
                 font=(FONT,9),fg=C["sub"],bg=C["bg"],anchor="w").pack(side="left",fill="x",expand=True)
        tk.Label(head,text="基础功能无需插件",font=(FONT,9),fg=C["muted"],bg=C["bg"],anchor="e").pack(side="right")

        card=RoundedPanel(page,pad=16,height=300);card.pack(fill="both",expand=True,pady=(0,12))
        meta=tk.Frame(card.body,bg=C["card2"]);meta.pack(fill="x",pady=(0,8))
        tk.Label(meta,text="插件目录",font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w").pack(side="left")
        tk.Label(meta,text=str(self.app.plugins.directory),font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w").pack(side="left",padx=(8,0))
        self.plugin_tree = self.table(card.body, [("name", "插件 / 版本"), ("purpose", "用途"), ("source", "声明来源（未核验）"), ("state", "状态 / 原因")])
        self.plugin_tree.empty_note.configure(text="还没有插件。每个插件文件夹需包含 manifest.json 与 plugin.py")
        self.plugin_note = tk.StringVar(value="把完整插件文件夹放进上面的目录，再刷新。")
        tk.Label(card.body,textvariable=self.plugin_note,font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w").pack(fill="x",pady=(8,0))

        footer=tk.Frame(page,bg=C["bg"]);footer.pack(fill="x")
        self.button(footer,"plugin_install","安装本地插件",self.install_plugin).pack(side="left")
        refresh=self.button(footer,"plugins_refresh","刷新",self.refresh_plugins)
        refresh.tone="soft";refresh.configure(background=C["bg"],width=64,height=30)
        refresh.pack(side="left",padx=(8,0));refresh.draw()
        tools=self.button(footer,"plugin_tools","插件工具",self.plugin_tools)
        tools.primary=False;tools.ghost=True;tools.configure(background=C["bg"],width=76,height=28)
        tools.pack(side="left",padx=(10,0));tools.draw()
        disable=self.button(footer,"plugins_disable","禁用选中",lambda: self.configure_plugin(False))
        disable.tone="soft";disable.configure(background=C["bg"],width=88,height=30)
        disable.pack(side="right",padx=(8,0));disable.draw()
        self.button(footer,"plugins_enable","审查并信任",lambda: self.configure_plugin(True)).pack(side="right")
        self.refresh_plugins()

    def install_plugin(self):
        folder = filedialog.askdirectory(parent=self.root, title="选择包含 manifest.json 与 plugin.py 的完整插件目录")
        if not folder: return
        if not self.confirm("安装本地插件", "只复制这个插件包，不自动信任、启用或运行代码：\n" + folder + "\n\n确认安装？"): return
        def done(ident):
            self.refresh_plugins(); self.message.set("%s 已安装但未启用，请先审查代码和权限，再决定是否信任。" % ident)
        self.submit(lambda: self.app.plugins.install(folder), done)

    def plugin_tools(self):
        ident = self.selected(self.plugin_tree)
        row = next(r for r in self.app.plugins.scan() if r["id"] == ident)
        _, page = self.dialog("插件工具：" + ident, width=760)
        if not row.get("loaded"):
            self.label(page, "工具声明：" + "、".join(row.get("declared_tools", [])) + "\n此插件尚未加载。审查并信任启用后，请退出灵梭再启动。不会为了预览而执行代码。")
            return
        tools = row["tools"]
        selected = tk.StringVar(value=tools[0]["name"] if tools else "")
        ttk.Combobox(page, textvariable=selected, values=[t["name"] for t in tools], state="readonly").pack(fill="x", pady=8)
        self.label(page, "参数使用 JSON 对象填写；无参数工具填 {}。只执行明确选择的已加载工具，不自动操作任何游戏。")
        arguments = tk.Text(page, height=5, background=C["card"], foreground=C["txt"], insertbackground=C["txt"], font=(FONT, 10))
        arguments.pack(fill="x"); arguments.insert("end", "{}")
        output = tk.Text(page, height=10, wrap="char", background=C["card"], foreground=C["txt"], font=(FONT, 10)); output.pack(fill="both", expand=True, pady=10)
        def call():
            name = selected.get()
            try: params = json.loads(arguments.get("1.0", "end-1c"))
            except ValueError: raise ValueError("工具参数须为 JSON 对象，例如 {}") from None
            if not self.confirm("执行插件工具", "插件拥有宿主权限。确认执行所选工具？\n" + name): return
            def done(result):
                if output.winfo_exists(): output.delete("1.0", "end"); output.insert("end", json.dumps(result, ensure_ascii=False, indent=2))
            self.submit(lambda: self.app.tools.execute("plugins.invoke",{"ident":ident,"tool":name,"arguments":json.dumps(params,ensure_ascii=False)},self.local_caller("plugins.execute")),done)
        self.button(page, "plugin_call", "确认调用所选工具", call).pack(anchor="w")

    def refresh_plugins(self):
        self.plugin_rows = self.app.plugins.scan()
        self.fill(self.plugin_tree, [(r["id"], (r["name"] + " " + r.get("version", ""),str(r.get("description","作者未说明用途"))[:160],str(r.get("source","本地安装，来源未核验"))[:160],
                   r.get("error") or ("已加载" if r.get("loaded") else "等待重启" if r.get("enabled") and r.get("trusted") else "未启用 / 待信任"))) for r in self.plugin_rows])

    def configure_plugin(self, enabled):
        ident = self.selected(self.plugin_tree)
        row = next(r for r in self.plugin_rows if r["id"] == ident)
        if enabled and not self.confirm("信任插件代码", "插件：%s\n权限声明：%s\n内容指纹：%s\n\nPython 插件能以你的账户权限执行代码。确认已审查来源和代码，并信任启用？" % (ident, row.get("permissions"), row.get("hash", "无效"))): return
        def done(_):
            self.refresh_plugins(); self.message.set("插件设置已保存。请点击退出灵梭后重新启动，使启用或完整停用生效。")
        def save():
            with self.app.settings_lock:self.app.plugins.configure(ident,enabled,enabled)
        self.submit(save,done,resource="settings")

    def _settings(self):
        self.settings_page()

    def dialog_shell(self, title, intro, *, width=760, height=600):
        """Shared secondary-page chrome: one sentence, one card, one action row."""
        dialog, body = self.dialog(title, width, height)
        tk.Label(body, text=intro, font=(FONT, 9), fg=C["sub"], bg=C["bg"], anchor="w", justify="left",
                 wraplength=max(300, width - 96)).pack(fill="x", pady=(0, 16))
        card = RoundedPanel(body, pad=16, height=max(140, height - 210))
        card.pack(fill="both", expand=True)
        footer = tk.Frame(body, bg=C["bg"]); footer.pack(fill="x", pady=(16, 0))
        return dialog, card.body, footer

    def dialog_actions(self, footer, dialog, key, label, command):
        close = self.button(footer, key + "_close", "关闭", lambda: self.close_dialog(dialog))
        close.primary = False; close.ghost = True; close.configure(background=C["bg"], width=72, height=30)
        close.pack(side="left"); close.draw()
        action = self.button(footer, key, label, command)
        action.pack(side="right")
        return action

    def dialog(self, title, width=680, height=620):
        if getattr(self, "active_dialog", None) and self.active_dialog.winfo_exists():
            if not self.allow_close_dialog(self.active_dialog):raise KeepDraft()
            self.active_dialog.destroy()
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
        dialog.title(title); dialog.configure(background=C["bg"])
        dialog.transient(self.root)
        from .window_layout import center_dialog
        center_dialog(self.root, dialog, width, height)
        dialog.bind("<Map>",lambda event:dark_titlebar(dialog) if event.widget==dialog else None)
        dialog.bind("<MouseWheel>", self._scroll)
        self.active_dialog = dialog
        body = ttk.Frame(dialog, padding=22); body.pack(fill="both", expand=True)
        dialog.notice_var=tk.StringVar();ttk.Label(body,textvariable=dialog.notice_var,wraplength=max(280,width-70)).pack(side="bottom",fill="x",pady=4)
        dialog.protocol("WM_DELETE_WINDOW",lambda:self.close_dialog(dialog))
        baseline_job=dialog.after_idle(lambda:self.mark_dialog_saved(dialog) if dialog.winfo_exists() else None)
        def cancel_baseline(event):
            if event.widget is dialog:
                try:dialog.after_cancel(baseline_job)
                except tk.TclError:pass
        dialog.bind("<Destroy>",cancel_baseline,add=True)
        dialog.deiconify()
        return dialog, body

    def advanced_dialog(self):
        dialog, page, footer = self.dialog_shell("高级设置", "未运行的程序按文件名添加。其他参数通常保持默认即可。", width=700, height=620)
        cfg = self.app.config.get()
        line = tk.Frame(page, bg=C["card2"]); line.pack(fill="x", pady=(0, 10))
        tk.Label(line, text="手动添加程序", font=(FONT, 9), fg=C["sub"], bg=C["card2"], width=14, anchor="w").pack(side="left")
        self.vars["manual_process"] = tk.StringVar()
        ttk.Entry(line, textvariable=self.vars["manual_process"]).pack(side="left", fill="x", expand=True)
        def add():
            self.manual_add(); self.close_dialog(dialog)
        pick = self.button(line, "manual_add", "加入", add)
        pick.tone = "soft"; pick.configure(background=C["card2"], width=56, height=28)
        pick.pack(side="left", padx=(8, 0)); pick.draw()
        for key, label, value in (("interval", "采集间隔（秒）", cfg["monitor"]["interval"]),
                                   ("max_mb", "文件上限（MB）", cfg["files"]["max_mb"]),
                                   ("max_depth", "文件搜索深度", cfg["files"]["max_depth"]),
                                   ("cooldown", "通知合并窗口（秒）", cfg["notifications"]["cooldown"]),
                                   ("attempts", "通知最多尝试次数", cfg["notifications"]["max_attempts"])):
            row = tk.Frame(page, bg=C["card2"]); row.pack(fill="x", pady=5)
            tk.Label(row, text=label, font=(FONT, 9), fg=C["sub"], bg=C["card2"], width=18, anchor="w").pack(side="left")
            self.vars[key] = tk.StringVar(value=str(value))
            ttk.Entry(row, textvariable=self.vars[key]).pack(side="left", fill="x", expand=True)
        tk.Label(page, textvariable=self.message, font=(FONT, 9), fg=C["muted"], bg=C["card2"], anchor="w", justify="left",
                 wraplength=560).pack(fill="x", pady=(12, 0))
        close = self.button(footer, "advanced_done", "关闭", lambda: self.close_dialog(dialog))
        close.primary = False; close.ghost = True; close.configure(background=C["bg"], width=72, height=30)
        close.pack(side="left"); close.draw()
        self.button(footer, "advanced_save", "保存高级设置", self.save_advanced).pack(side="right")

    def data_dialog(self):
        dialog, page, footer = self.dialog_shell("本地资料：仅软件访问",
            "这里的目录只允许本机软件访问，QQ 指令不能读取或修改。发送内容需要在本机选择接收人并预览确认。支持 1 MB 内 UTF-8 文本资料。",
            width=880, height=720)
        top = tk.Frame(page, bg=C["card2"]); top.pack(fill="x")
        for key, text, fn in (("private_root", "授权文件夹", self.add_private_root),
                              ("private_open", "打开文件", self.open_local_document),
                              ("runtime_info", "程序位置", self.runtime_info)):
            btn = self.button(top, key, text, fn)
            btn.tone = "soft"; btn.configure(background=C["card2"], width=88, height=28)
            btn.pack(side="left", padx=(0, 8)); btn.draw()
        self.private_scope = tk.StringVar(value="仅软件访问：" + ("；".join(self.app.config.get()["local_data"]["roots"]) or "尚未授权，请先选择资料文件夹"))
        tk.Label(page, textvariable=self.private_scope, font=(FONT, 9), fg=C["muted"], bg=C["card2"], anchor="w", justify="left",
                 wraplength=780).pack(fill="x", pady=(12, 4))
        self.document_path = tk.StringVar(value="未打开文件")
        tk.Label(page, textvariable=self.document_path, font=(FONT, 9), fg=C["sub"], bg=C["card2"], anchor="w").pack(fill="x", pady=(0, 10))
        box = tk.Frame(page, bg=C["card2"]); box.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(box); scroll.pack(side="right", fill="y")
        self.document_editor = tk.Text(box, wrap="char", font=(FONT, 10), background=C["card"], foreground=C["txt"],
                                      insertbackground=C["txt"], padx=12, pady=10, undo=True, highlightthickness=0, yscrollcommand=scroll.set)
        self.document_editor.pack(fill="both", expand=True); scroll.configure(command=self.document_editor.yview)
        self.document_state = {}
        tk.Label(page, text="未选择文字时预览全文，最多 2500 字；这里只转发确认的文字，不开放资料目录或发送原文件。",
                 font=(FONT, 9), fg=C["muted"], bg=C["card2"], anchor="w", justify="left", wraplength=780).pack(fill="x", pady=(10, 0))
        close = self.button(footer, "data_done", "关闭", lambda: self.close_dialog(dialog))
        close.primary = False; close.ghost = True; close.configure(background=C["bg"], width=72, height=30)
        close.pack(side="left"); close.draw()
        send = self.button(footer, "private_send", "预览并发送", self.preview_local_text)
        send.tone = "soft"; send.configure(background=C["bg"], width=104, height=30)
        send.pack(side="right", padx=(8, 0)); send.draw()
        self.button(footer, "private_save", "保存本地修改", self.save_local_document).pack(side="right")

    def runtime_info(self):
        from bootstrap import is_admin
        dialog,body,footer=self.dialog_shell("程序与诊断",
            "诊断只导出版本、依赖版本、开关和统计数量，不包含密钥、QQ 身份、路径、文件或日志正文。文件需你自行选择保存位置，不自动上传。",
            width=760,height=520)
        tk.Label(body,text="灵梭 "+__version__+" · 修订 "+REVISION,font=(FONT,10,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(fill="x")
        tk.Label(body,text="程序数据目录：%s\n当前权限：%s"%(self.app.directory,"管理员" if is_admin() else "普通用户"),
                 font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",wraplength=620).pack(fill="x",pady=(8,0))
        def save():
            path=filedialog.asksaveasfilename(parent=self.active_dialog,title="保存脱敏诊断（不覆盖已有文件）",initialfile="灵梭_诊断.json",defaultextension=".json",filetypes=[("诊断JSON","*.json")])
            if not path:return
            from .diagnostics import export
            self.submit(lambda:export(self.app,path),lambda _:self.message.set("脱敏诊断已保存到你选择的位置，未上传。"),resource="diagnostics")
        close=self.button(footer,"runtime_close","关闭",lambda:self.close_dialog(dialog))
        close.primary=False;close.ghost=True;close.configure(background=C["bg"],width=72,height=30)
        close.pack(side="left");close.draw()
        self.button(footer,"export_diagnostics","导出脱敏诊断",save).pack(side="right")

    def add_private_root(self):
        folder = filedialog.askdirectory(parent=self.active_dialog, title="选择仅由本机软件访问的资料文件夹")
        if not folder: return
        if not self.confirm("仅本机软件访问", "软件可读取和编辑该目录的文本资料，QQ 文件接口会排除此范围，即使它位于共享目录内。\n\n" + folder + "\n\n确认授权？"): return
        roots = self.app.config.get()["local_data"]["roots"]
        if folder not in roots: roots.append(folder)
        def done(_):
            if self.active_dialog.winfo_exists(): self.private_scope.set("仅软件访问：" + "；".join(roots))
            self.message.set("本地资料目录已授权，QQ 文件访问仍被禁止。")
        self.submit(lambda: self.app.save_section("local_data", {"roots":roots}), done)

    def open_local_document(self):
        name = filedialog.askopenfilename(parent=self.active_dialog, title="打开已授权资料目录中的 UTF-8 文本")
        if not name: return
        editor, state, path_var = self.document_editor, self.document_state, self.document_path
        if state and editor.get("1.0", "end-1c") != state["text"]:
            if not self.confirm("有未保存修改", "打开其他文件会丢弃当前尚未保存的修改，确认继续？"): return
        def loaded(value):
            if not editor.winfo_exists(): return
            state.clear(); state.update(value)
            editor.delete("1.0", "end"); editor.insert("1.0", value["text"]); editor.edit_reset(); path_var.set(value["path"])
            self.mark_dialog_saved(editor.winfo_toplevel())
        self.submit(lambda: self.app.documents.read(name), loaded)

    def save_local_document(self):
        if not self.document_state: raise ValueError("先打开已授权资料目录内的文本文件")
        editor, state = self.document_editor, self.document_state
        expected, text = dict(state), editor.get("1.0", "end-1c")
        def done(value):
            state.clear(); state.update(value); self.message.set("本地文件已保存，QQ 未读取或接收该文件。")
        self.submit(lambda: self.app.save_document(expected, text), done)

    def preview_local_text(self):
        if not self.document_state: raise ValueError("请先打开一份本地资料")
        try: text = self.document_editor.get("sel.first", "sel.last")
        except tk.TclError: text = self.document_editor.get("1.0", "end-1c")
        if not text.strip() or len(text) > 2500: raise ValueError("请选择 1–2500 字内容，软件不会自动截断全文")
        users = self.app.config.get()["qq"]["allowed_users"]
        if not users: raise ValueError("请先在“设置 → QQ 接入”开始绑定本人")
        preview = tk.Toplevel(self.active_dialog); preview.title("核对资料文字与 QQ 接收人")
        preview.geometry("720x580"); preview.configure(background=C["bg"]); preview.transient(self.active_dialog)
        body = ttk.Frame(preview, padding=18); body.pack(fill="both", expand=True)
        self.label(body, "下方是将发送的完整文字。仅此文字会进入通知队列，资料路径和原文件不发送。")
        user = tk.StringVar(value=users[0]); ttk.Combobox(body, textvariable=user, values=users, state="readonly").pack(fill="x", pady=8)
        box = ttk.Frame(body); box.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(box); scroll.pack(side="right", fill="y")
        field = tk.Text(box, wrap="char", background=C["card"], foreground=C["txt"], font=(FONT, 10), yscrollcommand=scroll.set)
        field.pack(fill="both", expand=True); scroll.configure(command=field.yview)
        field.insert("end", text); field.configure(state="disabled")
        def send():
            recipient = user.get()
            def done(_):
                if preview.winfo_exists(): preview.destroy()
                self.message.set("已确认文字并入队；平台接受及手机收到仍需另行核对。")
            self.submit(lambda: self.app.queue_local_text(recipient, text), done)
        row = self.row(body)
        self.button(row, "private_confirm_send", "确认发送这些文字", send).pack(side="right")
        self.button(row, "private_cancel_send", "取消", preview.destroy).pack(side="right", padx=8)

    def appearance_dialog(self):
        _, page = self.dialog("外观皮肤", width=760, height=600)
        self.label(page, "沿用原工程六种配色。选择后保存，退出灵梭并重新启动生效。")
        grid = ttk.Frame(page); grid.pack(fill="both", expand=True)
        for col in range(2): grid.columnconfigure(col, weight=1, uniform="theme")
        for i, (key, theme) in enumerate(SPEC["themes"].items()):
            summary = tk.StringVar(value=("当前使用" if key == self.theme else "可选皮肤") + "\n保存后重启生效")
            card = ActionCard(grid, theme["name"], summary, lambda k=key: self.save_theme(k), action="使用", height=125, swatch=theme["tokens"]["accent"])
            card.grid(row=i // 2, column=i % 2, sticky="ew", padx=6, pady=6)
            self.buttons["theme_" + key] = card.edit
        ttk.Label(page, textvariable=self.message, wraplength=690).pack(anchor="w", pady=8)

    def save_theme(self, name):
        if name not in SPEC["themes"]: raise ValueError("未知皮肤")
        path = self.app.directory / "appearance.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"theme":name}), encoding="utf-8"); temporary.replace(path)
        self.message.set("已保存“%s”，请退出灵梭后重新启动生效。" % SPEC["themes"][name]["name"])

    def web_search_dialog(self):
        _, page = self.dialog("搜索公开网页", height=460)
        self.label(page, "输入关键词后在系统浏览器中查看搜索结果。这里只打开搜索页面，不自动抓取、总结网页，也不发送本地资料。")
        self.entry(page, "web_query", "搜索关键词")
        self.vars["web_engine"] = tk.StringVar(value="Bing")
        ttk.Combobox(page, textvariable=self.vars["web_engine"], values=["Bing", "百度"], state="readonly").pack(fill="x", pady=8)
        self.button(page, "web_search", "打开搜索结果", self.open_web_search).pack(anchor="w", pady=12)

    def open_web_search(self):
        from urllib.parse import urlencode
        query = self.vars["web_query"].get().strip()
        if not query or len(query) > 300: raise ValueError("请输入 1–300 字的搜索词")
        url = ("https://www.baidu.com/s?" + urlencode({"wd":query})) if self.vars["web_engine"].get() == "百度" else ("https://www.bing.com/search?" + urlencode({"q":query}))
        self.open_url(url); self.message.set("已请求打开系统浏览器；软件尚未读取或总结搜索结果。")

    def manual_add(self):
        name = self.vars["manual_process"].get().strip()
        if not name.lower().endswith(".exe") or any(c in name for c in "/\\:\r\n"):
            raise ValueError("填写程序文件名，例如 notepad.exe，不是完整路径。")
        self.chosen.add(name); self.show("monitor"); self.refresh()
        self.message.set("已加入选择，请点击“保存选择并监控”。")

    def save_advanced(self):
        try: values = {k: int(self.vars[k].get()) for k in ("interval", "max_mb", "max_depth", "cooldown", "attempts")}
        except ValueError: raise ValueError("高级参数须为整数。") from None
        def save():
            with self.app.settings_lock:
                cfg = self.app.config.get()
                cfg["monitor"]["interval"] = values["interval"]
                cfg["files"].update(max_mb=values["max_mb"], max_depth=values["max_depth"])
                cfg["notifications"].update(cooldown=values["cooldown"], max_attempts=values["attempts"])
                self.app.save_config(cfg)
        self.submit(save)

    def refresh(self,snapshot=None):
        # Periodic refresh receives an immutable snapshot produced off the Tk thread.
        # An explicit refresh is retained for initial rendering and isolated diagnostics.
        snapshot=self.load_snapshot() if snapshot is None else snapshot
        self.last_snapshot=snapshot
        status,cfg=snapshot["status"],snapshot["config"]
        tasks,history=snapshot["tasks"],snapshot["history"]
        current=self.current_page()
        if current=="tasks":self.refresh_records(snapshot.get("task_page",{"rows":tasks,"total":len(tasks)}))
        if not self.qq_dirty():self.reset_qq_form()
        self.badges[0].set("QQ 已连接" if status["qq"]["connected"] else "QQ 未连接","ok" if status["qq"]["connected"] else "warn")
        self.badges[1].set("监控 %s"%len(cfg["monitor"]["process_names"]))
        self.badges[2].set("接收人 %s"%len(status["users"]));self.badges[2].pack_forget()
        self.badges[3].set("版本 "+__version__)
        self.side_status.set(__version__+" · "+REVISION+"\n源码开发版")
        self.banner.set(status["monitor_error"] or ("正在观察 %s 个程序"%len(cfg["monitor"]["process_names"]) if status["local_running"] and cfg["monitor"]["enabled"] else "本地监控已暂停" if not cfg["monitor"]["enabled"] else "本地核心尚未启动"))
        self.refresh_experience(status,cfg,tasks,history)
        if current in {"monitor","files","manage","tasks"} or (getattr(self,"active_dialog",None) and self.active_dialog.winfo_exists()):self.refresh_services(status,cfg,tasks)
        sampled=time.strftime("%H:%M:%S",time.localtime(status["last_sample"])) if status["last_sample"] else "尚未采集"
        self.monitor_note.set(status["monitor_error"] or "最近采集：%s"%sampled)
        dirty={n.casefold() for n in self.chosen}!={n.casefold() for n in cfg["monitor"]["process_names"]} or self.vars["monitor_enabled"].get()!=cfg["monitor"]["enabled"]
        self.monitor_saved_note.set("已保存 %s 种程序 · 当前选择尚未保存"%len(cfg["monitor"]["process_names"]) if dirty else "已保存 %s 种程序"%len(cfg["monitor"]["process_names"]))
        self.qq_status.set(("沙箱环境 · " if cfg["qq"]["is_sandbox"] else "正式环境 · ")+status["qq"]["message"])
        for entry in self.qq_secret_inputs:entry.set_saved(bool(cfg["qq"]["secret"]))
        self.qq_secret_note.set("机器人应用凭据，留空保留已保存密钥。" if cfg["qq"]["secret"] else "填写自己的机器人凭据，不是个人QQ密码。")
        users=[u["user"] for u in status["users"]]
        file_combo=getattr(self,"file_user_combo",None)
        if "file_user" in self.vars and self.vars["file_user"].get() not in users:
            self.vars["file_user"].set(users[0] if users else "")
        if file_combo is not None and file_combo.winfo_exists():
            file_combo.configure(values=users)
        for combo,key in ((self.notify_user_combo,"notify_user"),):
            if combo is None or not combo.winfo_exists():continue
            combo.configure(values=users)
            if self.vars[key].get() not in users:self.vars[key].set(users[0] if users else "")
        self.users_note.set("；".join(u["user"]+("：已订阅" if u["subscribed"] else "：已退订") for u in status["users"]) or "尚未绑定本人，请到QQ接入开始配对。")
        if self.test_tree is not None and self.test_tree.winfo_exists():
            self.fill(self.test_tree,[(r["id"],(r["id"],r["user"],"你已确认收到" if r["confirmed"] else "平台已接受，实收待确认" if r["accepted"] else {"pending":"等待提交","failed":"未完成","uncertain":"结果待核对","timed_out":"已过期","cancelled":"已取消"}.get(r["state"],r["state"]),r["error"] or "")) for r in status["tests"]])
        if self.history_tree is not None and self.history_tree.winfo_exists():
            self.fill(self.history_tree,[(r["id"],(time.strftime("%m-%d %H:%M:%S",time.localtime(r["ts"])),r["kind"],r["text"])) for r in history])
        saved_roots=cfg["files"]["roots"]
        self.refresh_file_page(status,cfg,tasks)
        if hasattr(self,"automation_status"):
            if self.click_hotkey and self.click_hotkey.error:
                error=self.click_hotkey.error;self.disarm_automation();self.automation_status.set(error)
            task=self.app.automation.status()
            if task["running"] or (self.armed_plan is not None and task["message"]!="尚未启动点击任务"):self.automation_status.set(task["message"])
            toggle=self.buttons.get("automation_toggle")
            if toggle and toggle.winfo_exists():
                text="停止" if task["running"] else "开始";primary=task["running"] or self.armed_plan is not None
                if toggle.text!=text or toggle.primary!=primary:toggle.text=text;toggle.primary=primary;toggle.draw()

    def _start_tray(self):
        def run():
            try:
                import pystray
                from .app import tray_image
                self.icon = pystray.Icon("LingSuoBasic", tray_image(), "灵梭基础版", self.tray_menu(pystray))
                if self.closing: return
                def ready(icon):
                    if self.closing:
                        icon.stop(); return
                    icon.visible = True
                    self.events.put(("tray-ready", None))
                self.icon.run(setup=ready)
            except Exception:
                self.events.put(("tray-error", None))
        threading.Thread(target=run, daemon=True, name="desktop-tray").start()

    def tray_menu(self,pystray):
        return pystray.Menu(
            pystray.MenuItem("打开主窗口",lambda *_:self.events.put(("restore",None)),default=True),
            pystray.MenuItem("以管理员身份重启",lambda *_:self.events.put(("restart-admin",None))),
            pystray.MenuItem("退出灵梭",lambda *_:self.events.put(("exit",None))))

    def restart_as_admin(self):
        if self.closing:return
        self.restore()
        if not self.allow_exit():return
        from .restart import request_admin_restart
        try:request_admin_restart(self.app.directory)
        except (OSError,ValueError):
            self.show_error("未获得管理员授权或重启请求失败，当前灵梭继续运行。请重试并在 Windows 授权窗口中确认。")
            return
        self.begin_close()
        self.message.set("管理员重启请求已提交，正在正常退出当前灵梭；新实例将等待退出完成。")

    def hide(self):
        if self.closing: return
        self.disarm_automation()
        if self.tray_ready:
            self.root.withdraw()
        elif self.confirm("托盘尚未可用", "托盘尚未就绪或不可用。要退出灵梭并停止本地监控吗？"):
            self.close()

    def restore(self):
        if not self.closing:
            self.root.deiconify(); self.root.lift()

    def allow_exit(self):
        if self.closing:return False
        dialog=getattr(self,"active_dialog",None)
        if dialog is not None and dialog.winfo_exists() and not self.allow_close_dialog(dialog):return False
        if self.qq_dirty():
            choice=self.draft_choice("QQ接入")
            if choice=="continue":return False
            if choice=="save":self.save_qq(reconnect=False);return False
        return True

    def close(self):
        if self.allow_exit():self.begin_close()

    def begin_close(self):
        self.disarm_automation()
        self.closing = True
        self.message.set("正在等待当前操作结束并退出灵梭…")


def run_desktop(runtime):
    root = tk.Tk()
    DesktopApp(root, runtime)
    root.mainloop()
