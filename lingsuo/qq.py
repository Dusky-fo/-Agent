"""Official QQ API, isolated from local monitoring. No connection at import time."""
import asyncio
import hashlib
import json
import threading
import time
from urllib import error, parse, request


class QQError(RuntimeError):
    user_safe = True

class QQUncertain(QQError):
    outcome_uncertain = True


def connection_error(exc):
    """Map safe numeric metadata only; never echo SDK exception bodies or URLs."""
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status in (400, 401):
        return "QQ 应用鉴权失败，请核对 AppID 与 AppSecret，不要填写个人 QQ 密码"
    if status == 403:
        return "QQ 平台拒绝访问，请检查机器人权限、开发设置和可用范围"
    if status == 429:
        return "QQ 请求过于频繁或额度受限，请稍后重试并检查平台配额"
    if isinstance(status, int) and status >= 500:
        return "QQ 平台暂时不可用，请稍后重试"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "QQ 连接超时，请检查网络、代理或防火墙后重试"
    return "QQ 连接失败，请检查网络、应用凭据、机器人权限及开发环境设置"


class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise QQError("QQ 接口意外重定向，已停止请求")


class QQTransport:
    def __init__(self, config, http=None, sleep=time.sleep):
        self.config, self.http, self.sleep = config, http or self._http, sleep
        self.token, self.expires, self.identity = "", 0, None
        self.lock = threading.RLock()

    @staticmethod
    def _http(method, url, body, headers, timeout):
        try:
            with request.build_opener(SafeRedirect()).open(request.Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
                raw = response.read(2 * 1024**2)
                if method == "PUT":
                    return {}
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            # Do not expose response text, bearer credentials, or presigned URLs.
            if method=="POST" and url.endswith("/messages") and (exc.code>=500 or exc.code==408):raise QQUncertain("消息提交返回临时服务错误，结果待核对；不会自动重发") from None
            code=None
            try:
                data=json.loads(exc.read(65536));code=next((data[k] for k in ("err_code","code","biz_code") if isinstance(data.get(k),int) and data[k]),None)
            except (ValueError,TypeError,OSError,AttributeError):pass
            failure=QQError(connection_error(type("HTTPStatus", (), {"status": exc.code})()) + "（HTTP %s%s）" % (exc.code,"，平台代码 %s"%code if code else ""))
            failure.code=code;raise failure from None
        except (error.URLError, TimeoutError, OSError):
            if method == "POST" and url.endswith("/messages"):
                raise QQUncertain("文件或消息提交结果不确定，请在 QQ 核对；程序不会自动重发") from None
            raise QQError("QQ 网络请求失败或超时") from None
        except (ValueError, TypeError):
            if method=="POST" and url.endswith("/messages"):raise QQUncertain("消息响应无法识别，请在 QQ 核对；不会自动重发") from None
            raise QQError("QQ 返回内容无效") from None

    def _token(self):
        cfg = self.config.get()["qq"]
        if not cfg["enabled"] or not cfg["appid"] or not cfg["secret"]:
            raise QQError("QQ 未启用或缺少凭据")
        identity = (cfg["appid"], cfg["secret"])
        with self.lock:
            if identity != self.identity or time.time() >= self.expires:
                payload = json.dumps({"appId": cfg["appid"], "clientSecret": cfg["secret"]}).encode()
                data = self.http("POST", "https://api.bot.qq.com/app/getAppAccessToken", payload, {"Content-Type": "application/json"}, 20)
                if not isinstance(data,dict):raise QQError("QQ 访问凭据响应无效")
                if data.get("code") not in (None, 0):
                    code = data["code"]
                    reason = {100007: "AppID 无效或机器人状态不正常", 100016: "AppID 或 AppSecret 不正确", 100001: "请求频繁，请稍后再试"}.get(code, "请检查应用凭据和平台权限")
                    raise QQError("QQ 凭据检查失败（代码 %s）：%s" % (code if isinstance(code,int) else "未知", reason))
                self.token = data.get("access_token", "")
                if not self.token:
                    raise QQError("QQ 未返回访问凭证，请检查应用配置")
                self.expires = time.time() + max(1, int(data.get("expires_in", 7200)) - 60)
                self.identity = identity
            return self.token

    def check_credentials(self):
        self._token()
        return {"ok": True, "message": "应用凭据检查通过；这不代表 QQ 消息通道已连接，请继续连接 QQ。"}

    def api(self, user, suffix, payload):
        headers = {"Authorization": "QQBot " + self._token(), "Content-Type": "application/json", "X-Union-Appid": self.config.get()["qq"]["appid"]}
        base = "https://sandbox.api.sgroup.qq.com" if self.config.get()["qq"]["is_sandbox"] else "https://api.bot.qq.com"
        url = base + "/v2/users/%s/%s" % (parse.quote(user, safe=""), suffix)
        data = self.http("POST", url, json.dumps(payload).encode(), headers, 25)
        if not isinstance(data,dict):
            if suffix=="messages":raise QQUncertain("消息提交响应结构无效，请在 QQ 核对；不会自动重发")
            raise QQError("QQ 接口响应结构无效")
        code = next((data[k] for k in ("err_code","code","biz_code") if data.get(k) not in (None,0)),0)
        if code not in (None, 0):
            exc=QQError("QQ 平台拒绝请求（代码 %s），请检查环境、接口权限、消息有效期和配额" % (code if isinstance(code,int) else "未知"))
            exc.code=code;raise exc
        return data

    def send_text(self, user, text, message_id=None, sequence=2):
        payload = {"msg_type": 0, "content": text[:3500]}
        if message_id:
            payload.update(msg_id=message_id, msg_seq=sequence)
        receipt=self.api(user, "messages", payload)
        if not receipt.get("id"):raise QQUncertain("平台未返回消息标识，请在 QQ 核对；不会自动重发")
        return receipt

    def send_file(self, user, snapshot, filename, message_id=None, context=None, sequence=1):
        if context:context.progress(5,"核对并计算文件摘要")
        size = snapshot.stat().st_size
        md5, sha1, prefix = hashlib.md5(), hashlib.sha1(), hashlib.md5()
        seen = 0
        with snapshot.open("rb") as f:
            while data := f.read(1024**2):
                if context:context.check()
                md5.update(data); sha1.update(data)
                if seen < 10002432:
                    prefix.update(data[:10002432 - seen])
                seen += len(data)
        prep = self.api(user, "upload_prepare", {"file_type": 4, "file_size": size, "file_name": filename,
                                                "md5": md5.hexdigest(), "sha1": sha1.hexdigest(), "md5_10m": prefix.hexdigest()})
        upload = prep.get("upload_id")
        parts = prep.get("parts", [])
        if not upload or not isinstance(parts, list) or not parts:
            raise QQError("QQ 未返回分片信息；文件接口可能尚未开放给该机器人")
        try:parts = sorted(parts, key=lambda p: int(p["index"]))
        except (KeyError,ValueError,TypeError):raise QQError("QQ 返回的分片结构无效") from None
        if len(parts)>10000 or [int(p["index"]) for p in parts] != list(range(1,len(parts)+1)):
            raise QQError("QQ 返回的分片序号无效")
        with snapshot.open("rb") as f:
            for part in parts:
                try:block_size = int(part.get("block_size") or prep.get("block_size") or 0)
                except (ValueError,TypeError):raise QQError("QQ 分片大小无效") from None
                if not 0 < block_size <= 16 * 1024**2:
                    raise QQError("QQ 分片大小超出本版支持范围")
                data = f.read(block_size)
                if not data:
                    raise QQError("QQ 分片数量与文件不符")
                url = part.get("presigned_url","")
                parsed = parse.urlsplit(url)
                # Signed object URLs never receive the QQ bearer token.
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise QQError("QQ 上传地址无效")
                for attempt in range(3):
                    if context:context.check()
                    try:
                        self.http("PUT", url, data, {"Content-Type": "application/octet-stream"}, 25)
                        break
                    except (QQError, OSError):
                        if attempt == 2:
                            raise QQError("文件分片上传失败，已达到重试上限") from None
                        self.sleep(attempt + 1)
                for attempt in range(3):
                    if context:context.check()
                    try:
                        self.api(user,"upload_part_finish",{"upload_id":upload,"part_index":int(part["index"]),"block_size":len(data),"md5":hashlib.md5(data).hexdigest()});break
                    except QQError as exc:
                        if getattr(exc,"code",None)!=40093001 or attempt==2:raise
                        self.sleep(attempt+1)
                if context:context.progress(10+int(70*int(part["index"])/len(parts)),"已上传分片 %s/%s"%(part["index"],len(parts)))
            if f.read(1):
                raise QQError("QQ 分片未覆盖完整文件")
        if context:context.progress(85,"合并上传文件")
        merged = self.api(user, "files", {"upload_id":upload})
        if not merged.get("file_info"):
            raise QQError("QQ 未返回文件信息，未发送文件消息")
        payload = {"msg_type": 7, "content":" ", "media": {"file_info": merged["file_info"]}}
        if message_id:
            payload.update(msg_id=message_id, msg_seq=sequence)
        # Message submission is not retried automatically: response loss can mean it was delivered.
        if context:context.progress(95,"提交文件消息（不会自动重试）")
        receipt=self.api(user, "messages", payload)
        if not receipt.get("id"):raise QQUncertain("平台未返回文件消息标识，结果不确定，请在 QQ 核对；不会自动重发")
        return {"message_id":str(receipt["id"]),"state":"platform_accepted"}


class QQGateway:
    def __init__(self, config, receive, event):
        self.config, self.receive, self.event = config, receive, event
        self.stop_event = threading.Event()
        self.connected = False
        self.client, self.loop, self.thread, self.task = None, None, None, None
        self.lock = threading.RLock()
        self.state, self.message = "disconnected", "QQ 尚未连接"

    def status(self):
        with self.lock:
            return {"state": self.state, "message": self.message, "connected": self.connected}

    def _state(self, state, message):
        with self.lock:
            self.state, self.message = state, message

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("QQ 连接仍在运行或停止中，请等待后重试")
            if not self.config.get()["qq"]["enabled"]:
                self._state("disabled", "QQ 已禁用；本地监控仍可使用")
                return
            self.stop_event.clear()
            self._state("connecting", "正在连接 QQ，请等待平台返回结果")
            self.thread = threading.Thread(target=self.run, daemon=True, name="qq-gateway")
            self.thread.start()

    def run(self):
        import logging
        logging.getLogger("botpy").disabled = True
        try:
            import botpy
        except ImportError:
            self._state("error", "缺少 QQ 组件，请按帮助安装源码依赖；本地监控继续运行")
            self.event("qq.error", self.message)
            return
        owner = self

        class Client(botpy.Client):
            async def on_ready(self):
                if owner.stop_event.is_set():
                    return
                owner.connected = True
                owner._state("connected", "QQ 消息通道已连接，可以开始绑定接收人")
                owner.event("qq.connected", "QQ 已连接")

            async def on_error(self, event_method, *args, **kwargs):
                owner.event("qq.error", "QQ 消息处理失败，请查看本机帮助；未记录消息或凭据内容")

            async def on_c2c_message_create(self, message):
                try:
                    args=(message.author.user_openid,(message.content or "").strip(),str(message.id))
                    attachments=getattr(message,"attachments",None)
                    await asyncio.to_thread(owner.receive,*args,*([attachments] if attachments else []))
                except Exception:
                    owner.event("qq.error", "QQ 指令处理失败，详情已保留为本地事件")

        async def session(client, cfg):
            async with client:
                await client.start(appid=cfg["appid"], secret=cfg["secret"])

        while not self.stop_event.is_set():
            try:
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                cfg = self.config.get()["qq"]
                self.client = Client(intents=botpy.Intents(public_messages=True), is_sandbox=cfg["is_sandbox"], log_level=50, bot_log=False, ext_handlers=False)
                if not cfg["enabled"] or self.stop_event.is_set():
                    break
                self.task = self.loop.create_task(session(self.client, cfg))
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._state("retrying", connection_error(exc) + "；10秒后重试，本地监控继续运行")
                self.event("qq.error", self.message)
            finally:
                self.connected = False
                if self.loop and not self.loop.is_running():
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        self.loop.run_until_complete(asyncio.wait(pending, timeout=3))
                    self.loop.close()
                self.task = None
            if not self.stop_event.is_set() and self.state == "connected":
                self._state("retrying", "QQ 连接已断开，10秒后重试")
            self.stop_event.wait(10)
        self.connected = False
        self._state("disconnected", "QQ 已断开；本地监控继续运行")

    def stop(self):
        self.stop_event.set()
        self.connected = False
        self._state("stopping", "正在停止 QQ 通道，本地监控继续运行")
        if self.task and self.loop and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():
                return False
        self._state("disconnected", "QQ 已断开；本地监控继续运行")
        return True
