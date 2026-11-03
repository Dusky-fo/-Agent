"""Native process selection and grouped display; monitoring remains in the core."""
import time
import tkinter as tk
from tkinter import ttk
from .desktop_skin import C,FONT,RoundButton,CheckToggle,RoundedPanel,Segmented,MenuButton

class MonitorScreens:
    def _monitor(self):
        page=self.pages["monitor"]
        self.vars["monitor_enabled"]=tk.BooleanVar(value=self.app.config.get()["monitor"]["enabled"])

        find=RoundedPanel(page,pad=18,height=152);find.pack(fill="x",pady=(0,12))
        head=tk.Frame(find.body,bg=C["card2"]);head.pack(fill="x")
        tk.Label(head,text="查找程序",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        self.process_count=tk.StringVar(value="尚未读取进程 · 已选 %s 种程序"%len(self.chosen))
        tk.Label(head,textvariable=self.process_count,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="e").pack(side="right")

        tools=tk.Frame(find.body,bg=C["card2"]);tools.pack(fill="x",pady=(8,0))
        tools.columnconfigure(3,weight=1)
        tk.Label(tools,text="搜索",font=(FONT,9),fg=C["sub"],bg=C["card2"]).grid(row=0,column=0,sticky="w",padx=(0,8))
        self.vars["process_query"]=tk.StringVar()
        ttk.Entry(tools,textvariable=self.vars["process_query"],width=24).grid(row=0,column=1,sticky="w",padx=(0,18))
        self.vars["process_query"].trace_add("write",lambda *_:self.filter_processes())
        self.process_filter="apps"
        self.filter_seg=Segmented(tools,[("apps","应用"),("background","后台"),("all","全部")],self.set_process_filter)
        self.filter_seg.grid(row=0,column=2,sticky="w")
        self.process_view=tk.StringVar(value="逐个实例")
        self.expanded_groups=set()
        view=MenuButton(tools,self.process_view,("逐个实例","按程序聚合"),command=lambda _v:self.filter_processes(),width=104)
        view.grid(row=0,column=4,sticky="e");self.view_menu=view

        stats=tk.Frame(find.body,bg=C["card2"]);stats.pack(fill="x",pady=(6,0))
        self.chosen_label=tk.StringVar(value="尚未选择程序")
        tk.Label(stats,textvariable=self.chosen_label,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w").pack(side="left")

        board=RoundedPanel(page,pad=18,height=352);board.pack(fill="both",expand=True)
        bhead=tk.Frame(board.body,bg=C["card2"]);bhead.pack(fill="x")
        tk.Label(bhead,text="程序列表",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        refresh=self.button(bhead,"read_processes","刷新进程",self.read_processes)
        refresh.primary=False;refresh.tone="soft";refresh.configure(width=100,height=28,background=C["card2"])
        refresh.pack(side="right");refresh.draw()
        foot=tk.Frame(board.body,bg=C["card2"]);foot.pack(side="bottom",fill="x",pady=(6,0))
        for key,label,fn in (("setting_anomalies","异常规则 ›",self.anomaly_dialog),("setting_watch","文件与日志 ›",self.watcher_dialog)):
            link=self.button(foot,key,label,fn)
            link.primary=False;link.ghost=True;link.configure(background=C["card2"],width=96,height=24)
            link.pack(side="right",padx=(6,0));link.draw()
        self.monitor_note=tk.StringVar()
        tk.Label(foot,textvariable=self.monitor_note,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w",justify="left").pack(side="left")
        self.process_tree=self.table(board.body,[("selected","监控"),("name","进程名称"),("pid","PID"),("memory","内存占用"),("kind","分类")])
        style=ttk.Style(self.root)
        style.configure("Monitor.Treeview",rowheight=30,font=(FONT,10),borderwidth=0,relief="flat",background=C["card2"],fieldbackground=C["card2"])
        style.map("Monitor.Treeview",background=[("selected",C["selected"] if self.theme=="light" else "#263e68")],foreground=[("selected",C["txt"])])
        style.configure("Monitor.Treeview.Heading",font=(FONT,9),padding=(6,6),background=C["card2"],foreground=C["sub"],relief="flat",borderwidth=0)
        style.map("Monitor.Treeview.Heading",background=[("active",C["card2"])])
        self.process_tree.configure(style="Monitor.Treeview")
        from PIL import Image,ImageDraw,ImageTk
        self.process_checks={}
        for checked in (False,True):
            bitmap=Image.new("RGBA",(18,18));draw=ImageDraw.Draw(bitmap)
            draw.rounded_rectangle((1,1,16,16),radius=4,fill=C["accent"] if checked else C["card2"],outline=C["accent"] if checked else C["sub"])
            if checked:draw.line(((4,9),(7,12),(14,5)),fill="white",width=2)
            self.process_checks[checked]=ImageTk.PhotoImage(bitmap,master=self.root)
        self.process_tree.configure(show="tree headings",displaycolumns=("name","pid","memory","kind"))
        self.process_tree.heading("#0",text="监控");self.process_tree.column("#0",width=54,minwidth=54,stretch=False)
        for key,width in (("selected",54),("name",240),("pid",90),("memory",140),("kind",90)):
            self.process_tree.column(key,width=width,minwidth=width if key=="selected" else 70,stretch=key=="name",
                                     anchor="e" if key in {"pid","memory"} else "w")
        self.process_tree.bind("<ButtonRelease-1>",self.process_checkbox)
        def keyboard_toggle(_):
            if self.process_tree.selection():self.toggle_process()
            return "break"
        self.process_tree.bind("<space>",keyboard_toggle)
        self.process_tree.bind("<<TreeviewOpen>>",self.remember_group_open)
        self.process_tree.bind("<<TreeviewClose>>",self.remember_group_close)
        self.process_tree.empty_note.configure(text="还没有读取进程。点右上角“刷新进程”，再勾选要监控的程序。")

        footer=ttk.Frame(page);footer.pack(fill="x",pady=(12,0))
        self.monitor_check=CheckToggle(footer,"启用监控",self.vars["monitor_enabled"])
        self.monitor_check.pack(side="left")
        self.monitor_saved_note=tk.StringVar()
        tk.Label(footer,textvariable=self.monitor_saved_note,font=(FONT,9),fg=C["sub"],bg=C["bg"],anchor="w").pack(side="left",padx=(14,0))
        self.button(footer,"save_monitor","保存选择并监控",self.save_monitor).pack(side="right")
        cancel=self.button(footer,"cancel_monitor","取消本次更改",self.cancel_monitor_selection)
        cancel.primary=False;cancel.ghost=True;cancel.configure(width=112,height=30)
        cancel.pack(side="right",padx=(0,8));cancel.draw()

    def read_processes(self):
        from .processes import list_processes, process_catalog
        provider = process_catalog if self.app.monitor.collector is list_processes else self.app.monitor.collector
        self.submit(provider, self.processes_loaded)

    def processes_loaded(self, rows):
        self.process_rows = rows; self.process_read_time = time.strftime("%H:%M:%S"); self.filter_processes()
        self.message.set("已读取进程。勾选程序后保存，同名实例统一监控。")

    def set_process_filter(self, key):
        self.process_filter = key; self.filter_processes()

    def filter_processes(self):
        query = self.vars["process_query"].get().strip().casefold()
        rows = [r for r in self.process_rows if r["name"].lower().endswith(".exe")]
        ready = bool(self.process_rows)
        counts = {"apps":sum(r.get("has_window") is True for r in rows), "background":sum(r.get("has_window") is False for r in rows), "all":len(rows)}
        self.filter_seg.labels({"apps":"应用 %s"%(counts["apps"] if ready else "—"),
                                "background":"后台 %s"%(counts["background"] if ready else "—"),
                                "all":"全部 %s"%(counts["all"] if ready else "—")})
        self.filter_seg.select(self.process_filter)
        filtered = [r for r in rows if (self.process_filter == "all" or r.get("has_window") is None or r.get("has_window") == (self.process_filter == "apps"))
                    and (query in r["name"].casefold() or query in str(r["pid"]))]
        chosen = {n.casefold() for n in self.chosen}
        values=[(str(r["pid"]), ("☑" if r["name"].casefold() in chosen else "☐", r["name"], r["pid"],
                 self.format_memory(r.get("memory_bytes")) if r.get("memory_bytes") is not None else r.get("memory", "未知"),
                 "应用" if r.get("has_window") is True else "后台" if r.get("has_window") is False else "未知")) for r in filtered]
        grouped = self.process_view.get() == "按程序聚合"
        heading = "实例" if grouped else "PID"
        if self.process_tree.heading("pid")["text"] != heading:
            self.process_tree.heading("pid", text=heading)
        signature = (grouped, tuple(values))
        if grouped:
            if getattr(self, "process_group_signature", None) != signature:
                position = self.process_tree.yview()[0]; selected = self.process_tree.selection()
                self.process_tree.delete(*self.process_tree.get_children()); self.last_tables.pop(self.process_tree, None)
                groups = {}
                by_pid = dict(values)
                for row in filtered: groups.setdefault(row["name"].casefold(), []).append(row)
                for name, instances in groups.items():
                    ident = "program:" + name
                    total = sum(instance.get("memory_bytes") or 0 for instance in instances)
                    kinds = {"应用" if row.get("has_window") is True else "后台" if row.get("has_window") is False else "未知" for row in instances}
                    kind = kinds.pop() if len(kinds) == 1 else "混合"
                    memory = self.format_memory(total)
                    self.process_tree.insert("", "end", iid=ident,
                                             values=("☑" if name in chosen else "☐", instances[0]["name"], "%s 个实例" % len(instances), memory, kind),
                                             image=self.process_checks[name in chosen], open=name in self.expanded_groups)
                    for instance in instances:
                        key = str(instance["pid"]); self.process_tree.insert(ident, "end", iid=key, values=by_pid[key])
                self.process_tree.selection_set([key for key in selected if self.process_tree.exists(key)])
                self.process_tree.yview_moveto(position)
                if filtered: self.process_tree.empty_note.place_forget()
                else: self.process_tree.empty_note.place(relx=.5, rely=.53, anchor="center")
        else:
            if getattr(self, "process_group_signature", (False,))[0]: self.last_tables.pop(self.process_tree, None)
            self.fill(self.process_tree, values)
        self.process_group_signature = signature
        for r in filtered: self.process_tree.item(str(r["pid"]), image=self.process_checks[r["name"].casefold() in chosen])
        self.process_count.set("显示 %s / %s · 已选 %s 种" % (len(filtered), len(rows), len(self.chosen)) if ready
                               else "尚未读取进程 · 已选 %s 种" % len(self.chosen))
        self.chosen_label.set("已选：" + ("、".join(sorted(self.chosen))[:60] or "尚未选择程序"))

    def remember_group_open(self, event=None):
        ident = self.process_tree.focus()
        if ident.startswith("program:"): self.expanded_groups.add(ident[8:])

    def remember_group_close(self, event=None):
        ident = self.process_tree.focus()
        if ident.startswith("program:"): self.expanded_groups.discard(ident[8:])

    @staticmethod
    def format_memory(value):
        if not value:
            return "未知"
        if value >= 1024 ** 3:
            return "%.2f GB" % (value / 1024 ** 3)
        if value >= 1024 ** 2:
            return "%.1f MB" % (value / 1024 ** 2)
        return "%.0f KB" % (value / 1024)

    def process_checkbox(self, event):
        if "indicator" in self.process_tree.identify_element(event.x, event.y): return
        row = self.process_tree.identify_row(event.y)
        if row: self.toggle_process(row)

    def toggle_process(self, row=None):
        ident = row or self.selected(self.process_tree)
        name = self.process_tree.item(ident, "values")[1]
        matches = {n for n in self.chosen if n.casefold() == name.casefold()}
        if matches: self.chosen.difference_update(matches)
        else: self.chosen.add(name)
        self.filter_processes()

    def cancel_monitor_selection(self):
        cfg = self.app.config.get()["monitor"]
        self.chosen = set(cfg["process_names"]); self.vars["monitor_enabled"].set(cfg["enabled"]); self.filter_processes()

    def save_monitor(self):
        values = {"enabled": self.vars["monitor_enabled"].get(), "process_names": sorted(self.chosen)}
        self.submit(lambda: self.app.save_section("monitor", values))
