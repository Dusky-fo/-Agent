"""Real workflows with temporary data and explicitly injected offline QQ doubles."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib import request, error

from test_core import Base
from lingsuo.runtime import Runtime
from lingsuo.panel import serve
from lingsuo.qq import QQError, QQGateway


class FakeGateway:
    def __init__(self, config, receive, event):
        self.config, self.receive, self.event = config, receive, event
        self.connected = False
        self.starts = 0
        self.stops = 0
        self.can_stop = True

    def start(self):
        self.starts += 1
        self.connected = True

    def stop(self):
        self.stops += 1
        if self.can_stop:
            self.connected = False
        return self.can_stop

    def status(self):
        return {"state": "connected" if self.connected else "disconnected", "connected": self.connected,
                "message": "offline test double"}


class GuidanceBase(Base):
    def setUp(self):
        super().setUp()
        self.gateway = FakeGateway(self.app.config, self.app.receive, self.app.store.event)
        self.app.gateway = self.gateway

    def start_panel(self):
        self.server, url = serve(self.app, port=0, collector=lambda: [{"name": "fixture.exe", "pid": 77}])
        self.base, self.token = url.split("/#")
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, data=None, auth=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if auth: headers["Authorization"] = "Bearer " + self.token
        if origin: headers["Origin"] = origin
        req = request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with request.urlopen(req, timeout=5) as response:
            return json.loads(response.read())

    def arm(self):
        self.config(qq=True)
        cfg = self.app.config.get(); cfg["qq"]["allowed_users"] = []; self.app.config.save(cfg)
        return self.app.pairing.arm()


class PairingTests(GuidanceBase):
    def test_first_open_message_never_auto_authorizes(self):
        self.arm()
        answer = self.app.commands.handle("alice", "开启", "m1")
        self.assertIn("配对码", answer)
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], [])
        self.assertEqual(self.app.store.subscribers(), [])
        self.assertIn("尚未授权", self.app.commands.handle("alice", "桌面文件", "m2"))

    def test_local_arm_required(self):
        self.config(qq=True)
        answer = self.app.commands.handle("stranger", "开启", "m")
        self.assertIn("本机", answer)
        self.assertFalse(self.app.pairing.status()["requests"])

    def test_requests_have_distinct_codes_and_local_confirmation_selects_owner(self):
        self.arm(); self.app.pairing.request("stranger"); self.app.pairing.request("alice")
        rows = self.app.pairing.status()["requests"]
        self.assertNotEqual(rows[0]["code"], rows[1]["code"])
        alice = rows[1]
        self.app.confirm_pairing(alice["id"], alice["code"])
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["alice"])
        self.assertEqual(self.app.store.subscribers(), ["alice"])
        self.assertFalse(self.app.pairing.status()["active"])
        self.assertIn("已订阅", self.app.commands.handle("alice", "开启", "m"))

    def test_wrong_code_and_replay_rejected(self):
        self.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        with self.assertRaises(ValueError): self.app.confirm_pairing(row["id"], "WRONG")
        self.app.confirm_pairing(row["id"], row["code"])
        with self.assertRaises(ValueError): self.app.confirm_pairing(row["id"], row["code"])

    def test_expiration_and_cancel_invalidate_requests(self):
        tick = [0]
        self.app.pairing.clock = lambda: tick[0]
        self.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        tick[0] = 121
        with self.assertRaises(ValueError): self.app.confirm_pairing(row["id"], row["code"])
        self.app.pairing.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        self.app.pairing.cancel()
        with self.assertRaises(ValueError): self.app.confirm_pairing(row["id"], row["code"])

    def test_changed_credentials_invalidate_requests(self):
        self.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        self.app.save_section("qq", {"secret": "new-fake-secret"})
        with self.assertRaises(ValueError): self.app.confirm_pairing(row["id"], row["code"])

    def test_requests_bounded_and_same_sender_reuses_challenge(self):
        self.arm(); first = self.app.pairing.request("u0")
        self.assertEqual(first, self.app.pairing.request("u0"))
        for i in range(1, 9): self.app.pairing.request("u" + str(i))
        self.assertEqual(len(self.app.pairing.status()["requests"]), 8)

    def test_pairing_does_not_grant_file_roots(self):
        self.arm(); cfg = self.app.config.get(); cfg["files"]["roots"] = []; self.app.config.save(cfg)
        self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        self.app.confirm_pairing(row["id"], row["code"])
        with self.assertRaises(PermissionError): self.app.files.fingerprint(self.sample())
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])

    def test_simultaneous_confirmations_only_one_succeeds(self):
        self.arm(); self.app.pairing.request("alice"); self.app.pairing.request("bob")
        rows = self.app.pairing.status()["requests"]
        def confirm(row):
            try: self.app.confirm_pairing(row["id"], row["code"]); return True
            except ValueError: return False
        with ThreadPoolExecutor(2) as pool: results = list(pool.map(confirm, rows))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.app.config.get()["qq"]["allowed_users"]), 1)

    def test_binding_persists_but_pending_session_does_not(self):
        self.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        self.app.confirm_pairing(row["id"], row["code"])
        other = Runtime(self.app.directory, collector=lambda: [], transport=self.qq, gateway_factory=FakeGateway)
        try:
            self.assertEqual(other.config.get()["qq"]["allowed_users"], ["alice"])
            self.assertEqual(other.store.subscribers(), ["alice"])
            self.assertFalse(other.pairing.status()["active"])
        finally: other.stop()


class ConnectionAndReceiptTests(GuidanceBase):
    def test_reconnect_does_not_restart_monitor(self):
        self.config(qq=True)
        sentinel = object(); self.app.monitor.thread = sentinel
        self.app.reconnect_qq()
        self.assertIs(self.app.monitor.thread, sentinel)
        self.assertEqual(self.gateway.starts, 1)
        self.app.monitor.thread = None

    def test_incomplete_stop_does_not_create_second_gateway(self):
        self.config(qq=True); self.gateway.can_stop = False
        with self.assertRaises(ValueError): self.app.reconnect_qq()
        self.assertEqual(self.gateway.starts, 0)
        self.gateway.can_stop = True

    def test_credential_check_uses_injected_http_and_does_not_save_or_connect(self):
        seen = []
        class Checker:
            def __init__(self, config): seen.append(config.get()["qq"])
            def check_credentials(self): return {"ok": True, "message": "credentials accepted"}
        self.app.transport_factory = Checker
        self.app.check_qq("fake-app", "fake-secret")
        self.assertEqual(seen[0]["appid"], "fake-app")
        self.assertFalse(self.app.config.get()["qq"]["enabled"])
        self.assertEqual(self.gateway.starts, 0)

    def test_invalid_save_has_no_gateway_or_queue_side_effect(self):
        self.config(qq=True); self.app.queue_test("alice")
        cfg = self.app.config.get(); cfg["qq"]["appid"] = "different"; cfg["monitor"]["interval"] = 0
        with self.assertRaises(ValueError): self.app.save_config(cfg)
        self.assertEqual(self.gateway.stops, 0)
        self.assertEqual(len(self.app.store.due()), 1)

    def test_new_bot_clears_old_binding_and_pending_notifications(self):
        self.config(qq=True); self.app.store.subscribe("alice", True); self.app.queue_test("alice")
        self.app.save_section("qq", {"appid": "another-fake-app", "secret": "another-fake-secret"})
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], [])
        self.assertEqual(self.app.store.subscribers(), [])
        self.assertEqual(self.app.store.due(), [])

    def test_new_bot_must_not_reuse_hidden_secret(self):
        self.config(qq=True)
        with self.assertRaises(ValueError): self.app.save_section("qq", {"appid": "different-app", "secret": ""})
        self.assertEqual(self.app.config.get()["qq"]["appid"], "fake-app")

    def test_monitor_section_preserves_newly_bound_user(self):
        self.arm(); self.app.pairing.request("alice"); row = self.app.pairing.status()["requests"][0]
        self.app.confirm_pairing(row["id"], row["code"])
        self.app.save_section("monitor", {"process_names": ["fixture.exe"]})
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["alice"])

    def test_receipt_pending_accepted_and_user_confirmed_are_distinct(self):
        self.config(qq=True); receipt = self.app.queue_test("alice")
        with self.assertRaises(ValueError): self.app.confirm_test(receipt["id"])
        row = self.app.status()["tests"][0]
        self.assertIsNone(row["accepted"]); self.assertIsNone(row["confirmed"])
        self.gateway.connected = True; self.app.flush_once()
        row = self.app.status()["tests"][0]
        self.assertTrue(row["accepted"]); self.assertIsNone(row["confirmed"])
        self.app.confirm_test(receipt["id"])
        self.assertTrue(self.app.status()["tests"][0]["confirmed"])

    def test_failed_notification_cannot_be_confirmed(self):
        cfg = self.config(qq=True); cfg["notifications"]["max_attempts"] = 1; self.app.config.save(cfg)
        receipt = self.app.queue_test("alice"); self.gateway.connected = True; self.qq.fail = True
        self.app.flush_once()
        self.assertEqual(self.app.status()["tests"][0]["state"], "failed")
        with self.assertRaises(ValueError): self.app.confirm_test(receipt["id"])

    def test_receipt_cannot_cross_bot_identity_or_revoked_user(self):
        self.config(qq=True); receipt = self.app.queue_test("alice"); self.gateway.connected = True; self.app.flush_once()
        cfg = self.app.config.get(); cfg["qq"]["allowed_users"] = []; self.app.config.save(cfg)
        with self.assertRaises(ValueError): self.app.confirm_test(receipt["id"])

    def test_home_status_uses_actual_configuration(self):
        initial = self.app.status()
        self.assertFalse(any(initial["todo"].values()))
        self.config(qq=True); self.gateway.connected = True
        status = self.app.status()
        self.assertTrue(status["todo"]["qq_connected"])
        self.assertTrue(status["todo"]["receiver_bound"])
        self.assertTrue(status["todo"]["files_authorized"])
        self.assertFalse(status["todo"]["monitor_added"])

    def test_real_sdk_cancellation_reconnects_without_duplicate_threads(self):
        import botpy
        self.config(qq=True)
        ready = threading.Event()
        gateway = QQGateway(self.app.config, lambda *a: None, lambda *a: None)
        async def fake_login(client, token): pass
        async def fake_init(client, token):
            await client.on_ready(); ready.set()
            await asyncio.Future()
        with patch.object(botpy.Client, "_bot_login", fake_login), patch.object(botpy.Client, "_bot_init", fake_init):
            try:
                gateway.start(); self.assertTrue(ready.wait(2))
                with self.assertRaises(ValueError): gateway.start()
                self.assertTrue(gateway.stop()); self.assertFalse(gateway.thread.is_alive())
                ready.clear(); gateway.start(); self.assertTrue(ready.wait(2))
                self.assertTrue(gateway.stop())
            finally: gateway.stop()


class GuidancePanelTests(GuidanceBase):
    def setUp(self):
        super().setUp(); self.start_panel()

    def test_static_scripts_and_styles_load_without_api_token(self):
        for path, mime in (("/app.js", "application/javascript"), ("/app.css", "text/css")):
            with request.urlopen(self.base + path) as response:
                self.assertIn(mime, response.headers["Content-Type"])
                self.assertGreater(len(response.read()), 100)

    def test_pairing_api_still_requires_local_authentication(self):
        with self.assertRaises(error.HTTPError) as ctx: self.call("/api/pairing/start", {}, auth=False)
        self.assertEqual(ctx.exception.code, 403)
        with self.assertRaises(error.HTTPError): self.call("/api/pairing/start", {}, origin="https://foreign.invalid")

    def test_pairing_disconnected_rejected_then_full_local_confirmation(self):
        self.arm()
        with self.assertRaises(error.HTTPError): self.call("/api/pairing/start", {})
        self.gateway.connected = True
        self.call("/api/pairing/start", {})
        self.app.receive("alice", "开启", "qq-start")
        row = self.call("/api/status")["pairing"]["requests"][0]
        self.call("/api/pairing/confirm", {"id": row["id"], "code": row["code"]})
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["alice"])

    def test_help_covers_implemented_workflows(self):
        articles = self.call("/api/help")
        self.assertEqual({a["id"] for a in articles}, {"first", "qq", "binding", "notifications", "monitor", "files", "plugins", "faq", "automation","watch","anomalies","inbound","tasks","manage","webtasks"})

    def test_check_failure_is_actionable_not_file_error_or_credential_echo(self):
        class Denied:
            def __init__(self, cfg): pass
            def check_credentials(self): raise QQError("应用凭据无效，请核对 AppID 与 AppSecret")
        self.app.transport_factory = Denied
        with self.assertRaises(error.HTTPError) as ctx: self.call("/api/qq/check", {"appid":"fake-app", "secret":"never-echo-this"})
        body = ctx.exception.read().decode()
        self.assertIn("AppID", body); self.assertNotIn("never-echo-this", body)

    def test_settings_cannot_silently_add_authorized_users(self):
        with self.assertRaises(error.HTTPError): self.call("/api/settings", {"section":"qq", "values":{"allowed_users":["stranger"]}})
        self.assertFalse(self.app.config.get()["qq"]["allowed_users"])
