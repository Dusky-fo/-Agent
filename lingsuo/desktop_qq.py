"""Native QQScreens extracted from the existing desktop, no separate process."""
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog
from .desktop_skin import C,FONT,CheckToggle,MaskedSecretEntry,PageStack

class QQScreens:
    def _notifications(self):
        cfg = self.app.config.get()["qq"]
        for key, value in (("appid", cfg["appid"]), ("secret", ""), ("qq_openids", ", ".join(cfg["allowed_users"]))):
            self.vars[key] = tk.StringVar(value=value)
        self.vars["qq_enabled"] = tk.BooleanVar(value=cfg["enabled"])
        self.vars["qq_sandbox"] = tk.BooleanVar(value=cfg["is_sandbox"])
        self.vars["notify_user"] = tk.StringVar()
        self.qq_status = tk.StringVar()
        self.notify_user_combo = self.test_tree = self.history_tree = None
        self.users_note = tk.StringVar()
        body = ttk.Frame(self.pages["notifications"], padding=(20, 6, 20, 12))
        body.pack(fill="both", expand=True)
        self.qq_wizard_tabs=ttk.Notebook(body);self.qq_wizard_tabs.pack(fill="both",expand=True)
        quick=ttk.Frame(self.qq_wizard_tabs,padding=12);advanced=ttk.Frame(self.qq_wizard_tabs,padding=12)
        self.qq_wizard_tabs.add(quick,text="快速接入");self.qq_wizard_tabs.add(advanced,text="高级手动配置")
        self.build_qq_form(advanced);advanced_secret=self.qq_secret_input
        self.build_qq_wizard(quick)
        self.qq_secret_inputs=[self.qq_secret_input,advanced_secret]
        self.qq_form_baseline=self.qq_draft_values()

    def build_qq_wizard(self,page):
        self.label(page,"先保存并连接自己的机器人，再绑定本人。尚未准备机器人时，可以先使用本地监控。")
        footer=ttk.Frame(page);footer.pack(side="bottom",fill="x",pady=5)
        flow=self.row(footer);self.qq_flow=flow
        self.button(flow,"quick_previous","上一步",lambda:self.qq_stage(max(0,self.qq_stage_index-1))).pack(side="left")
        self.qq_step_label=tk.StringVar();ttk.Label(flow,textvariable=self.qq_step_label).pack(side="left",fill="x",expand=True,padx=12)
        self.button(flow,"quick_next","下一步",self.qq_next).pack(side="right")
        support=self.row(footer)
        self.button(support,"quick_monitor","先用本地监控",lambda:self.show("monitor")).pack(side="left")
        self.button(support,"quick_folders","文件夹权限",self.permissions_dialog).pack(side="right")
        self.qq_step_pages=PageStack(page);self.qq_step_pages.pack(fill="both",expand=True)
        self.qq_stage_frames=[]
        for title in ("连接机器人","绑定本人","共享与验收"):
            frame=ttk.Frame(self.qq_step_pages);self.qq_step_pages.add(frame,text=title);self.qq_stage_frames.append(frame)
        form=self.scroll_area(self.qq_stage_frames[0])
        self.button(form,"quick_official","1  打开 QQ 官方平台，创建自己的机器人",lambda:self.open_url("https://q.qq.com/")).pack(anchor="w",pady=5)
        self.label(form,"AppID / AppSecret 是机器人应用凭据，不是个人QQ号码或密码。平台创建和审核需自行完成。")
        ttk.Label(form,text="2  填写应用凭据").pack(anchor="w",pady=(7,2))
        for key,label in (("appid","AppID"),("secret","AppSecret（已保存时留空保留）")):
            ttk.Label(form,text=label).pack(anchor="w")
            if key=="secret":
                self.qq_secret_input=MaskedSecretEntry(form,self.vars[key],saved=bool(self.app.config.get()["qq"]["secret"]))
                self.qq_secret_input.pack(fill="x",pady=5)
            else:ttk.Entry(form,textvariable=self.vars[key]).pack(fill="x",pady=5)
        CheckToggle(form,"沙箱环境",self.vars["qq_sandbox"]).pack(anchor="w")
        row=self.row(footer);row.pack_configure(before=flow);self.qq_primary_bar=row
        self.button(row,"quick_connect","保存并连接",lambda:self.save_qq(reconnect=True)).pack(side="left",padx=(0,8))
        self.button(row,"quick_only_save","仅保存",lambda:self.save_qq(reconnect=False)).pack(side="left")
        ttk.Label(form,textvariable=self.qq_status,wraplength=800).pack(anchor="w",pady=7)
        form=self.scroll_area(self.qq_stage_frames[1])
        ttk.Label(form,text="3  绑定本人（首次确认一次，不用复制OpenID）").pack(anchor="w",pady=(7,3))
        self.pairing_note=tk.StringVar()
        ttk.Label(form,textvariable=self.pairing_note,wraplength=800).pack(anchor="w")
        row=self.row(form)
        def start_pair():
            if self.qq_dirty():raise ValueError("先保存当前凭据并连接，再开始绑定")
            self.submit(self.app.start_pairing)
        self.button(row,"quick_pair_start","开始绑定 · 2分钟",start_pair).pack(side="left",padx=(0,8))
        self.button(row,"quick_pair_cancel","取消本次绑定",self.app.pairing.cancel).pack(side="left")
        self.pairing_tree=self.table(form,[("code","核对手机上的配对码"),("user","发送人身份")],height=2,expand=False)
        self.pairing_tree.empty_note.configure(text="开始绑定后，用本人QQ向机器人发送“开启”")
        def confirm_pair():
            if self.qq_dirty():raise ValueError("凭据有未保存修改，请先保存并连接后重新绑定")
            ident=self.selected(self.pairing_tree);row=next(r for r in self.app.pairing.status()["requests"] if r["id"]==ident)
            if not self.confirm("确认本人配对","请核对本人QQ收到的配对码："+row["code"]+"\n\n一致才确认。绑定不会开放文件目录。"):return
            def done(result):
                current=self.qq_draft_values();old=self.qq_form_baseline;saved=self.app.config.get()["qq"]
                baseline=(saved["appid"],"",", ".join(saved["allowed_users"]),saved["is_sandbox"])
                for i,key in enumerate(("appid","secret","qq_openids","qq_sandbox")):
                    if current[i]==old[i]:self.vars[key].set(baseline[i])
                self.qq_form_baseline=baseline;self.result_message(result)
                if not self.qq_dirty():self.qq_stage(2)
            self.submit(lambda:self.app.confirm_pairing(ident,row["code"]),done)
        self.button(form,"quick_pair_confirm","配对码一致，确认绑定本人",confirm_pair).pack(anchor="w",pady=5)
        self.label(form,"绑定后到文件夹权限选择QQ共享范围。手机提醒仍受平台回复窗口限制；请用自己的测试文件核对实际收发。")
        form=self.scroll_area(self.qq_stage_frames[2]);self.qq_ready_note=tk.StringVar()
        ttk.Label(form,text="共享范围与真实验收",font=(FONT,12,"bold")).pack(anchor="w",pady=10)
        ttk.Label(form,textvariable=self.qq_ready_note,wraplength=720).pack(anchor="w",pady=12)
        self.button(form,"quick_share","选择QQ共享目录",self.permissions_dialog).pack(anchor="w",pady=8)
        self.button(form,"quick_send_page","前往文件收发",lambda:self.show("files")).pack(anchor="w",pady=8)
        self.label(form,"目录默认不开放。用小测试文件核对实际收发；平台接受不代表手机已经收到，实际收到后再确认。")
        self.qq_stage(0)

    def qq_stage(self,index):
        self.qq_stage_index=index;self.qq_step_pages.select(self.qq_stage_frames[index])
        if index==0:
            if not self.qq_primary_bar.winfo_manager():self.qq_primary_bar.pack(fill="x",before=self.qq_flow,pady=5)
        else:self.qq_primary_bar.pack_forget()
        self.qq_step_label.set(("1 / 3  连接自己的机器人","2 / 3  首次授权本人","3 / 3  共享与实际验收")[index])

    def qq_next(self):
        if self.qq_stage_index==0 and (self.qq_dirty() or not self.app.gateway.connected):raise ValueError("请先保存并连接，等待真实消息通道就绪。")
        if self.qq_stage_index==1 and not self.app.config.get()["qq"]["allowed_users"]:raise ValueError("请先核对本人配对码并在本机确认绑定。")
        if self.qq_stage_index==2:self.show("files")
        else:self.qq_stage(self.qq_stage_index+1)

    def notification_dialog(self):
        dialog, body = self.dialog("通知管理", width=920, height=680)
        self.button(body, "notification_close", "关闭", dialog.destroy).pack(side="bottom", anchor="e", pady=(10, 0))
        page = self.scroll_area(body)
        ttk.Label(page, textvariable=self.qq_status, wraplength=830).pack(anchor="w", pady=(0, 8))
        row = self.row(page)
        self.button(row, "disconnect_qq", "临时断开 QQ", lambda: self.submit(self.app.disconnect_qq, self.result_message)).pack(side="left", padx=(0, 8))
        self.button(row, "disable_qq", "关闭 QQ", self.disable_qq).pack(side="left")
        ttk.Label(page, text="接收人与通知", foreground=C["txt"], font=(FONT, 11, "bold")).pack(anchor="w", pady=(8, 4))
        row = self.row(page)
        self.notify_user_combo = ttk.Combobox(row, textvariable=self.vars["notify_user"], state="readonly", width=20)
        self.notify_user_combo.pack(side="left", fill="x", expand=True)
        for key, text, fn in (("subscribe", "订阅", lambda: self.subscription(True)), ("unsubscribe", "退订", lambda: self.subscription(False)),
                              ("revoke", "撤销授权", self.revoke), ("test_notify", "发送测试通知", self.test_notify)):
            self.button(row, key, text, fn).pack(side="left", padx=3)
        self.users_note = tk.StringVar()
        ttk.Label(page, textvariable=self.users_note, wraplength=820).pack(anchor="w")
        self.test_tree = self.table(page, [("id", "测试编号"), ("user", "接收人"), ("state", "真实状态"), ("error", "失败原因")], height=3)
        self.button(page, "receipt", "我确认手机已收到选中编号", self.confirm_receipt).pack(anchor="w")
        self.label(page, "已入队 → 平台接受 → 你在手机核对后确认。平台接受不等于手机送达。")
        self.request_snapshot()

    def history_dialog(self):
        dialog, body = self.dialog("运行记录", width=900, height=600)
        self.button(body, "history_close", "关闭", dialog.destroy).pack(side="bottom", anchor="e", pady=(10, 0))
        ttk.Label(body, text="运行记录", style="Title.TLabel").pack(anchor="w", pady=(0, 12))
        self.history_tree = self.table(body, [("time", "时间"), ("kind", "类型"), ("text", "记录")], height=10)
        self.request_snapshot()

    def reset_qq_form(self):
        cfg = self.app.config.get()["qq"]
        for key, value in (("appid", cfg["appid"]), ("secret", ""), ("qq_openids", ", ".join(cfg["allowed_users"])),
                           ("qq_sandbox", cfg["is_sandbox"])):
            self.vars[key].set(value)
        self.qq_form_baseline=self.qq_draft_values()

    def build_qq_form(self, body):
        cfg = self.app.config.get()["qq"]
        ttk.Label(body, text="QQ 机器人", foreground=C["txt"], font=(FONT, 12, "bold")).pack(anchor="w", pady=(0, 12))
        footer = ttk.Frame(body); footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.button(footer, "qq_save", "保存", lambda: self.save_qq(reconnect=False)).pack(side="right")
        self.button(footer, "qq_cancel", "取消更改", self.reset_qq_form).pack(side="right", padx=8)
        self.button(footer, "check_qq", "检查应用凭据", self.check_qq).pack(side="left")
        self.button(footer, "connect_qq", "应用已保存配置并重连", lambda: self.submit(self.app.reconnect_qq, self.result_message)).pack(side="left", padx=8)
        ttk.Label(body, textvariable=self.qq_status, wraplength=690, font=(FONT, 9)).pack(side="bottom", anchor="w", pady=(8, 0))
        # Keep all original fields together above the separately scrolling help.
        for key, label in (("appid", "AppID"), ("secret", "AppSecret（留空 = 不修改）"),
                           ("qq_openids", "接收通知的 openid（多个用逗号分隔）")):
            ttk.Label(body, text=label, font=(FONT, 9)).pack(anchor="w", pady=(4, 5))
            if key == "secret":
                self.qq_secret_input = MaskedSecretEntry(body, self.vars[key], saved=bool(cfg["secret"]))
                self.qq_secret_input.pack(fill="x", pady=(0, 8))
            else:
                ttk.Entry(body, textvariable=self.vars[key]).pack(fill="x", pady=(0, 8))
        self.qq_secret_note = tk.StringVar()
        ttk.Label(body, textvariable=self.qq_secret_note,
                  wraplength=690, font=(FONT, 9)).pack(anchor="w")
        self.qq_sandbox_check = CheckToggle(body, "沙箱环境", self.vars["qq_sandbox"])
        self.qq_sandbox_check.pack(anchor="w", pady=(6, 8))
        help_area = self.scroll_area(body)
        ttk.Label(help_area, text="怎么申请与接入", foreground=C["txt"], font=(FONT, 10, "bold")).pack(anchor="w", pady=(2, 10))
        self.button(help_area, "official", "1  打开 https://q.qq.com/（QQ 开放平台）", lambda: self.open_url("https://q.qq.com/")).pack(anchor="w", pady=(0, 10))
        self.label(help_area, "使用自己的 QQ 登录，按官方平台要求注册开发者。")
        for text in (
            "2  点「创建机器人」→ 填名称、头像、简介 → 按平台要求提交。",
            "3  进入机器人后台 →「开发设置」→ 复制 AppID 和 AppSecret。",
            "4  填到上面第一、二栏，选择沙箱环境后保存；点击下方「应用已保存配置并重连」。",
            "5  用自己的 QQ 私聊这个机器人发送「开启」，它会回复你自己的 OpenID。",
            "6  把 OpenID 填到第三栏 → 本机核对并保存；新增接收人自动订阅通知。",
            "一般先用沙箱环境：在官方平台把自己加入沙箱单聊/体验成员名单。沙箱消息范围受平台限制，正式环境需按要求开通权限、配置 IP 白名单及审核。以平台当前界面为准。",
            "收不到消息时，查看连接状态或“记录 → 查看告警事件”。保存不代表连接成功，平台接受不代表手机收到。没有发起配对时，“开启”仅回复自己的 OpenID；开始配对后仍须本机确认，绑定不开放文件目录。"):
            self.label(help_area, text)

    def disable_qq(self):
        def stop():
            self.app.save_section("qq", {"enabled":False})
            return {"message":"QQ 已关闭，本地监控继续运行。"}
        self.submit(stop, self.result_message)

    def result_message(self, result):
        self.message.set(result.get("message", "操作已完成。"))

    def check_qq(self):
        appid, secret = self.vars["appid"].get().strip(), self.vars["secret"].get().strip()
        self.submit(lambda: self.app.check_qq(appid, secret), self.result_message)

    def save_qq(self, enabled=True, *, reconnect=True, close_dialog=None):
        submitted=self.qq_draft_values()
        values = {"appid": self.vars["appid"].get().strip(), "secret": self.vars["secret"].get().strip(),
                  "enabled": enabled, "is_sandbox": self.vars["qq_sandbox"].get()}
        old = self.app.config.get()["qq"]
        users = self.app.parse_openids(self.vars["qq_openids"].get())
        if any(values[k] != old[k] for k in ("appid", "is_sandbox")):
            if not self.confirm("更换机器人或环境", "更换 AppID 或正式/沙箱环境会撤销旧接收人、订阅和待发通知，需要重新获取并填写 OpenID。确认继续？"): return
            if users == old["allowed_users"]:
                users = []; self.vars["qq_openids"].set("")
        if users != old["allowed_users"]:
            text = "将保存这些接收人的本机访问授权，新增接收人会订阅通知，移除者将失去文件访问与通知权限。请核对来自本人机器人回复的 OpenID：\n\n" + ("\n".join(users) if users else "（空：清除接收人授权）")
            if not self.confirm("确认接收人 OpenID 授权", text): return
        def save():
            self.app.save_section("qq", values)
            self.app.save_manual_users(users)
            return self.app.reconnect_qq() if values["enabled"] and reconnect else {"message": "设置已保存。请点击「应用已保存配置并重连」；本地监控继续运行。"}
        def done(result):
            saved=self.app.config.get()["qq"]
            baseline=(saved["appid"],"",", ".join(saved["allowed_users"]),saved["is_sandbox"])
            current=self.qq_draft_values()
            for index,key in enumerate(("appid","secret","qq_openids","qq_sandbox")):
                if current[index]==submitted[index]:self.vars[key].set(baseline[index])
            self.vars["qq_enabled"].set(saved["enabled"]);self.qq_form_baseline=baseline;self.result_message(result)
            if self.qq_dirty():self.message.set("提交的配置已保存；你后来输入的内容仍未保存。")
            elif reconnect and self.app.gateway.connected:self.qq_stage(1)
            if close_dialog and close_dialog.winfo_exists() and not self.qq_dirty(): close_dialog.destroy()
        self.submit(save, done)

    def notify_user(self):
        user = self.vars["notify_user"].get()
        if user not in self.app.config.get()["qq"]["allowed_users"]: raise ValueError("请先绑定本人并选择接收人。")
        return user

    def subscription(self, enabled):
        user = self.notify_user()
        self.submit(lambda: self.app.store.subscribe(user, enabled))

    def revoke(self):
        user = self.notify_user()
        if not self.confirm("撤销接收人授权", "将撤销该用户的文件访问和通知订阅：\n" + user): return
        def revoke():
            with self.app.settings_lock:
                cfg = self.app.config.get()
                cfg["qq"]["allowed_users"] = [u for u in cfg["qq"]["allowed_users"] if u != user]
                self.app.save_config(cfg); self.app.store.subscribe(user, False)
        self.submit(revoke, lambda _: self.vars["qq_openids"].set(", ".join(self.app.config.get()["qq"]["allowed_users"])))

    def test_notify(self):
        user = self.notify_user()
        self.submit(lambda: self.app.queue_test(user), lambda r: self.message.set("测试 %s 已入队，等待平台结果，请在手机核对编号。" % r["id"]))

    def confirm_receipt(self):
        ident = self.selected(self.test_tree)
        self.submit(lambda: self.app.confirm_test(ident))

