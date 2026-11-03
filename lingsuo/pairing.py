"""Local-armed, expiring pairing. A QQ message never grants itself access."""
import secrets
import threading
import time


class Pairing:
    def __init__(self, config, store, clock=time.monotonic):
        self.config, self.store, self.clock = config, store, clock
        self.lock = threading.RLock()
        self.deadline = 0
        self.identity = None
        self.requests = {}

    def _identity(self):
        qq = self.config.get()["qq"]
        return (qq["appid"], qq["secret"], qq["enabled"], qq["is_sandbox"])

    def _active(self):
        return self.clock() < self.deadline and self.identity == self._identity()

    def arm(self):
        with self.lock:
            qq = self.config.get()["qq"]
            if not qq["enabled"] or not qq["appid"] or not qq["secret"]:
                raise ValueError("请先保存 QQ 应用信息并连接机器人")
            self.identity = self._identity()
            self.deadline = self.clock() + 120
            self.requests.clear()
            return self.status()

    def cancel(self):
        with self.lock:
            self.deadline = 0
            self.requests.clear()

    def request(self, user):
        with self.lock:
            if not self._active():
                return "尚未授权。请先在本机 QQ 接入页点击“开始绑定”，再发送“开启”。"
            if not user or len(user) > 200:
                return "无法识别发送人，请检查机器人单聊消息权限。"
            if user not in self.requests:
                if len(self.requests) >= 8:
                    return "本次绑定请求较多，请在本机取消后重新开始。"
                self.requests[user] = {"id": secrets.token_urlsafe(18), "code": secrets.token_hex(3).upper(), "user": user}
            code = self.requests[user]["code"]
            return "配对码：%s\n请在本机灵梭页面找到相同配对码，点击“这是我，确认绑定”。确认前没有读取文件或控制本机的权限；本次绑定2分钟内有效。" % code

    def status(self):
        with self.lock:
            active = self._active()
            return {"active": active, "seconds_left": max(0, int(self.deadline - self.clock())) if active else 0,
                    "requests": [dict(r) for r in self.requests.values()] if active else []}

    def confirm(self, request_id, code):
        with self.lock, self.config.lock:
            if not self._active():
                raise ValueError("配对已过期或 QQ 配置已改变，请重新开始绑定")
            row = next((r for r in self.requests.values() if secrets.compare_digest(r["id"], str(request_id))), None)
            if not row or not secrets.compare_digest(row["code"], str(code)):
                raise ValueError("配对码不匹配，请核对 QQ 消息，不要确认陌生请求")
            cfg = self.config.get()
            old = self.config.get()
            cfg["qq"]["allowed_users"] = sorted(set(cfg["qq"]["allowed_users"] + [row["user"]]))
            self.config.save(cfg)
            try:
                self.store.subscribe(row["user"], True)
            except Exception:
                self.config.save(old)
                raise
            self.cancel()  # no second claimant can use the same local pairing session
            self.store.event("qq.bound", "已在本机确认绑定接收人并开启通知")
            return {"ok": True, "user": row["user"], "message": "已绑定并订阅通知；请继续发送测试通知。"}
