"""Desktop experience helpers: drafts, grouped navigation, honest task summaries."""
import json
import time
import tkinter as tk
from tkinter import ttk,messagebox
from .desktop_skin import C,FONT,ActionCard,RoundedPanel
from .desktop_home import OverviewCard, overview_states, overview_tables


class KeepDraft(RuntimeError):pass


GROUPS={"notifications":"settings","plugins":"automation"}
TASK_NAMES={"qq.notification":"QQ 通知","qq.file.send":"发送文件","qq.attachment.receive":"接收附件","files.search":"文件搜索","files.apply":"本机文件变更","files.recover":"恢复文件","files.backup.cleanup":"清理冗余备份","documents.convert":"文档转换","documents.save":"保存文本","web.read":"网页读取","mouse.image":"图片识别点击","plugins.invoke":"插件工具","rules.workflow":"固定任务"}


def task_sentence(row):
    if row["received"]:return "你已确认收到"
    state=row["state"]
    if state=="queued":return row["error"] or "等待执行"
    if state=="running":return row["step"] or "正在处理"
    if state=="platform_accepted":return "QQ 平台已接受，手机是否收到尚未确认"
    if state=="completed":
        result=json.loads(row.get("result") or "{}")
        if result.get("file"):return "已完成："+str(result["file"])
        return row["step"] or "已完成"
    if state=="cancelled":return "已停止；已完成步骤可到恢复记录核对"
    if state=="uncertain":return "提交结果待核对，请先查看 QQ，避免重复发送"
    if state=="interrupted":return "上次退出时未完成，没有自动重跑"
    return row["error"] or ("已超时，请检查后重新操作" if state=="timed_out" else "未完成，请查看详情")


