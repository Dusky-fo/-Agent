import secrets
import threading
import time
import re
from pathlib import Path
from .intents import canonical, file_request, selection_number, CONFIRMATIONS
from .tasks import STATE_NAMES

HELP = "你可以说：把桌面的操作手册发给我 / 找一下报告 / 桌面有哪些文件 / 看看监控 / 开启提醒 / 关掉提醒。找到唯一文件后回复“发送”；多个结果先选编号，再回复“发送”。发给当前已授权的本人对话，无需核对接收人。发送“取消”放弃选择。\n发送“任务”查看本人任务；“任务 编号”“取消任务 编号”“收到任务 编号”查询、取消或确认收到。附件需本机开启接收后，回复“接收”或“接收 1”落盘，不自动运行。"


class Commands:
    def __init__(self, runtime):
        self.app = runtime
        self.pending, self.locks = {}, {}
        self.lock = threading.RLock()

    def handle(self, user, text, message_id):
        # Status/cancel must remain responsive while the same user's upload owns its file lock.
        normalized=canonical(text)
        if user in self.app.config.get()["qq"]["allowed_users"]:
            answer=self.task_command(user,normalized)
            if answer is not None:return answer
        with self.lock:
            lock = self.locks.setdefault(user, threading.Lock())
        with lock:
            text = canonical(text)
            cfg = self.app.config.get()
            if text in ("我的ID", "我的id", "openid", "OpenID"):
                return self.identity_reply(user)
            if user not in cfg["qq"]["allowed_users"]:
                if text in ("开启", "绑定"):
                    if self.app.pairing.status()["active"]:
                        return self.app.pairing.request(user)
                    return self.identity_reply(user)
                if text in ("绑定", "我的ID", "帮助"):
                    return "尚未授权。发送“开启”获取你自己的 OpenID，再在本机“绑定 QQ 机器人”填写并保存。"
                return "尚未授权，不能读取或控制本机。请发送“开启”获取 OpenID，在本机填写并保存授权。"
            if text in ("帮助", "help"):
                return HELP
            if text=="接收" or text.startswith("接收 "):
                number=text[3:].strip() if text!="接收" else ""
                if number and not number.isdecimal():raise ValueError("请回复“接收”或“接收 1”")
                task=self.app.inbox.receive(user,int(number) if number else None)
                return "附件已保存到本机授权接收目录（未打开），任务 "+task["id"]
            if text == "状态":
                names = cfg["monitor"]["process_names"]
                return "监控：%s\n目标：%s\n当前匹配进程：%d\nQQ：%s" % ("运行" if cfg["monitor"]["enabled"] else "暂停", "、".join(names) or "未选择", len(self.app.monitor.last), "已连接" if self.app.gateway.connected else "未连接")
            if text in ("开启通知", "开启", "订阅"):
                self.app.store.subscribe(user, True)
                return "已订阅进程通知。\n你的 OpenID：" + user
            if text in ("停止通知", "停止", "退订"):
                self.app.store.subscribe(user, False)
                return "已退订，待发通知已取消。"
            if text == "测试通知":
                self.app.queue_test(user)
                return "测试通知已入队，发送结果请查看本地通知页。"
            if text == "历史":
                rows = self.app.store.history(8)
                return "\n".join(r["text"] for r in rows) or "暂无记录。"
            if text == "取消":
                self.pending.pop(user, None)
                return "已取消文件选择。"
            pending = self.pending.get(user)
            if pending and pending["expires"] <= time.monotonic():
                self.pending.pop(user, None)
                pending = None
            if text in CONFIRMATIONS or text.startswith("确认发送 "):
                code = text[5:].strip() if text.startswith("确认发送 ") else None
                if not pending or "file" not in pending or (code is not None and not secrets.compare_digest(code, pending["code"])):
                    return "确认无效或已过期。请先说要取哪个文件，例如“把桌面的操作手册发给我”；多个结果先选编号。"
                self.pending.pop(user, None)  # one use, including uncertain network outcomes
                task=self.app.send_file(user, pending["file"], message_id)
                return "文件已提交给 QQ 平台，任务 %s。请查看当前对话中的文件消息；平台接受不代表已经收到。可回复“任务 %s”查询，实际收到后回复“收到任务 %s”。"%(task["id"],task["id"],task["id"])
            number = selection_number(text)
            if number is not None and pending and "rows" in pending:
                index = number - 1
                if not 0 <= index < len(pending["rows"]):
                    return "编号无效，请重新选择。"
                return self.select_file(user, pending["rows"][index])
            if number == 1 and pending and pending.get("only_result"):
                return self.select_file(user,pending["file"],only_result=True)
            if number is not None:
                return "当前没有可选的文件列表，或选择已过期。请先说文件名，例如“找一下操作手册”。"
            try:
                request = file_request(text)
            except ValueError:
                self.pending.pop(user, None)
                raise
            if request:
                self.pending.pop(user, None)
                if request.desktop_only and not cfg["files"]["allow_desktop"]:
                    return "桌面尚未授权，请先在本机文件页开启桌面共享。"
                rows = self.app.files.search(request.query, request.desktop_only)
                if len(rows) == 1 and request.query:
                    return self.select_file(user, rows[0], only_result=True)
                self.pending[user] = {"rows": rows, "expires": time.monotonic() + 120}
                return ("找到这些文件（最多20项）：\n" + "\n".join("%d. %s（%s）[%s]" % (i, r["name"], self.file_size(r["size"]), Path(r["path"]).parent.name) for i, r in enumerate(rows, 1)) + "\n回复编号或“第一个”等选择，再回复“发送”。2分钟内有效。") if rows else "授权目录中没找到可发送的匹配文件。可以只说文件名的一部分；请检查目录授权、文件大小和搜索深度。"
            return "这句话我还没识别出具体操作。" + HELP

    def task_command(self,user,text):
        if text in ("收到了","文件收到了","已经收到"):
            scope=self.app.qq_scope(self.app.config.get()["qq"])
            import json
            rows=[r for r in self.app.tasks.recent(user,30) if r["kind"]=="qq.file.send" and r["state"]=="platform_accepted" and not r["received"] and json.loads(r["result"]).get("qq_scope")==scope]
            if len(rows)==1:
                self.app.tasks.confirm_received(rows[0]["id"],user);return "已记录你确认收到这个文件。"
            return "当前没有唯一待核对的文件。发送“任务”查看，再用“收到任务 编号”确认具体一项。"
        if text=="取消" and user not in self.pending:
            rows=[r for r in self.app.tasks.recent(user) if r["state"] in {"queued","running"} and r["kind"] in {"qq.file.send","qq.attachment.receive"}]
            if len(rows)==1:self.app.tasks.cancel(rows[0]["id"],user);return "已请求停止当前文件任务，等待正在执行的步骤安全结束。"
            if len(rows)>1:return "有多个文件任务，请发送“任务”，再用“取消任务 编号”选择。"
        if text in ("任务","我的任务"):
            rows=self.app.tasks.recent(user,5)
            return "\n".join(r["id"]+" "+STATE_NAMES.get(r["state"],r["state"]) for r in rows) or "暂无任务。"
        for prefix in ("任务 ","取消任务 ","收到任务 "):
            if text.startswith(prefix):
                ident=text[len(prefix):].strip()
                if prefix=="取消任务 ":self.app.tasks.cancel(ident,user);return "已请求取消，等待当前步骤安全结束。"
                if prefix=="收到任务 ":self.app.tasks.confirm_received(ident,user);return "已记录你确认收到该任务的文件或编号通知。"
                row=self.app.tasks.get(ident,user)
                return "%s：%s %s%%\n%s\n%s"%(row["id"],STATE_NAMES.get(row["state"],row["state"]),row["progress"],row["step"],row["error"] or ("用户已确认收到" if row["received"] else ""))
        return None

    @staticmethod
    def file_size(size):
        return "%.2f MB" % (size / 1024**2) if size >= 1024**2 else "%.1f KB" % (size / 1024)

    def select_file(self, user, row, only_result=False):
        self.pending.pop(user, None)
        fingerprint = self.app.files.fingerprint(row["path"])
        self.pending[user] = {"file":fingerprint, "code":secrets.token_hex(3), "expires":time.monotonic() + 120, "only_result":only_result}
        return self.file_preview(fingerprint)

    def file_preview(self, fingerprint):
        return "已选：%s\n大小：%s\n目录：%s\n回复“发送”或“确认发送”即可发到当前对话；回复“取消”放弃。2分钟内有效。" % (fingerprint["name"], self.file_size(fingerprint["size"]), Path(fingerprint["path"]).parent.name)

    @staticmethod
    def identity_reply(user):
        if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", user):
            return "无法识别发送人 OpenID，请检查机器人单聊权限。"
        return "你的 OpenID：\n%s\n\n推荐在电脑灵梭“设置 → QQ接入”开始绑定，再发送“开启”核对配对码。也可在高级手动配置粘贴这个OpenID保存。当前回复不会自动授予本机或文件访问权限。" % user
