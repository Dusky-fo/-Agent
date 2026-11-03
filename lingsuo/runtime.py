import threading
import time
from pathlib import Path
from types import SimpleNamespace
from .pairing import Pairing
from .config import Config, validate
import copy
import re
from . import __version__
from .store import Store
from .files import FileService
from .local_data import LocalDocuments
from .monitor import ProcessMonitor
from .plugins import Plugins
from .qq import QQGateway, QQTransport, QQError
from .commands import Commands
from .automation import AutomationEngine
from .tasks import Tasks, Outcome,TaskCancelled,TaskTimedOut
from .observers import FileLogObserver, AnomalyObserver, safe_path
from .inbound import Inbox
from .file_manager import FileManager
from .conversion import Documents
from .tools import ToolExecutor, WebTasks


class Runtime:
    def __init__(self, directory, collector=None, transport=None, desktop=None, gateway_factory=QQGateway, transport_factory=QQTransport):
        self.directory = Path(directory)
        self.config = Config(directory)
        self.store = Store(self.directory / "state.sqlite3")
        self.tasks = Tasks(self.store)
        self.files = FileService(self.config, directory, **({"desktop": desktop} if desktop else {}), event=self.store.event)
        self.documents = LocalDocuments(self.config, directory)
        self.transport = transport or QQTransport(self.config)
        self.gateway = gateway_factory(self.config, self.receive, self.store.event)
        self.transport_factory = transport_factory
        self.pairing = Pairing(self.config, self.store)
        self.settings_lock = threading.RLock()
        self.plugins = Plugins(self.directory / "plugins", self.config)
        self.monitor = ProcessMonitor(self.config, self.emit, **({"collector": collector} if collector else {}))
        self.watch = FileLogObserver(self.config,directory,self.emit)
        self.anomalies = AnomalyObserver(self.config,self.monitor,self.emit)
        self.inbox = Inbox(self.config,directory,self.tasks)
        self.file_manager=FileManager(self.config,directory,self.tasks)
        self.conversion=Documents(self.file_manager)
        self.web=WebTasks()
        self.tools=ToolExecutor(self.tasks)
        string=lambda:{"type":"string","required":True}
        self.tools.register("files.search","搜索本机文件管理授权目录",lambda context,query:{"files":self.file_manager.search(query)},{"query":string()},{"files.read"})
        self.tools.register("files.apply","执行已在本机预览的文件变更",lambda context,plan:self.file_manager.execute(plan,context),{"plan":string()},{"files.write"})
        self.tools.register("files.recover","恢复有备份的本机文件变更",lambda context,ident:self.file_manager.recover(ident,context),{"ident":string()},{"files.write"})
        self.tools.register("web.read","读取本机逐次授权的公开 HTTPS 页面",lambda context,url,consent:self.web.read(url,consent,context),{"url":string(),"consent":string()},{"web.read"})
        self.tools.register("tasks.status","查询属于调用者的任务",lambda context,ident:{"task":self.tasks.get(ident,context.owner)}, {"ident":string()},{"tasks.read"},("local","qq"))
        def plugin_tool(context,ident,tool,arguments):
            import json
            try:args=json.loads(arguments)
            except ValueError:raise ValueError("插件参数须为 JSON 对象") from None
            context.progress(10,"执行本机明确授权的受信插件")
            return {"plugin":ident,"result":self.plugins.call(ident,tool,args)}
        self.tools.register("plugins.invoke","调用已信任且已加载的本地插件",plugin_tool,{"ident":string(),"tool":string(),"arguments":string()},{"plugins.execute"})
        def convert(context,source,target,sha256):
            expected=self.conversion.preview(source,target)
            if expected["sha256"]!=sha256:raise ValueError("文档内容与预览不同")
            return self.conversion.execute(expected,context)
        self.tools.register("documents.convert","转换已预览的本机授权文档",convert,{"source":string(),"target":string(),"sha256":string()},{"files.write"})
        self.commands = Commands(self)
        self.automation = AutomationEngine(tasks=self.tasks)
        self.stop_event = threading.Event()
        self.worker = None
        self.observer_worker = None
        self.started = False
        self.flush_lock = threading.Lock()
        self.request_lock=threading.RLock();self.active_requests={}

    def start(self):
        self.plugins.load_enabled()
        self.monitor.start()
        self.gateway.start()
        self.worker = threading.Thread(target=self.run, daemon=True, name="notifications")
        self.worker.start()
        self.observer_worker=threading.Thread(target=self.observe,daemon=True,name="file-log-anomalies")
        self.observer_worker.start()
        self.started = True
        self.store.event("core.started", "本地核心已启动")

    def emit(self, kind, text, key):
        self.store.event(kind, text)
        cfg = self.config.get()
        allowed = set(cfg["qq"]["allowed_users"])
        for user in self.store.subscribers():
            if user in allowed:
                self.store.enqueue(user, text, key, cfg["notifications"]["cooldown"])

    def queue_test(self, user):
        qq = self.config.get()["qq"]
        if user not in qq["allowed_users"]:
            raise PermissionError("接收人未授权")
        if not qq["enabled"]:
            raise ValueError("QQ 尚未启用，请先保存应用信息并连接")
        return self.store.create_test(user, self.qq_scope(qq))

    @staticmethod
    def qq_scope(qq):
        return qq["appid"] + (":sandbox" if qq["is_sandbox"] else "")

    def queue_local_text(self, user, text):
        import secrets
        with self.settings_lock:
            qq = self.config.get()["qq"]
            if not qq["enabled"] or user not in qq["allowed_users"]:
                raise PermissionError("请先连接 QQ 并绑定本人接收人")
            if not isinstance(text, str) or not text.strip() or len(text) > 2500:
                raise ValueError("请手动选择 1–2500 字的资料内容；不会自动截断全文")
            if not self.store.enqueue(user, text, "local-text:" + secrets.token_hex(8), 0):
                raise ValueError("通知队列已满，请稍后再试")
            self.store.event("document.queued", "已在本机确认并将资料内容入队，未记录正文")

    def save_document(self,expected,text):
        saved={}
        def work(context):
            context.progress(20,"核对版本并备份原文")
            saved.update(self.documents.save(expected,text))
            return {"file":saved["name"],"backup_sha256":expected["sha256"]}
        self.tasks.run("documents.save","",work,60)
        return saved

    def save_config(self, new):
        with self.settings_lock, self.flush_lock:
            new = validate(copy.deepcopy(new))
            old = self.config.get()
            for section,key in (("watch","roots"),("watch","logs"),("file_manager","roots")):
                for path in new[section][key]:
                    if path not in old[section][key]:
                        candidate=safe_path(path,self.directory)
                        if not (candidate.is_file() if key=="logs" else candidate.is_dir()):raise ValueError("日志须选文件，观察/管理范围须选目录")
            if new["inbound"]["directory"] and new["inbound"]["directory"]!=old["inbound"]["directory"]:
                if not safe_path(new["inbound"]["directory"],self.directory).is_dir():raise ValueError("附件接收范围须选现有目录")
            changed = any(new["qq"][k] != old["qq"][k] for k in ("appid", "secret", "enabled", "is_sandbox"))
            if changed:
                self.pairing.cancel()
                if not self.gateway.stop():
                    raise ValueError("旧 QQ 通道正在停止，请等待后重试保存；本地监控未停止")
            if any(new["qq"][k] != old["qq"][k] for k in ("appid", "is_sandbox")):
                new["qq"]["allowed_users"] = []
                self.store.cancel_pending()
                for user in self.store.subscribers():
                    self.store.subscribe(user, False)
                self.commands.pending.clear()
                self.inbox.pending.clear()
            self.config.save(new)
            self.store.event("settings.saved", "设置已保存；QQ 配置变更后请点击连接")

    def save_section(self, section, values):
        if section not in ("monitor", "files", "local_data", "notifications", "panel", "qq", "watch", "anomalies", "inbound", "file_manager") or not isinstance(values, dict):
            raise ValueError("不支持此设置项")
        with self.settings_lock:
            cfg = self.config.get()
            allowed = set(cfg[section]) - ({"allowed_users"} if section == "qq" else set())
            if set(values) - allowed:
                raise ValueError("设置中含未知字段；接收人须通过本机配对确认")
            if section == "qq" and values.get("appid", cfg["qq"]["appid"]) != cfg["qq"]["appid"] and not values.get("secret"):
                raise ValueError("更换 AppID 时必须填写该机器人的 AppSecret，不能沿用旧应用密钥")
            if section == "qq" and not values.get("secret"):
                values = {k: v for k, v in values.items() if k != "secret"}
            cfg[section].update(values)
            self.save_config(cfg)

    @staticmethod
    def parse_openids(value):
        if not isinstance(value, str) or len(value) > 65536:
            raise ValueError("接收人 OpenID 格式无效")
        users = list(dict.fromkeys(user for user in re.split(r"[,，;；\s]+", value.strip()) if user)) if value.strip() else []
        if len(users) > 200 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", user) or (user.isdecimal() and len(user) < 32) for user in users):
            raise ValueError("请粘贴机器人回复的 OpenID，多个用逗号分隔；不是个人 QQ 号码")
        return users

    def save_manual_users(self, users):
        """Explicitly local authorization; never exposed as a QQ command or generic setting."""
        if not isinstance(users, list) or any(not isinstance(user, str) for user in users):
            raise ValueError("接收人 OpenID 列表无效")
        users = self.parse_openids(",".join(users))
        with self.settings_lock, self.flush_lock:
            old = self.config.get(); cfg = copy.deepcopy(old)
            if users and (not cfg["qq"]["appid"] or not cfg["qq"]["secret"]):
                raise ValueError("请先保存机器人 AppID 和 AppSecret，再保存接收人 OpenID")
            cfg["qq"]["allowed_users"] = users
            self.config.save(cfg)
            previous_subscriptions = set(self.store.subscribers())
            try:
                for removed in set(old["qq"]["allowed_users"]) - set(users): self.store.subscribe(removed, False)
                for added in set(users) - set(old["qq"]["allowed_users"]): self.store.subscribe(added, True)
            except Exception:
                self.config.save(old)
                # Reconcile subscription changes when the storage layer is still usable.
                for user in set(users) | set(old["qq"]["allowed_users"]):
                    try: self.store.subscribe(user, user in previous_subscriptions)
                    except Exception: pass
                raise
            self.pairing.cancel(); self.commands.pending.clear()
            self.store.event("qq.authorization", "已在本机保存 %s 个接收人 OpenID 授权" % len(users))
        return {"ok":True, "message":"接收人授权已保存；新接收人已订阅通知，文件目录仍需单独授权。"}

    def confirm_pairing(self, ident, code):
        with self.settings_lock, self.flush_lock:
            result = self.pairing.confirm(str(ident), str(code))
            self.store.enqueue(result["user"], "已在本机确认绑定，通知已开启。发送“状态”查看监控，发送“停止”退订。文件共享范围仍需在本机单独设置。", "pairing-confirmed", 0)
            return result

    def start_pairing(self):
        if not self.gateway.connected:raise ValueError("先保存并连接机器人，消息通道连接后才能开始绑定")
        return self.pairing.arm()

    def check_qq(self, appid, secret=""):
        cfg = self.config.get()
        appid = str(appid).strip()
        if not secret and appid == cfg["qq"]["appid"]:
            secret = cfg["qq"]["secret"]
        if not appid or not secret or len(appid) > 1024 or len(secret) > 1024:
            raise ValueError("请填写机器人 AppID 与 AppSecret；它们不是个人 QQ 账号和密码")
        cfg["qq"].update(enabled=True, appid=appid, secret=secret)
        return self.transport_factory(SimpleNamespace(get=lambda: cfg)).check_credentials()

    def reconnect_qq(self):
        with self.settings_lock:
            if not self.config.get()["qq"]["enabled"]:
                raise ValueError("请先保存并启用 QQ 应用配置")
            self.pairing.cancel()
            if not self.gateway.stop():
                raise ValueError("旧 QQ 通道尚未停止，请稍后再点连接，不会创建重复连接")
            self.gateway.start()
            return {"ok": True, "message": "已开始连接 QQ，请等待消息通道状态；本地监控未重启。", "qq": self.gateway.status()}

    def disconnect_qq(self):
        with self.settings_lock:
            self.pairing.cancel()
            if not self.gateway.stop():
                raise ValueError("QQ 正在停止，请稍后查看；本地监控未停止")
            return {"ok": True, "message": "QQ 已断开，本地监控继续运行；重启程序后仍按已保存配置启动。"}

    def confirm_test(self, ident):
        qq = self.config.get()["qq"]
        self.store.confirm_test(str(ident), self.qq_scope(qq), qq["allowed_users"])

    def flush_once(self):
        self.store.expire_pending()
        if not self.gateway.connected:
            return
        with self.flush_lock:
            cfg = self.config.get()
            for item in self.store.due():
                with self.request_lock:
                    if self.active_requests.get(item["user"],0):continue
                if not self.store.claim_outbox(item["id"]):continue
                error = ""
                uncertain=False
                try:
                    if not cfg["qq"]["enabled"] or item["user"] not in cfg["qq"]["allowed_users"]:
                        raise PermissionError("QQ 已禁用或接收人已撤销授权")
                    reply=self.store.reserve_reply(item["user"],self.qq_scope(cfg["qq"]))
                    if reply is None:
                        self.store.defer(item,"等待用户近期消息：请向机器人发送“状态”；不保证主动推送可用")
                        continue
                    if isinstance(self.transport,QQTransport):self.transport.send_text(item["user"],item["text"],reply[0],sequence=reply[1])
                    else:self.transport.send_text(item["user"],item["text"],reply[0])
                except Exception as exc:
                    uncertain=getattr(exc,"outcome_uncertain",False)
                    error = "通知提交失败（网络、权限或配额），请检查 QQ 状态"
                    self.store.event("notify.error", error)
                self.store.finish(item, error, cfg["notifications"]["max_attempts"],uncertain=uncertain)

    def run(self):
        while not self.stop_event.wait(2):
            self.flush_once()

    def observe(self):
        while not self.stop_event.wait(1):
            try:
                self.watch.tick();self.anomalies.tick()
            except Exception:
                self.watch.error="观察服务出现错误，请检查配置；不会报告虚构的退出"

    def send_test_file(self, user):
        """Write a tiny text file into the first authorized root and send it, to prove the channel."""
        roots = self.files.roots()
        if not roots:
            raise ValueError("先授权一个文件夹，再发送测试文件")
        path = roots[0] / ("灵梭测试-%s.txt" % time.strftime("%H%M%S"))
        path.write_text("这是灵梭的测试文件。\n生成时间：%s\n如果你在手机上看到它，说明文件通道是通的。\n"
                        % time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        return self.send_file(user, self.files.fingerprint(path))

    def send_file(self, user, fingerprint, message_id=None,task_started=None):
        def execute(context):
            if task_started:task_started(context.id)
            with self.settings_lock:
                cfg = self.config.get()
                if not cfg["qq"]["enabled"] or user not in cfg["qq"]["allowed_users"]:
                    raise PermissionError("QQ 未启用或接收人未授权")
                passive=message_id
                sequence=1
                if isinstance(self.transport,QQTransport):
                    reply=self.store.reserve_reply(user,self.qq_scope(cfg["qq"]),message_id=passive)
                    if not reply:raise ValueError("请先在 QQ 私聊机器人发送“状态”，再发送文件；主动消息受平台限制")
                    passive=reply[0]
                    sequence=reply[1]
            def guard():
                with self.settings_lock:
                    current=self.config.get()["qq"]
                    if not current["enabled"] or user not in current["allowed_users"] or any(current[k]!=cfg["qq"][k] for k in ("appid","secret","is_sandbox")):raise PermissionError("QQ或接收人授权已变化，停止后续文件提交")
                    self.files.authorize(fingerprint["path"])
            context.guard=guard
            context.progress(1,"再次核对授权与文件内容")
            with self.files.snapshot(fingerprint) as snapshot:
                context.check()
                if isinstance(self.transport,QQTransport):result=self.transport.send_file(user,snapshot,fingerprint["name"],passive,context=context,sequence=sequence)
                else:result=self.transport.send_file(user,snapshot,fingerprint["name"],passive)
            self.store.event("file.sent", "已向平台提交文件，尚不代表用户收到：" + fingerprint["name"])
            return Outcome({"file":fingerprint["name"],"size":fingerprint["size"],"receipt":result or {},"folder":str(Path(fingerprint["path"]).parent),"qq_scope":self.qq_scope(cfg["qq"])},"platform_accepted")
        return self.tasks.run("qq.file.send",user,execute,timeout=600)

    def receive(self, user, text, message_id, attachments=None):
        if len(text) > 2000 or not self.store.claim(message_id):
            return
        with self.request_lock:self.active_requests[user]=self.active_requests.get(user,0)+1
        try:self.handle_received(user,text,message_id,attachments)
        finally:
            with self.request_lock:
                self.active_requests[user]-=1
                if not self.active_requests[user]:self.active_requests.pop(user)

    def handle_received(self,user,text,message_id,attachments=None):
        cfg=self.config.get()["qq"];sequence=1
        if user in cfg["allowed_users"]:
            self.store.remember_reply(user,self.qq_scope(cfg),message_id)
            reply=self.store.reserve_reply(user,self.qq_scope(cfg),message_id)
            if not reply:return
            sequence=reply[1]
        try:
            answer = self.inbox.offer(user,attachments) if attachments else self.commands.handle(user, text, message_id)
        except (ValueError, PermissionError, QQError,TaskCancelled,TaskTimedOut) as exc:
            answer = str(exc)
            if getattr(exc,"task_id",None):answer+="\n任务 "+exc.task_id+"，可发送“任务 "+exc.task_id+"”查看状态。"
            self.store.event("command.error", answer)
        except Exception:
            answer = "操作未完成，请在本机检查设置和文件状态。"
            self.store.event("command.error", answer)
        try:
            if isinstance(self.transport,QQTransport):self.transport.send_text(user,answer,message_id,sequence=sequence)
            else:self.transport.send_text(user, answer, message_id)
        except Exception:
            self.store.event("reply.error", "QQ 回复提交失败，未自动重复发送")

    def status(self):
        cfg = self.config.get()
        roots = [str(p) for p in self.files.roots()]
        users = cfg["qq"]["allowed_users"]
        subscribed = set(self.store.subscribers())
        qq = self.gateway.status()
        if not cfg["qq"]["enabled"]:
            qq = {"state": "disabled", "message": "QQ 已禁用；本地监控可独立使用", "connected": False}
        if self.config.secret_error:qq={"state":"credential_error","message":self.config.secret_error,"connected":False}
        tests = self.store.tests(self.qq_scope(cfg["qq"]), users)
        monitor_error=self.monitor.sampling_error()
        return {"version":__version__, "monitor": cfg["monitor"], "processes": self.monitor.last,
                "local_running": bool(self.started and self.monitor.thread and self.monitor.thread.is_alive()),
                "monitor_error": monitor_error,"last_sample":self.monitor.sample_time, "qq_connected": self.gateway.connected,
                "qq": qq, "pairing": self.pairing.status(),
                "users": [{"user": u, "subscribed": u in subscribed} for u in users],
                "tests": tests,
                "watch":self.watch.status(),"anomalies":self.anomalies.status(),
                "todo": {"qq_connected": qq["connected"], "receiver_bound": bool(users),
                         "monitor_added": bool(cfg["monitor"]["process_names"]), "files_authorized": bool(roots)},
                "queue": self.store.queue_summary(), "desktop": str(self.files.desktop()),
                "roots": roots, "data_dir": str(self.directory)}

    def stop(self):
        self.automation.stop()
        self.tasks.shutdown()
        self.stop_event.set()
        self.watch.stop_event.set()
        self.monitor.stop()
        self.gateway.stop()
        if self.worker:
            self.worker.join(30)
        if self.observer_worker:self.observer_worker.join(30)
        with self.store.lock:active=self.store.db.execute("SELECT count(*) FROM tasks WHERE state='running'").fetchone()[0]
        if not active and (self.automation.thread is None or not self.automation.thread.is_alive()) and (self.worker is None or not self.worker.is_alive()) and (self.observer_worker is None or not self.observer_worker.is_alive()):
            self.store.close()