class ExperienceScreens:
    def qq_draft_values(self):return tuple(self.vars[k].get() for k in ("appid","secret","qq_openids","qq_sandbox"))

    def qq_dirty(self):return self.qq_draft_values()!=getattr(self,"qq_form_baseline",self.qq_draft_values())

    def dialog_inputs(self,dialog):
        values=[]
        def visit(widget):
            try:
                if getattr(widget,"draft_ignore",False):return
                if isinstance(widget,(ttk.Entry,ttk.Combobox)):values.append((str(widget),widget.get()))
                elif isinstance(widget,tk.Text) and str(widget.cget("state"))!="disabled":values.append((str(widget),widget.get("1.0","end-1c")))
                if hasattr(widget,"variable"):values.append((str(widget)+":check",widget.variable.get()))
                for child in widget.winfo_children():visit(child)
            except tk.TclError:pass
        visit(dialog);return values

    def mark_dialog_saved(self,dialog=None,values=None):
        dialog=dialog or getattr(self,"active_dialog",None)
        if dialog is not None and dialog.winfo_exists():dialog.draft_baseline=self.dialog_inputs(dialog) if values is None else values

    def allow_close_dialog(self,dialog):
        if not dialog.winfo_exists():return True
        if not getattr(dialog,"save_action",None) or self.dialog_inputs(dialog)==getattr(dialog,"draft_baseline",[]):return True
        choice=self.draft_choice(dialog.title())
        if choice=="discard":return True
        if choice=="save":
            action=dialog.save_action
            if getattr(self,"_active_action_key",None)==action[0]:return False
            action[1]()
            self.message.set("正在保存当前窗口；保存完成后可继续打开其他功能。")
        return False

    def close_dialog(self,dialog):
        if self.allow_close_dialog(dialog):dialog.destroy()

    def default_draft_choice(self,title):
        choice=messagebox.askyesnocancel("保留未保存的内容",title+"有未保存的修改。\n\n是：保存并留在当前窗口\n否：放弃修改并继续\n取消：继续编辑",parent=self.root)
        return "save" if choice is True else "discard" if choice is False else "continue"

    def setup_subnav(self,parent):
        self.subnav=ttk.Frame(parent);self.subnav.pack(fill="x",pady=(0,8))
        self.subnav_groups={}
        groups={"settings":(("notifications","QQ 接入"),("settings","通用设置"))}
        for group,items in groups.items():
            frame=ttk.Frame(self.subnav);self.subnav_groups[group]=frame
            for page,label in items:self.button(frame,"section_"+page,label,lambda p=page:self.open_notifications() if p=="notifications" else self.show(p)).pack(side="left",padx=(0,8))

    def update_navigation(self,key):
        group=GROUPS.get(key,key)
        for name,item in self.nav.items():item.select(name==group)
        for name,frame in self.subnav_groups.items():
            if name==group:frame.pack(fill="x")
            else:frame.pack_forget()
        self.page_title.set({"home":"总览","monitor":"监控","files":"文件","manage":"本机整理","tasks":"记录","automation":"工具","plugins":"插件","notifications":"设置 · QQ 接入","settings":"设置"}[key])

    def home_page(self):
        page=ttk.Frame(self.pages["home"]);page.pack(fill="both",expand=True)
        self.home_grid=ttk.Frame(page);self.home_grid.pack(fill="x",pady=(0,12))
        self.home_cards={}
        entries=(("monitor","本地进程监控","监控指定程序的运行状态。","home_monitor",lambda:self.show("monitor")),
                 ("qq","QQ 连接","连接机器人，收发文件消息。","home_qq",self.home_open_qq),
                 ("files","授权文件","设置 QQ 可访问的目录。","home_files",self.permissions_dialog))
        for col,(key,title,description,button,action) in enumerate(entries):
            self.home_grid.columnconfigure(col,weight=1,uniform="overview")
            card=OverviewCard(self.home_grid,title,description,self.button,button,action,kind=key)
            card.grid(row=0,column=col,sticky="nsew",padx=(0,12) if col<2 else 0)
            self.home_cards[key]=card
        self.home_columns=3
        self.home_grid.bind("<Configure>",lambda event:self.layout_home_cards(event.width))
        low=ttk.Frame(page);low.pack(fill="both",expand=True)
        low.rowconfigure(0,weight=1)
        low.columnconfigure(0,weight=19,uniform="home_low");low.columnconfigure(1,weight=10,uniform="home_low")
        left=RoundedPanel(low,pad=16,height=236);left.grid(row=0,column=0,sticky="nsew",padx=(0,16))
        right=RoundedPanel(low,pad=16,height=236);right.grid(row=0,column=1,sticky="nsew")
        self.home_activity_panel=left;self.home_todo_panel=right

        head=tk.Frame(left.body,bg=C["card2"]);head.pack(fill="x")
        tk.Label(head,text="最近活动",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        records=self.button(head,"home_records","全部记录 ›",lambda:self.show("tasks"))
        records.primary=False;records.ghost=True;records.configure(background=C["card2"],width=84,height=28)
        records.pack(side="right");records.draw()
        self.home_activity_list=tk.Frame(left.body,bg=C["card2"]);self.home_activity_list.pack(fill="both",expand=True,pady=(4,0))
        self.home_activity_rows=[]
        self.home_activity_empty=tk.Label(self.home_activity_list,text="还没有记录。添加一个监控程序后，这里会显示程序的启动与退出。",font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w",justify="left",wraplength=430)
        self.home_activity_add=self.button(self.home_activity_list,"home_activity_add","添加监控",lambda:self.show("monitor"))
        self.home_activity_add.primary=True;self.home_activity_add.configure(width=104,height=30)
        self.home_activity_more=tk.StringVar()
        tk.Label(left.body,textvariable=self.home_activity_more,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w").pack(fill="x")
        self.home_transfer=tk.StringVar()
        transfer=tk.Label(left.body,textvariable=self.home_transfer,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="w",justify="left")
        transfer.pack(fill="x",pady=(3,0));transfer.bind("<Configure>",lambda e:transfer.configure(wraplength=max(160,e.width)))

        thead=tk.Frame(right.body,bg=C["card2"]);thead.pack(fill="x")
        tk.Label(thead,text="待办",font=(FONT,11,"bold"),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left")
        self.home_todo_count=tk.StringVar()
        tk.Label(thead,textvariable=self.home_todo_count,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="e").pack(side="right")
        self.home_todo_rows=tk.Frame(right.body,bg=C["card2"]);self.home_todo_rows.pack(fill="both",expand=True,pady=(4,0))
        self.home_todo_button=self.button(right.body,"home_todo_next","去连接",self.home_todo_next)
        self.home_todo_button.primary=True;self.home_todo_button.configure(width=104,height=30)
        self.home_todo_button.pack(anchor="w")
        self.home_todo_target=None

    @staticmethod
    def activity_tone(kind):
        kind=str(kind)
        if "error" in kind or "fail" in kind:return "warn"
        if "exit" in kind:return "bad"
        if kind.startswith(("process.start","process.restore","qq.connected","file.sent","core.started")):return "ok"
        return "sub"

    def activity_row(self,parent,stamp,tone,text):
        line=tk.Frame(parent,bg=C["card2"],height=30);line.pack(fill="x");line.pack_propagate(False)
        tk.Label(line,text=stamp,font=(FONT,9),fg=C["sub"],bg=C["card2"],width=6,anchor="w").pack(side="left")
        dot=tk.Canvas(line,width=6,height=6,bg=C["card2"],highlightthickness=0,bd=0);dot.pack(side="left",padx=(2,9),pady=(12,0))
        dot.create_oval(0,0,5,5,fill=C[{"ok":"ok","warn":"warn","bad":"bad"}.get(tone,"sub")],outline="")
        tk.Label(line,text=text[:44],font=(FONT,9),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left",fill="x",expand=True)

    def todo_row(self,parent,label,done,value):
        line=tk.Frame(parent,bg=C["card2"],height=34);line.pack(fill="x");line.pack_propagate(False)
        box=tk.Canvas(line,width=14,height=14,bg=C["card2"],highlightthickness=0,bd=0);box.pack(side="left",pady=(10,0))
        if done:
            box.create_rectangle(0,0,13,13,fill=C["ok"],outline=C["ok"])
            box.create_line(3,7,6,10,11,4,fill=C["bg"],width=2)
        else:box.create_rectangle(0,0,13,13,fill=C["card2"],outline=C["sub"])
        tk.Label(line,text=label,font=(FONT,10),fg=C["txt"],bg=C["card2"],anchor="w").pack(side="left",padx=(9,0))
        tk.Label(line,text=value,font=(FONT,9),fg=C["sub"],bg=C["card2"],anchor="e").pack(side="right")

    def home_todo_next(self):
        target=getattr(self,"home_todo_target",None)
        if target is None:self.show("settings");return
        target()

    def render_home_todo(self,status):
        todo=status["todo"]
        items=(("qq_connected","QQ 接入","去连接",self.open_notifications),
               ("receiver_bound","本人授权","去绑定",self.open_notifications),
               ("files_authorized","共享目录","去设置",self.permissions_dialog))
        done=sum(bool(todo[key]) for key,_,_,_ in items)
        signature=(done,tuple(bool(todo[key]) for key,_,_,_ in items))
        if signature==getattr(self,"todo_signature",None):return
        self.todo_signature=signature
        self.home_todo_count.set("%s / %s"%(done,len(items)))
        for child in self.home_todo_rows.winfo_children():child.destroy()
        self.home_todo_target=None;first_verb=None
        for key,label,verb,fn in items:
            self.todo_row(self.home_todo_rows,label,bool(todo[key]),"已完成" if todo[key] else "待设置")
            if not todo[key] and self.home_todo_target is None:self.home_todo_target=fn;first_verb=verb
        text="查看验收步骤" if done==len(items) else first_verb
        button=self.home_todo_button
        if button.text!=text:button.text=text;button.draw()

    def render_home_activity(self,history):
        rows=[(time.strftime("%H:%M",time.localtime(r["ts"])),self.activity_tone(r["kind"]),r["text"]) for r in history[:3]]
        signature=(tuple(rows),len(history))
        self.home_activity_rows=rows
        if signature==getattr(self,"activity_signature",None):return
        self.activity_signature=signature
        for child in self.home_activity_list.winfo_children():
            if child is not self.home_activity_empty and child is not self.home_activity_add:child.destroy()
        if rows:
            self.home_activity_empty.pack_forget();self.home_activity_add.pack_forget()
            for stamp,tone,text in rows:self.activity_row(self.home_activity_list,stamp,tone,text)
            hidden=len(history)-len(rows)
            self.home_activity_more.set("还有 %s 条 · 保留最近 30 天"%hidden if hidden>0 else "保留最近 30 天")
        else:
            self.home_activity_empty.pack(anchor="w",pady=(2,10))
            self.home_activity_add.pack(anchor="w")
            self.home_activity_more.set("")

    def layout_home_cards(self,width):
        columns=3 if width>=660 else 1
        if columns==self.home_columns:return
        self.home_columns=columns
        for col in range(3):self.home_grid.columnconfigure(col,weight=1 if col<columns else 0,uniform="overview" if col<columns else "")
        for index,card in enumerate(self.home_cards.values()):
            card.grid_configure(row=index//columns,column=index%columns,padx=(0,12) if columns==3 and index<2 else 0,pady=0 if columns==3 else (0,12))

    def home_open_qq(self):
        self.open_notifications()
        if self.last_snapshot and self.last_snapshot["status"]["qq"]["connected"] and not self.last_snapshot["status"]["users"]:
            self.qq_stage(1)

    def settings_page(self):
        page=self.scroll_area(self.pages["settings"]);self.settings_summaries={};self.settings_cards={}
        grid=ttk.Frame(page);grid.pack(fill="x")
        for col in range(2):grid.columnconfigure(col,weight=1,uniform="settings")
        entries=(("qq","QQ 接入",self.open_notifications),("permissions","文件夹权限",self.permissions_dialog),("notify_manage","通知与订阅",self.notification_dialog),("appearance","外观皮肤",self.appearance_dialog),("advanced","通用与高级参数",self.advanced_dialog),("diagnostics","程序与数据位置",self.runtime_info))
        for i,(key,title,action) in enumerate(entries):
            var=tk.StringVar();self.settings_summaries[key]=var
            card=ActionCard(grid,title,var,self.guard_action("setting_"+key,action));card.grid(row=i//2,column=i%2,sticky="ew",padx=6,pady=7)
            self.settings_cards[key]=card;self.buttons["setting_"+key]=card.edit
        self.label(page,"监控规则在监控页，文件收发在文件页，图片识别与插件在工具页。这里集中保存通用设置。")

    def refresh_experience(self,status,cfg,tasks,history):
        current=self.current_page()
        if current=="home":
            for key,values in overview_states(status,cfg).items():self.home_cards[key].update_state(*values)
            for key,rows in overview_tables(status,cfg).items():self.home_cards[key].update_table(rows)
            self.render_home_activity(history)
            self.render_home_todo(status)
            self.home_transfer.set("手机提醒：QQ 需要你先发过消息才可能回复，不保证随时送达。")
        if current=="settings":
            summaries={"qq":status["qq"]["message"],"permissions":"集中管理目录用途\n本机、QQ、观察和接收分别授权","notify_manage":"订阅、测试与实际收件核对\nQQ 按需回复，手机提醒能力独立判断","appearance":"六套配色 · 保存后重启","advanced":"采集间隔、文件限制与通知策略","diagnostics":"版本、运行账户与本机数据位置"}
            for key,value in summaries.items():self.settings_summaries[key].set(value)
        if hasattr(self,"pairing_tree") and self.pairing_tree.winfo_exists():
            pairing=status["pairing"]
            self.fill(self.pairing_tree,[(r["id"],(r["code"],r["user"])) for r in pairing["requests"]])
            self.pairing_note.set("等待本人 QQ 发送“开启”，剩余 %s 秒"%pairing["seconds_left"] if pairing["active"] else ("已授权 %s 个接收人"%len(status["users"]) if status["users"] else "本机发起绑定后，再用本人 QQ 发送“开启”"))
        if hasattr(self,"qq_ready_note"):self.qq_ready_note.set("机器人：%s\n本人授权：%s 人\nQQ共享：%s 个有效目录\n文件实收仍需实际核对。"%(status["qq"]["message"],len(status["users"]),len(status["roots"])))
        if getattr(self,"file_task_id",None):
            row=next((r for r in tasks if r["id"]==self.file_task_id),None)
            if row:self.file_transfer_note.set("任务 "+row["id"]+" · "+task_sentence(row))

    def current_page(self):
        selected=self.tabs.select()
        return next((key for key,page in self.pages.items() if str(page)==selected),"home")
