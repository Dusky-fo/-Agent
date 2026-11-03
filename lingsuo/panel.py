import copy
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .paths import resource_dir
from .processes import list_processes
from .qq import QQError


def serve(app, port=None, collector=list_processes):
    token = secrets.token_urlsafe(32)
    pending, pending_lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never record the URL fragment, token, credentials, or filenames.

        def reply(self, data, code=200, html=False):
            body = data if html else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self, api=True):
            port = self.server.server_port
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            valid_host = host in ("127.0.0.1:%d" % port, "localhost:%d" % port)
            valid_origin = not origin or origin in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port)
            provided = self.headers.get("Authorization", "")
            if not valid_host or not valid_origin or api and not secrets.compare_digest(provided, "Bearer " + token):
                self.reply({"ok": False, "error": "本机面板认证失败，请从托盘重新打开"}, 403)
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path)
            if not self.authorized(api=path.path not in ("/", "/app.css", "/app.js")):
                return
            try:
                if path.path == "/":
                    self.reply((resource_dir() / "ui/index.html").read_bytes(), html=True)
                elif path.path == "/app.css":
                    body = (resource_dir() / "ui/app.css").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/css; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path.path == "/app.js":
                    body = (resource_dir() / "ui/app.js").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path.path == "/api/help":
                    self.reply(json.loads((resource_dir() / "resources/help.json").read_text(encoding="utf-8")))
                elif path.path == "/api/status":
                    self.reply(app.status())
                elif path.path == "/api/config":
                    self.reply(app.config.public())
                elif path.path == "/api/processes":
                    self.reply(collector())
                elif path.path == "/api/history":
                    self.reply(app.store.history())
                elif path.path == "/api/plugins":
                    self.reply(app.plugins.scan())
                elif path.path == "/api/files":
                    query = parse_qs(path.query)
                    self.reply(app.files.search(query.get("q", [""])[0][:200], query.get("desktop", ["0"])[0] == "1"))
                else:
                    self.reply({"error": "未找到页面"}, 404)
            except Exception:
                self.reply({"error": "读取失败，请检查本机配置或文件权限"}, 400)

        def do_POST(self):
            if not self.authorized():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536 or self.headers.get_content_type() != "application/json":
                    raise ValueError("请求格式或大小无效")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("请求须为对象")
                path = urlsplit(self.path).path
                if path == "/api/config":
                    old = app.config.get()
                    new = copy.deepcopy(data)
                    if set(new) - set(old):
                        raise ValueError("未知配置字段")
                    new["plugins"] = old["plugins"]  # trust is only set by the explicit plugin action
                    new["qq"].pop("has_secret", None)
                    if not new["qq"].get("secret"):
                        if new["qq"].get("appid") != old["qq"]["appid"]:
                            raise ValueError("更换 AppID 时请填写对应的新 AppSecret")
                        new["qq"]["secret"] = old["qq"]["secret"]
                    if data.get("clear_secret"):
                        raise ValueError("未知配置字段")
                    app.save_config(new)
                    self.reply({"ok": True, "message": "已保存。QQ 变更后点击连接即可；仅面板端口变更需重启程序。"})
                elif path == "/api/settings":
                    app.save_section(data.get("section"), data.get("values"))
                    self.reply({"ok": True, "message": "设置已保存。监控、共享、通知参数即时生效；面板端口需重启程序。"})
                elif path == "/api/qq/check":
                    self.reply(app.check_qq(str(data.get("appid", "")), str(data.get("secret", ""))))
                elif path == "/api/qq/connect":
                    self.reply(app.reconnect_qq())
                elif path == "/api/qq/disconnect":
                    self.reply(app.disconnect_qq())
                elif path == "/api/pairing/start":
                    if not app.gateway.connected:
                        raise ValueError("QQ 消息通道尚未连接，请先检查并连接 QQ")
                    self.reply(app.pairing.arm())
                elif path == "/api/pairing/cancel":
                    app.pairing.cancel()
                    self.reply({"ok": True, "message": "已取消本次绑定，待确认请求没有获得权限。"})
                elif path == "/api/pairing/confirm":
                    self.reply(app.confirm_pairing(data.get("id", ""), data.get("code", "")))
                elif path == "/api/qq/subscription":
                    user = str(data.get("user", ""))
                    if user not in app.config.get()["qq"]["allowed_users"] or type(data.get("enabled")) is not bool:
                        raise ValueError("接收人或订阅状态无效")
                    app.store.subscribe(user, data["enabled"])
                    self.reply({"ok": True, "message": "通知订阅已更新。"})
                elif path == "/api/qq/remove-user":
                    user = str(data.get("user", ""))
                    with app.settings_lock:
                        cfg = app.config.get()
                        cfg["qq"]["allowed_users"] = [u for u in cfg["qq"]["allowed_users"] if u != user]
                        app.save_config(cfg)
                        app.store.subscribe(user, False)
                    self.reply({"ok": True, "message": "已撤销该接收人的授权和通知订阅。"})
                elif path == "/api/notify/test":
                    test = app.queue_test(str(data.get("user", "")))
                    self.reply({"ok": True, "test": test, "message": "测试已入队，尚不代表手机收到。请等待平台接受后核对同编号通知。"})
                elif path == "/api/notify/confirm":
                    app.confirm_test(data.get("id", ""))
                    self.reply({"ok": True, "message": "已记录你对该编号通知的收到确认。"})
                elif path == "/api/files/prepare":
                    user = str(data.get("user", ""))
                    if user not in app.config.get()["qq"]["allowed_users"]:
                        raise PermissionError("接收人未授权")
                    fingerprint = app.files.fingerprint(data["path"])
                    key = secrets.token_urlsafe(24)
                    with pending_lock:
                        for oldkey in list(pending):
                            if pending[oldkey]["expires"] < time.monotonic():
                                pending.pop(oldkey)
                        if len(pending) >= 100:
                            raise ValueError("待确认任务过多，请稍后重试")
                        pending[key] = {"user": user, "file": fingerprint, "expires": time.monotonic() + 120}
                    self.reply({"confirmation": key, "name": fingerprint["name"], "size": fingerprint["size"], "user": user})
                elif path == "/api/files/send":
                    with pending_lock:
                        item = pending.pop(str(data.get("confirmation", "")), None)
                    if not item or item["expires"] < time.monotonic():
                        raise ValueError("确认已使用或过期，请重新选择")
                    app.send_file(item["user"], item["file"])
                    self.reply({"ok": True, "message": "文件消息已提交给 QQ 平台。"})
                elif path == "/api/plugins/configure":
                    if type(data.get("enabled")) is not bool:
                        raise ValueError("启用状态无效")
                    app.plugins.configure(data["id"], data["enabled"], data.get("trusted") is True)
                    self.reply({"ok": True, "message": "插件设置已保存。启用需重启；禁用立即阻止工具调用，插件代码完全卸载仍需重启。"})
                elif path == "/api/plugins/call":
                    self.reply({"ok": True, "result": app.plugins.call(data["id"], data["tool"], data.get("args", {}))})
                else:
                    self.reply({"error": "未找到接口"}, 404)
            except (ValueError, PermissionError, QQError) as exc:
                self.reply({"ok": False, "error": str(exc)}, 400)
            except Exception:
                self.reply({"ok": False, "error": "操作未完成。请检查 QQ 权限、网络及文件状态；文件发送不会自动重复提交。"}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", app.config.get()["panel"]["port"] if port is None else port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="panel").start()
    return server, "http://127.0.0.1:%d/#%s" % (server.server_port, token)
