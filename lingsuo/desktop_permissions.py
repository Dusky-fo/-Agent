"""Native folder permissions and file-level log authorization, all local-only."""
import tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog
from .desktop_skin import CheckToggle,C,FONT
from .permissions import rows,KINDS,apply_row
from .observers import safe_path


class PermissionScreens:
    def permissions_dialog(self):
        dialog,body,footer=self.dialog_shell("文件夹权限",
            "选择目录后分别开启所需用途。接收附件和本机管理不会自动开放给 QQ；仅本机资料会限制 QQ 取文件。",
            width=940, height=680)
        tree=self.table(body,[("path","文件夹"),("qq","QQ取文件"),("observe","观察"),("manage","本机管理"),("private","仅本机资料"),("receive","附件接收")],height=6)
        tree.empty_note.configure(text="尚未授权文件夹 · 点右下角选择目录，再开启所需用途")
        for key in KINDS:
            if key in tree["columns"]:tree.column(key,width=85,minwidth=70,stretch=False)
        tree.column("path",width=310,minwidth=180)
        selected=tk.StringVar();self.permission_path=selected;last_selection=[None]
        tk.Label(body,textvariable=selected,font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",wraplength=860).pack(fill="x",pady=(0,6))
        values={k:tk.BooleanVar(value=False) for k in KINDS};self.permission_values=values
        self.permission_tree=tree;current=[]
        def load():
            current[:]=rows(self.app.config.get(),self.app.files.desktop())
            self.fill(tree,[(r["path"],(r["path"],"受私有规则限制" if r["qq_blocked"] else "排除私有子目录" if r["qq_partial"] else "允许" if r["qq"] else "—","允许" if r["observe"] else ("指定日志" if r["logs"] else "—"),"允许" if r["manage"] else "—","是" if r["private"] else "—","已指定" if r["receive"] else "—")) for r in current])
            log_combo.configure(values=self.app.config.get()["watch"]["logs"])
        def pick(_=None):
            if not tree.selection():return
            ident=tree.selection()[0]
            if ident==last_selection[0]:return
            if not self.allow_close_dialog(dialog):
                if last_selection[0] and tree.exists(last_selection[0]):tree.selection_set(last_selection[0])
                else:tree.selection_remove(ident)
                return
            item=next(r for r in current if r["path"]==ident);selected.set(item["path"])
            last_selection[0]=ident
            for key,var in values.items():var.set(item[key])
            logs.set("指定日志："+("；".join(item["logs"]) or "无。添加日志不会授予整个目录的观察权限。"))
            self.mark_dialog_saved(dialog)
        tree.bind("<<TreeviewSelect>>",pick)
        labels={"private":"仅本机资料","qq":"允许 QQ 取文件","observe":"观察文件变化","manage":"本机管理与转换","receive":"作为附件接收目录"}
        line=tk.Frame(body,bg=C["card2"]);line.pack(fill="x",pady=(4,8))
        for key in KINDS:CheckToggle(line,labels[key],values[key],background=C["card2"]).pack(side="left",padx=(0,8))
        logs=tk.StringVar(value="指定日志不自动授权整个目录。")
        tk.Label(body,textvariable=logs,font=(FONT,9),fg=C["muted"],bg=C["card2"],anchor="w",justify="left",wraplength=860).pack(fill="x",pady=(0,6))
        log_selected=tk.StringVar();log_combo=ttk.Combobox(body,textvariable=log_selected,values=self.app.config.get()["watch"]["logs"],state="readonly");log_combo.draft_ignore=True
        log_combo.pack(fill="x",pady=4)
        def choose():
            if not self.allow_close_dialog(dialog):return
            path=filedialog.askdirectory(parent=dialog,title="选择需要配置用途的本机目录")
            if path:
                safe_path(path,self.app.directory);selected.set(path)
                existing=next((r for r in current if r["path"].casefold()==str(Path(path).resolve()).casefold()),{})
                for key,var in values.items():var.set(existing.get(key,False))
                self.mark_dialog_saved(dialog)
        def save():
            path=selected.get()
            if not path:raise ValueError("先选择或添加一个文件夹")
            grants={k:v.get() for k,v in values.items()};old=self.app.config.get()
            new=apply_row(old,path,grants,self.app.files.desktop())
            if new==old:return
            previous=next((r for r in rows(old,self.app.files.desktop()) if r["path"].casefold()==path.casefold()),{})
            added=[labels[k] for k in KINDS if grants[k] and not previous.get(k)]
            if added and not self.confirm("授权这个文件夹",path+"\n\n新增用途："+"、".join(added)+"\n\n各用途独立生效。确认授权？"):return
            def work():
                with self.app.settings_lock:
                    cfg=apply_row(self.app.config.get(),path,grants,self.app.files.desktop());self.app.save_config(cfg)
            self.submit(work,lambda _:load(),resource="settings")
        def remove():
            path=selected.get()
            if not path:raise ValueError("先选择目录")
            if not self.confirm("撤销该目录的权限",path+"\n\n同时移除这里指定的日志。相关任务及恢复操作将重新检查授权。确认撤销？"):return
            def work():
                with self.app.settings_lock:self.app.save_config(apply_row(self.app.config.get(),path,{k:False for k in KINDS},self.app.files.desktop(),remove=True))
            self.submit(work,lambda _:(selected.set(""),load()))
        def add_log():
            path=filedialog.askopenfilename(parent=dialog,title="明确授权一个日志文件")
            if not path:return
            if not self.confirm("授权日志增量读取",path+"\n\n只授权这个日志，不开放整个目录。确认添加？"):return
            def work():
                with self.app.settings_lock:
                    cfg=self.app.config.get()
                    if path not in cfg["watch"]["logs"]:cfg["watch"]["logs"].append(path)
                    self.app.save_config(cfg)
            self.submit(work,lambda _:load())
        def remove_log():
            path=log_selected.get()
            if not path:raise ValueError("先在日志列表选择要撤销的项；文件不存在也可以撤销")
            def work():
                with self.app.settings_lock:
                    cfg=self.app.config.get();cfg["watch"]["logs"]=[p for p in cfg["watch"]["logs"] if p.casefold()!=path.casefold()]
                    if not cfg["watch"]["roots"] and not cfg["watch"]["logs"]:cfg["watch"]["enabled"]=False
                    self.app.save_config(cfg)
            self.submit(work,lambda _:(load(),log_combo.configure(values=self.app.config.get()["watch"]["logs"]),log_selected.set("")))
        close=self.button(footer,"permissions_close","关闭",lambda:self.close_dialog(dialog))
        close.primary=False;close.ghost=True;close.configure(background=C["bg"],width=72,height=30)
        close.pack(side="left");close.draw()
        for key,label,fn in (("permissions_log","授权日志",add_log),("permissions_unlog","撤销日志",remove_log),("permissions_remove","撤销目录",remove),("permissions_add","选择目录",choose)):
            btn=self.button(footer,key,label,fn)
            btn.tone="soft";btn.configure(background=C["bg"],width=88,height=30)
            btn.pack(side="right",padx=(8,0));btn.draw()
        self.button(footer,"permissions_save","保存选中目录",save).pack(side="right")
        tk.Label(footer,text="接收目录最多一个，替换前先撤销旧目录。",font=(FONT,9),fg=C["muted"],bg=C["bg"],anchor="w").pack(side="left",padx=(10,0))
        load()
