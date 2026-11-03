"""Native opt-in service screens, using the existing desktop widgets and executor."""
import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog
from .desktop_skin import C,FONT,CheckToggle,RoundedPanel
from .tools import Caller
from .tasks import STATE_NAMES
from .desktop_ux import TASK_NAMES,task_sentence


class ServiceScreens:
    def local_caller(self,*permissions):return Caller("","local",frozenset(permissions))

    def scope_picker(self,parent,title,values,files=False):
        self.label(parent,title)
        items=list(values);selected=tk.StringVar(value=items[0] if items else "")
        combo=ttk.Combobox(parent,textvariable=selected,values=items,state="readonly");combo.pack(fill="x",pady=5)
        def add():
            value=filedialog.askopenfilename(parent=parent,title=title) if files else filedialog.askdirectory(parent=parent,title=title)
            if value and value not in items:items.append(value);combo.configure(values=items);selected.set(value)
        def remove():
            if selected.get() in items:items.remove(selected.get());combo.configure(values=items);selected.set(items[0] if items else "")
        row=self.row(parent)
        self.button(row,title+"_add","选择文件" if files else "选择目录",add).pack(side="left")
        self.button(row,title+"_remove","移除选中范围",remove).pack(side="left",padx=8)
        return items

    def watcher_dialog(self):
        dialog,body=self.dialog("文件监控与日志分析",width=800,height=730)
        footer=ttk.Frame(body);footer.pack(side="bottom",fill="x",pady=8)
        page=self.scroll_area(body);cfg=self.app.config.get()["watch"]
        self.label(page,"只观察这里明确保存的目录和日志；不会因此开放 QQ 文件共享。首次启用从当前状态建立基线，日志从末尾增量读取，暂停期间不补采。")
        enabled=tk.BooleanVar(value=cfg["enabled"]);CheckToggle(page,"启用文件与日志监控",enabled).pack(anchor="w")
        self.label(page,"已授权观察 %s 个目录、%s 个指定日志。"%(len(cfg["roots"]),len(cfg["logs"])))
        self.button(page,"watch_permissions","管理文件夹与日志权限",self.permissions_dialog).pack(anchor="w",pady=5)
        for key,label in (("include","包含文件名（逗号分隔）"),("exclude","排除文件名（逗号分隔）"),("keywords","日志关键词（逗号分隔）")):
            self.entry(page,"watch_"+key,label,",".join(cfg[key]))
        self.entry(page,"watch_patterns","受限正则（分号分隔）",";".join(cfg["patterns"]))
        self.label(page,"包含/排除使用 *、? 文件名通配符；正则只允许固定文字、字符类、点、锚点和转义，不支持量词、分组或分支。日志正文不进入通知，只报告命中次数与文件名。")
        for key,label in (("interval","扫描间隔（秒）"),("merge_seconds","事件合并（秒）"),("max_files","最大观察文件数")):
            self.entry(page,"watch_"+key,label,str(cfg[key]))
        self.watch_status=tk.StringVar(value="")
        ttk.Label(footer,textvariable=self.watch_status,wraplength=740).pack(anchor="w")
        def save():
            values={"enabled":enabled.get()}
            for key in ("include","exclude","keywords","patterns"):
                values[key]=[v.strip() for v in self.vars["watch_"+key].get().split(";" if key=="patterns" else ",") if v.strip()]
            for key in ("interval","merge_seconds","max_files"):values[key]=int(self.vars["watch_"+key].get())
            self.submit(lambda:self.app.save_section("watch",values),lambda _:self.message.set("观察范围已保存，首次采集建立基线；未授权 QQ 读取这些目录。"))
        self.button(footer,"watch_save","保存观察范围与开关",save).pack(side="right",pady=8)
        self.button(footer,"watch_close","关闭",dialog.destroy).pack(side="left")

    def anomaly_dialog(self):
        dialog,page=self.dialog("进程异常规则",width=760,height=650)
        cfg=self.app.config.get()["anomalies"]
        self.label(page,"仅检查已保存的监控程序。CPU 按整机总能力计为 0–100%，内存为每个 PID 的工作集。无可见窗口时无响应状态未知；读取失败不会当作退出。不会重启目标程序。")
        enabled=tk.BooleanVar(value=cfg["enabled"]);CheckToggle(page,"启用进程异常检测",enabled).pack(anchor="w")
        for key,label in (("cpu_percent","CPU 阈值（%）"),("memory_mb","内存阈值（MB）"),("sustain_seconds","资源持续时间（秒）"),("absence_seconds","退出持续时间（秒）"),("hang_seconds","无响应持续时间（秒）")):
            self.entry(page,"anomaly_"+key,label,str(cfg[key]))
        self.label(page,"持续满足规则后只报警一次，确认正常后记录恢复；通知合并与冷却沿用设置中的通知策略。")
        def save():
            values={key:int(self.vars["anomaly_"+key].get()) for key in cfg if key!="enabled"};values["enabled"]=enabled.get()
            self.submit(lambda:self.app.save_section("anomalies",values),lambda _:self.message.set("异常规则已保存；请到进程监控选择程序。"))
        footer=tk.Frame(page);footer.pack(side="bottom",fill="x",pady=(12,0))
        self.button(footer,"anomaly_close","关闭",lambda:self.close_dialog(dialog)).pack(side="left")
        self.button(footer,"anomaly_save","保存规则",save).pack(side="right")

    def inbox_dialog(self):
        cfg=self.app.config.get()["inbound"]
        dialog,body,footer=self.dialog_shell("QQ 附件接收到本机",
            "默认关闭。仅已授权的 QQ 私聊用户可提交附件；收到事件只显示附件列表，不下载。回复“接收”或“接收 1”后才保存到此目录，不打开、不运行、不覆盖同名文件。接收目录不会自动加入 QQ 共享范围。",
            width=760,height=560)
        enabled=tk.BooleanVar(value=cfg["enabled"])
        CheckToggle(body,"启用授权对话的附件接收",enabled,background=C["card2"]).pack(anchor="w",pady=(0,12))
        for key,label,value in (("inbox_directory","本机接收目录",cfg["directory"]),("inbox_max_mb","单文件上限（MB）",str(cfg["max_mb"]))):
            line=tk.Frame(body,bg=C["card2"]);line.pack(fill="x",pady=6)
            tk.Label(line,text=label,font=(FONT,9),fg=C["sub"],bg=C["card2"],width=16,anchor="w").pack(side="left")
            self.vars[key]=tk.StringVar(value=value)
            ttk.Entry(line,textvariable=self.vars[key]).pack(side="left",fill="x",expand=True)
            if key=="inbox_directory":
                def choose():
                    picked=filedialog.askdirectory(parent=dialog,title="选择接收附件的现有目录")
                    if picked:self.vars["inbox_directory"].set(picked)
                pick=self.button(line,"inbox_choose","选择",choose)
                pick.tone="soft";pick.configure(background=C["card2"],width=56,height=28)
                pick.pack(side="left",padx=(8,0));pick.draw()
        note=tk.Label(body,text="官方消息附件字段支持此流程，但你的账号是否收到附件事件、下载地址是否可用仍需真实账号验收。",
                      font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",wraplength=620)
        note.pack(fill="x",pady=(12,0))
        def save():
            values={"enabled":enabled.get(),"directory":self.vars["inbox_directory"].get().strip(),"max_mb":int(self.vars["inbox_max_mb"].get())}
            if not self.confirm("附件落盘授权","允许已授权的 QQ 对话在主动回复“接收”后将附件保存到：\n"+values["directory"]+"\n\n不自动开放该目录给 QQ 搜索。确认保存？"):return
            self.submit(lambda:self.app.save_section("inbound",values))
        self.dialog_actions(footer,dialog,"inbox_save","保存接收设置",save)
        return dialog

    def _manager_page(self):
        page=self.scroll_area(self.pages["manage"])
        self.manager_scope=tk.StringVar()
        scope=RoundedPanel(page,pad=18,height=146);scope.pack(fill="x",pady=(4,12))
        sfoot=tk.Frame(scope.body,bg=C["card2"]);sfoot.pack(side="bottom",fill="x")
        self.button(sfoot,"manager_recovery","恢复记录",self.recovery_dialog).pack(side="left")
        self.buttons["manager_recovery"].ghost=True;self.buttons["manager_recovery"].configure(background=C["card2"],width=88,height=26);self.buttons["manager_recovery"].draw()
        sc=self.button(sfoot,"manager_scope","设置范围",self.manager_scope_dialog)
        sc.primary=True;sc.draw();sc.pack(side="right")
        head=tk.Frame(scope.body,bg=C["card2"]);head.pack(fill="x")
        tk.Label(head,text="本机管理范围",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        tk.Label(scope.body,text="只在这几个目录里搜索和整理，QQ 不能调用它们。",font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w").pack(fill="x",pady=(4,4))
        tk.Label(scope.body,textvariable=self.manager_scope,font=(FONT,9),fg=C["txt"],bg=C["card2"],anchor="w",justify="left",wraplength=860).pack(fill="x")

        box=RoundedPanel(page,pad=18,height=430);box.pack(fill="both",expand=True)
        foot=tk.Frame(box.body,bg=C["card2"]);foot.pack(side="bottom",fill="x",pady=(8,0))
        tk.Label(foot,text="每次最多 100 个文件、100 MB；同盘移动，同名不覆盖；都可先预览再执行",font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w").pack(side="left",fill="x",expand=True)
        for key,text,mode in (("convert","转换文档","convert"),("organize","按扩展名整理","organize"),("move","移动","move"),("rename","重命名","rename")):
            btn=self.button(foot,"manager_"+key,text,lambda m=mode:self.manager_action(m))
            btn.tone="soft";btn.configure(background=C["card2"]);btn.pack(side="right",padx=(6,0));btn.draw()
        bhead=tk.Frame(box.body,bg=C["card2"]);bhead.pack(fill="x")
        tk.Label(bhead,text="本机文件",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        self.button(bhead,"manager_edit","文本编辑",self.data_dialog).pack(side="right")
        self.buttons["manager_edit"].ghost=True;self.buttons["manager_edit"].configure(background=C["card2"],width=88,height=26);self.buttons["manager_edit"].draw()
        search=tk.Frame(box.body,bg=C["card2"]);search.pack(fill="x",pady=(8,4))
        self.vars["manager_query"]=tk.StringVar()
        ttk.Entry(search,textvariable=self.vars["manager_query"]).pack(side="left",fill="x",expand=True)
        def search_files():
            query=self.vars["manager_query"].get()
            def done(task):
                rows=json.loads(task["result"]).get("files",[]);self.manager_rows=rows
                self.fill(self.manager_tree,[(str(i),(r["name"],"%.1f KB"%(r["size"]/1024),r["path"])) for i,r in enumerate(rows)])
            self.submit(lambda:self.app.tools.execute("files.search",{"query":query},self.local_caller("files.read")),done)
        srch=self.button(search,"manager_search","搜索",search_files)
        srch.primary=True;srch.draw();srch.pack(side="left",padx=(8,0))
        self.manager_rows=[]
        self.manager_tree=self.table(box.body,[("name","文件"),("size","大小"),("path","完整路径")],height=8)
        self.manager_tree.empty_note.configure(text="先设置本机管理范围，再在左上输入部分文件名")


    def manager_scope_dialog(self):
        self.permissions_dialog()

    def manager_action(self,mode):
        selection=self.manager_tree.selection()
        if not selection:raise ValueError("先搜索并选中文件；可用 Ctrl 选择多个文件整理")
        paths=[self.manager_rows[int(i)]["path"] for i in selection]
        if mode!="organize" and len(paths)!=1:raise ValueError("此操作每次选择一个文件")
        dialog,page,footer=self.dialog_shell({"rename":"重命名预览","move":"移动文件预览","organize":"按扩展名整理预览","convert":"文档转换预览"}[mode],
            "选择目标后先生成预览，再确认执行。预览 5 分钟内有效；内容变化后必须重新预览。", width=840, height=660)
        tk.Label(page,text="源文件：\n"+"\n".join(paths[:5]),font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",
                 wraplength=760).pack(fill="x",pady=(0,12))
        line=tk.Frame(page,bg=C["card2"]);line.pack(fill="x",pady=(0,8))
        tk.Label(line,text="新名称" if mode=="rename" else "目标",font=(FONT,9),fg=C["sub"],bg=C["card2"],width=8,anchor="w").pack(side="left")
        self.vars["manager_target"]=tk.StringVar(value=Path(paths[0]).name if mode=="rename" else "")
        ttk.Entry(line,textvariable=self.vars["manager_target"]).pack(side="left",fill="x",expand=True)
        if mode!="rename":
            def choose():
                value=filedialog.asksaveasfilename(parent=dialog,title="输出新文件，支持 .docx / .txt / .html") if mode=="convert" else filedialog.askdirectory(parent=dialog,title="选择已授权目标目录")
                if value:self.vars["manager_target"].set(value)
            pick=self.button(line,"manager_choose_target","选择",choose)
            pick.tone="soft";pick.configure(background=C["card2"],width=56,height=28)
            pick.pack(side="left",padx=(8,0));pick.draw()
        tk.Label(page,text="TXT/MD→DOCX、DOCX→TXT、TXT/MD→HTML；只转换文字，不保留原排版或图片，不渲染 Markdown。输入≤5 MB，不支持 PDF、旧 DOC、OCR。" if mode=="convert" else "目标同名时拒绝执行，不会覆盖。执行会占用与本次文件总量相当的备份空间；中途停止可能部分完成，可在恢复记录里核对。",
                 font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",wraplength=760).pack(fill="x",pady=(0,10))
        preview=tk.Text(page,height=10,wrap="char",bg=C["card"],fg=C["txt"],font=(FONT,9),bd=0,highlightthickness=0,padx=10,pady=8)
        preview.pack(fill="both",expand=True)
        state={}
        def prepare():
            value=self.vars["manager_target"].get().strip()
            def work():
                if mode=="convert":return self.app.conversion.preview(paths[0],value)
                if mode=="organize":return self.app.file_manager.organize(paths,value)
                target=Path(paths[0]).with_name(value) if mode=="rename" else Path(value)/Path(paths[0]).name
                return self.app.file_manager.preview([(paths[0],target)])
            def done(result):
                state.clear();state.update(result)
                state["preview_expires"]=time.monotonic()+300
                if preview.winfo_exists():
                    preview.delete("1.0","end")
                    rows=result.get("rows",[result]);text="\n\n".join("原文件："+r["source"]+"\n目标："+r["target"]+"\n大小：%.1f KB"%(r["size"]/1024) for r in rows)
                    if "backup_bytes" in result:text+="\n\n恢复备份需要：%.1f MB；预览 5 分钟有效。"%(result["backup_bytes"]/1024**2)
                    else:text+="\n\n源文件保留，输出不会覆盖现有文件。"
                    preview.insert("end",text)
            self.submit(work,done)
        self.vars["manager_target"].trace_add("write",lambda *_:state.clear())
        def execute():
            if not state or state.get("preview_expires",0)<time.monotonic():raise ValueError("先生成有效预览；预览超过 5 分钟须重新生成")
            expected=dict(state);state.clear()
            if mode in {"move","organize"} and not self.confirm("执行文件变更","即将执行已预览的文件移动或整理。请核对完整目标；中途停止可能部分完成，可查看恢复记录。确认执行？"):return
            if mode=="convert":fn=lambda:self.app.tools.execute("documents.convert",{k:expected[k] for k in ("source","target","sha256")},self.local_caller("files.write"),180)
            else:fn=lambda:self.app.tools.execute("files.apply",{"plan":expected["id"]},self.local_caller("files.write"),300)
            self.submit(fn,lambda _:self.show("tasks"))
        close=self.button(footer,"manager_preview_close","关闭",lambda:self.close_dialog(dialog))
        close.primary=False;close.ghost=True;close.configure(background=C["bg"],width=72,height=30)
        close.pack(side="left");close.draw()
        self.button(footer,"manager_preview","生成预览",prepare).pack(side="left",padx=(8,0))
        self.button(footer,"manager_execute","确认执行",execute).pack(side="right")

    def recovery_dialog(self):
        dialog, page, footer = self.dialog_shell("文件变更恢复记录",
            "仅恢复由本机文件管理保留的备份。源位置已有不同文件、目标被修改或授权已撤销时拒绝自动恢复，不会覆盖后来改动。",
            width=880, height=620)
        usage=tk.StringVar(value="正在读取备份占用…")
        tk.Label(page, textvariable=usage, font=(FONT, 9), fg=C["muted"], bg=C["card2"], anchor="w", justify="left",
                 wraplength=800).pack(fill="x", pady=(0, 10))
        tree=self.table(page,[("time","时间"),("names","文件"),("state","状态"),("files","数量")])
        tree.column("time", width=110, stretch=False, anchor="e")
        tree.column("files", width=60, stretch=False, anchor="e")
        states={"preparing":"准备备份","executing":"执行中","completed":"已完成","partial":"部分完成 / 已停止","restored":"已恢复，可核对清理","cleaned":"冗余备份已清理","cleanup_pending":"清理未完成，原文件保留"}
        def load():return self.app.file_manager.history(),self.app.file_manager.backup_usage()
        def loaded(value):
            if not tree.winfo_exists():return
            items,size=value;usage.set("恢复备份 %.1f / 512 MB；不会自动清理。位置：%s"%(size/1024**2,self.app.file_manager.journals))
            self.fill(tree,[(r["id"],(time.strftime("%m-%d %H:%M",time.localtime(r["created"])),r["names"],states.get(r["state"],r["state"]),r["files"])) for r in items])
        self.submit(load,loaded,resource="recovery-read")
        def details():
            ident=self.selected(tree)
            def show(item):
                _,body=self.dialog("文件变更详情",width=900,height=590)
                self.label(body,"恢复编号："+ident+"。恢复会重新检查当前授权与文件变化；有冲突不会覆盖。")
                table=self.table(body,[("source","原位置"),("target","目标位置"),("state","完成情况")])
                labels={"backed_up":"已备份，尚未移动","moving":"需核对是否移动","moved":"已移动","copied":"已复制，待核对","restored":"已恢复"}
                self.fill(table,[(str(i),(r["source"],r["target"],labels.get(r["phase"],r["phase"]))) for i,r in enumerate(item["rows"])])
            self.submit(lambda:self.app.file_manager.details(ident),show,resource="recovery-read")
        def restore():
            ident=self.selected(tree)
            if self.confirm("恢复文件变更","确认核对备份并恢复操作 "+ident+"？目标改动或同名冲突会停止恢复。"):
                self.submit(lambda:self.app.tools.execute("files.recover",{"ident":ident},self.local_caller("files.write"),300),lambda _:self.show("tasks"))
        close = self.button(footer, "recovery_close", "关闭", lambda: self.close_dialog(dialog))
        close.primary = False; close.ghost = True; close.configure(background=C["bg"], width=72, height=30)
        close.pack(side="left"); close.draw()
        def cleanup():
            ident=self.selected(tree)
            def ready(preview):
                text="仅删除本记录中已恢复且原文件仍一致的冗余备份，不删除原文件。\n\n可释放 %.2f MB\n原位置：\n%s\n\n确认清理？"%(preview["bytes"]/1024**2,"\n".join(preview["originals"][:5]))
                if self.confirm("清理已恢复备份",text):
                    self.submit(lambda:self.app.tasks.run("files.backup.cleanup","",lambda context:self.app.file_manager.cleanup(preview,context),300),lambda _:self.message.set("选定记录的冗余备份已清理，原文件保留；重新打开可查看占用。"),action="manager_cleanup",resource="file-write")
            self.submit(lambda:self.app.file_manager.cleanup_preview(ident),ready,resource="recovery-read")
        detail = self.button(footer, "manager_recovery_details", "详情", details)
        detail.tone = "soft"; detail.configure(background=C["bg"], width=72, height=30)
        detail.pack(side="right", padx=(8, 0)); detail.draw()
        clean = self.button(footer, "manager_cleanup", "清理冗余备份", cleanup)
        clean.tone = "soft"; clean.configure(background=C["bg"], width=118, height=30)
        clean.pack(side="right", padx=(8, 0)); clean.draw()
        self.button(footer, "manager_restore", "恢复选中记录", restore).pack(side="right")

    def web_task_dialog(self):
        dialog,page,footer=self.dialog_shell("授权网页读取",
            "只读取你逐次确认的公开 HTTPS 网址，不登录、不携带浏览器 Cookie、不执行页面脚本或网页指令，不提交表单。禁止内网地址和重定向。",
            width=840, height=660)
        line=tk.Frame(page,bg=C["card2"]);line.pack(fill="x",pady=(0,10))
        tk.Label(line,text="网址",font=(FONT,9),fg=C["sub"],bg=C["card2"],width=6,anchor="w").pack(side="left")
        self.vars["web_task_url"]=tk.StringVar()
        ttk.Entry(line,textvariable=self.vars["web_task_url"]).pack(side="left",fill="x",expand=True)
        output=tk.Text(page,wrap="char",height=15,bg=C["card"],fg=C["txt"],font=(FONT,10),bd=0,highlightthickness=0,padx=10,pady=8)
        output.pack(fill="both",expand=True)
        def read():
            url=self.vars["web_task_url"].get().strip()
            if not self.confirm("网页读取授权","向这个完整网址发起一次无登录的读取请求（不提交本地文件）：\n"+url):return
            consent=self.app.web.grant(url)
            def done(task):
                if output.winfo_exists():output.delete("1.0","end");output.insert("end",json.loads(task["result"]).get("text", "内容过大，请查看任务摘要"))
            self.submit(lambda:self.app.tools.execute("web.read",{"url":url,"consent":consent},self.local_caller("web.read"),60),done)
        close=self.button(footer,"web_task_close","关闭",lambda:self.close_dialog(dialog))
        close.primary=False;close.ghost=True;close.configure(background=C["bg"],width=72,height=30)
        close.pack(side="left");close.draw()
        self.button(footer,"web_task_read","授权并读取",read).pack(side="right")

    def refresh_services(self,status,cfg,tasks=None):
        if hasattr(self,"manager_scope"):
            self.manager_scope.set("本机管理范围："+("；".join(cfg["file_manager"]["roots"]) or "尚未授权 · 点击设置范围"))
        if hasattr(self,"watch_status"):self.watch_status.set(status["watch"]["error"] or ("观察 %s 个文件、%s 个日志；待合并 %s 项"%(status["watch"]["files"],status["watch"]["logs"],status["watch"]["pending"]) if cfg["watch"]["enabled"] else "已暂停；不会读取目录或日志"))
