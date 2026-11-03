import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from lingsuo.config import Config, DEFAULT, validate
from lingsuo.files import FileService
from lingsuo.monitor import ProcessMonitor
from lingsuo.processes import parse_tasklist
from lingsuo.runtime import Runtime
from lingsuo.store import Store
from lingsuo.plugins import Plugins, Registry
from lingsuo.qq import QQTransport, QQError
from lingsuo.qq import QQGateway
from lingsuo.panel import serve


class FakeQQ:
    def __init__(self):
        self.texts, self.files = [], []
        self.fail = False

    def send_text(self, user, text, message_id=None):
        if self.fail:
            raise QQError("simulated failure")
        self.texts.append((user, text, message_id))

    def send_file(self, user, path, filename, message_id=None):
        self.files.append((user, path.read_bytes(), filename, message_id))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shared = self.root / "shared"
        self.shared.mkdir()
        self.qq = FakeQQ()
        self.app = Runtime(self.root / "data", collector=lambda: [], transport=self.qq, desktop=lambda: self.shared)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.app.stop)
        # Every test rejects external socket connections. Panel tests may use loopback only.
        original = socket.socket.connect
        def guarded(sock, address):
            if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                return original(sock, address)
            raise AssertionError("external network is forbidden in isolated tests")
        self.guard = patch.object(socket.socket, "connect", guarded)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def config(self, qq=False):
        cfg = self.app.config.get()
        cfg["files"]["roots"] = [str(self.shared)]
        if qq:
            cfg["qq"].update(enabled=True, appid="fake-app", secret="fake-secret", allowed_users=["alice", "bob"])
        self.app.config.save(cfg)
        if qq:
            for user in ("alice","bob"):self.app.store.remember_reply(user,self.app.qq_scope(cfg["qq"]),"fixture-recent-"+user)
        return cfg

    def sample(self, name="sample.txt", data=b"example"):
        p = self.shared / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class ConfigurationTests(Base):
    def test_defaults_do_not_require_qq(self):
        self.app.start()
        self.assertFalse(self.app.gateway.connected)
        self.assertTrue(self.app.monitor.thread.is_alive())

    def test_config_persistence_and_secret_redaction(self):
        self.config(qq=True)
        self.assertEqual(Config(self.root / "data").get()["qq"]["secret"], "fake-secret")
        self.assertEqual(self.app.config.public()["qq"]["secret"], "")
        self.assertTrue(self.app.config.public()["qq"]["has_secret"])

    def test_invalid_save_does_not_destroy_valid_configuration(self):
        cfg = self.app.config.get()
        cfg["monitor"]["interval"] = True
        with self.assertRaises(ValueError):
            self.app.config.save(cfg)
        self.assertEqual(Config(self.root / "data").get()["monitor"]["interval"], 5)

    def test_qq_requires_credentials_only_when_enabled(self):
        cfg = self.app.config.get()
        cfg["qq"]["enabled"] = True
        with self.assertRaises(ValueError):
            validate(cfg)

    def test_network_root_and_process_path_rejected(self):
        cfg = self.app.config.get()
        cfg["files"]["roots"] = [r"\\server\share"]
        with self.assertRaises(ValueError):
            validate(cfg)
        cfg = self.app.config.get()
        cfg["monitor"]["process_names"] = [r"C:\app.exe"]
        with self.assertRaises(ValueError):
            validate(cfg)


class ProcessTests(Base):
    def test_csv_multiple_pids_and_unicode(self):
        rows = parse_tasklist('"文档.exe","42","Console","1","1,024 K"\n"文档.exe","43","Console","1","2,048 K"')
        self.assertEqual([r["pid"] for r in rows], [42, 43])
        self.assertEqual(rows[0]["memory_bytes"], 1048576)

    def test_start_exit_restore_and_same_name_pid_change(self):
        cfg = self.config()
        cfg["monitor"]["process_names"] = ["sample.exe"]
        self.app.config.save(cfg)
        events = []
        rows = []
        monitor = ProcessMonitor(self.app.config, lambda *e: events.append(e), collector=lambda: rows)
        monitor.tick()
        rows[:] = [{"name": "sample.exe", "pid": 1}, {"name": "unrelated.exe", "pid": 2}]
        monitor.tick()
        rows[:] = [{"name": "sample.exe", "pid": 3}]
        monitor.tick()
        self.assertEqual([e[0] for e in events], ["process.start", "process.exit", "process.restore"])

    def test_initial_snapshot_is_baseline(self):
        cfg = self.config(); cfg["monitor"]["process_names"] = ["sample.exe"]; self.app.config.save(cfg)
        self.app.monitor.collector = lambda: [{"name": "sample.exe", "pid": 1}]
        self.app.monitor.tick()
        self.assertEqual(self.app.store.history(), [])

    def test_collection_failure_does_not_report_false_exit(self):
        cfg = self.config(); cfg["monitor"]["process_names"] = ["sample.exe"]; self.app.config.save(cfg)
        self.app.monitor.collector = lambda: [{"name": "sample.exe", "pid": 1}]
        self.app.monitor.tick()
        self.app.monitor.collector = lambda: (_ for _ in ()).throw(OSError())
        self.app.monitor.tick()
        self.assertTrue(self.app.monitor.error)
        self.assertFalse(self.app.store.history())

    def test_pause_and_changed_selection_reset_baseline(self):
        cfg = self.config(); cfg["monitor"]["process_names"] = ["sample.exe"]; self.app.config.save(cfg)
        self.app.monitor.collector = lambda: [{"name": "sample.exe", "pid": 1}]
        self.app.monitor.tick()
        cfg["monitor"]["enabled"] = False; self.app.config.save(cfg)
        self.app.monitor.collector = lambda: []
        self.app.monitor.tick()
        self.assertFalse(self.app.store.history())


class FileTests(Base):
    def test_default_shares_nothing(self):
        p = self.sample()
        with self.assertRaises(PermissionError):
            self.app.files.fingerprint(p)

    def test_desktop_provider_and_authorization(self):
        self.sample()
        self.assertEqual(self.app.files.search(desktop_only=True), [])
        cfg = self.app.config.get(); cfg["files"]["allow_desktop"] = True; self.app.config.save(cfg)
        self.assertEqual(len(self.app.files.search(desktop_only=True)), 1)

    def test_traversal_and_program_data_rejected(self):
        self.config()
        outside = self.root / "private.txt"; outside.write_text("private")
        with self.assertRaises(PermissionError):
            self.app.files.fingerprint(self.shared / ".." / "private.txt")
        cfg = self.app.config.get(); cfg["files"]["roots"] = [str(self.root)]; self.app.config.save(cfg)
        with self.assertRaises(PermissionError):
            self.app.files.fingerprint(self.app.config.path)

    def test_authorized_snapshot_and_cleanup(self):
        self.config()
        p = self.sample()
        with self.app.files.snapshot(self.app.files.fingerprint(p)) as copy_path:
            self.assertEqual(copy_path.read_bytes(), b"example")
        self.assertFalse(copy_path.exists())

    def test_changed_file_invalidates_confirmation(self):
        self.config(); p = self.sample(); fp = self.app.files.fingerprint(p); p.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            with self.app.files.snapshot(fp):
                pass
        self.assertEqual(list((self.app.directory / "spool").iterdir()), [])

    def test_revoked_root_invalidates_confirmation(self):
        self.config(); p = self.sample(); fp = self.app.files.fingerprint(p)
        cfg = self.app.config.get(); cfg["files"]["roots"] = []; self.app.config.save(cfg)
        with self.assertRaises(PermissionError):
            with self.app.files.snapshot(fp):
                pass

    def test_search_depth_limit_and_result_cap(self):
        self.config()
        for n in range(25): self.sample("file%02d.txt" % n)
        self.assertEqual(len(self.app.files.search()), 20)
        self.sample("a/b/deep.txt")
        cfg = self.app.config.get(); cfg["files"]["max_depth"] = 0; self.app.config.save(cfg)
        self.assertEqual(self.app.files.search("deep"), [])

    def test_file_size_limit(self):
        self.config(); cfg = self.app.config.get(); cfg["files"]["max_mb"] = 1; self.app.config.save(cfg)
        p = self.sample(data=b"x" * (1024**2 + 1))
        with self.assertRaises(ValueError): self.app.files.fingerprint(p)

    def test_symlink_escape(self):
        self.config(); outside = self.root / "private.txt"; outside.write_text("private")
        link = self.shared / "escape.txt"
        try: link.symlink_to(outside)
        except OSError: self.skipTest("Windows symbolic-link privilege unavailable")
        with self.assertRaises(PermissionError): self.app.files.fingerprint(link)

    @unittest.skipUnless(os.name == "nt", "Windows junction verification")
    def test_directory_junction_cannot_escape_shared_root(self):
        self.config()
        outside = self.root / "outside"; outside.mkdir(); (outside / "private.txt").write_text("private")
        link = self.shared / "junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        try:
            with self.assertRaises(PermissionError): self.app.files.fingerprint(link / "private.txt")
            self.assertEqual(self.app.files.search("private"), [])
        finally:
            link.rmdir()


class CommandTests(Base):
    def test_unauthorized_user_cannot_list_files(self):
        self.config(qq=True); self.sample()
        answer = self.app.commands.handle("stranger", "搜索文件 sample", "msg")
        self.assertIn("尚未授权", answer)
        self.assertNotIn("sample", answer)

    def test_file_selection_confirmation_bound_to_user(self):
        self.config(qq=True); self.sample()
        commands = self.app.commands
        commands.handle("alice", "搜索文件 sample", "m1")
        answer = commands.handle("alice", "1", "m2")
        code = commands.pending["alice"]["code"]
        self.assertIn("确认发送", answer)
        self.assertIn("确认无效", commands.handle("bob", "确认发送 " + code, "m3"))
        commands.handle("alice", "确认发送 " + code, "m4")
        commands.handle("alice", "确认发送 " + code, "m5")
        self.assertEqual(len(self.qq.files), 1)
        self.assertEqual(self.qq.files[0][0], "alice")

    def test_confirmation_expiration(self):
        self.config(qq=True); self.sample()
        commands = self.app.commands
        commands.handle("alice", "搜索文件 sample", "m1"); commands.handle("alice", "1", "m2")
        code = commands.pending["alice"]["code"]
        commands.pending["alice"]["expires"] = 0
        self.assertIn("过期", commands.handle("alice", "确认发送 " + code, "m3"))
        self.assertFalse(self.qq.files)

    def test_message_deduplication_persists(self):
        self.assertTrue(self.app.store.claim("message")); self.assertFalse(self.app.store.claim("message"))
        other = Store(self.app.directory / "state.sqlite3")
        try: self.assertFalse(other.claim("message"))
        finally: other.close()

    def test_unsubscribe_cancels_pending_notifications(self):
        self.config(qq=True)
        self.app.store.subscribe("alice", True)
        self.app.emit("test", "sample", "key")
        self.app.commands.handle("alice", "停止通知", "m")
        self.assertEqual(self.app.store.due(), [])

    def test_notification_merge_and_retry_exhaustion(self):
        cfg = self.config(qq=True); cfg["notifications"]["max_attempts"] = 2; self.app.config.save(cfg)
        self.app.store.subscribe("alice", True)
        self.app.emit("test", "sample", "same"); self.app.emit("test", "sample", "same")
        self.assertEqual(len(self.app.store.due()), 1)
        self.qq.fail = True; self.app.gateway.connected = True
        self.app.flush_once()
        with self.app.store.lock, self.app.store.db:
            self.app.store.db.execute("UPDATE outbox SET due=0")
        self.app.flush_once()
        self.assertEqual(self.app.store.queue_summary()["failed"], 1)

    def test_disconnected_queue_restores_after_restart(self):
        self.config(qq=True); self.app.queue_test("alice"); self.app.flush_once()
        self.assertFalse(self.qq.texts)
        other = Store(self.app.directory / "state.sqlite3")
        try: self.assertEqual(len(other.due()), 1)
        finally: other.close()


class PluginTests(Base):
    def install(self, ident="example", source=None):
        source_root = Path(__file__).resolve().parents[1] / "sample_plugins/example"
        folder = self.app.plugins.directory / ident
        shutil.copytree(source_root, folder)
        if ident != "example":
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8")); manifest["id"] = ident
            manifest["tools"] = [ident + ".ping"]
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if source: (folder / "plugin.py").write_text(source, encoding="utf-8")
        return folder

    def test_disabled_plugin_is_not_imported(self):
        self.install(source="raise RuntimeError('must not import')")
        self.app.plugins.load_enabled()
        self.assertFalse(self.app.plugins.errors)

    def test_explicit_trust_required(self):
        self.install()
        with self.assertRaises(ValueError): self.app.plugins.configure("example", True)

    def test_trusted_example_load_and_disable(self):
        self.install(); self.app.plugins.configure("example", True, True)
        self.app.plugins.load_enabled()
        self.assertTrue(self.app.plugins.call("example", "example.ping", {})["ok"])
        self.app.plugins.configure("example", False)
        with self.assertRaises(PermissionError): self.app.plugins.call("example", "example.ping", {})

    def test_changed_plugin_requires_retrust(self):
        folder = self.install(); self.app.plugins.configure("example", True, True)
        self.app.plugins.load_enabled(); (folder / "plugin.py").write_text("def register(r): pass", encoding="utf-8")
        with self.assertRaises(PermissionError): self.app.plugins.call("example", "example.ping", {})

    def test_broken_plugin_does_not_block_other_plugins(self):
        self.install("broken", "raise ImportError('dependency missing')"); self.install()
        self.app.plugins.configure("broken", True, True); self.app.plugins.configure("example", True, True)
        self.app.plugins.load_enabled()
        self.assertIn("broken", self.app.plugins.errors)
        self.assertIn("example", self.app.plugins.loaded)

    def test_parameter_types_and_unknown_keys(self):
        registry = Registry(); registry.register("sample.value", "sample", lambda count: count, {"count": {"type": "integer", "required": True}})
        with self.assertRaises(ValueError): registry.call("sample.value", {"count": True})
        with self.assertRaises(ValueError): registry.call("sample.value", {"count": 2, "extra": 3})


class QQUploadTests(Base):
    def fake_http(self, method, url, body, headers, timeout):
        self.calls.append((method, url, body, headers))
        if "getAppAccessToken" in url: return {"access_token": "test-token", "expires_in": 7200}
        if url.endswith("upload_prepare"):
            return {"upload_id": "test-upload", "block_size": 4, "parts": [{"index": 1, "presigned_url": "https://upload.example/part1"}, {"index": 2, "presigned_url": "https://upload.example/part2"}]}
        if url.endswith("/files"): return {"file_info": "test-file-info"}
        if url.endswith("/messages"):return {"id":"fixture-receipt"}
        return {}

    def test_chunk_upload_hashes_and_media_message(self):
        self.config(qq=True); self.calls = []
        p = self.sample(data=b"abcdefgh")
        transport = QQTransport(self.app.config, http=self.fake_http, sleep=lambda _: None)
        transport.send_file("alice", p, "sample.txt", "msg")
        puts = [c for c in self.calls if c[0] == "PUT"]
        self.assertEqual([c[2] for c in puts], [b"abcd", b"efgh"])
        self.assertTrue(all("Authorization" not in c[3] for c in puts))
        prep = next(json.loads(c[2]) for c in self.calls if c[1].endswith("upload_prepare"))
        self.assertEqual(prep["md5"], hashlib.md5(b"abcdefgh").hexdigest())
        payload = json.loads(self.calls[-1][2])
        self.assertEqual(payload["media"]["file_info"], "test-file-info")
        self.assertEqual(payload["msg_id"], "msg")

    def test_failed_chunk_retries_bounded_and_does_not_send_message(self):
        self.config(qq=True); self.calls = []
        def http(*args):
            if args[0] == "PUT":
                self.calls.append(args); raise QQError("timeout")
            return self.fake_http(*args)
        transport = QQTransport(self.app.config, http=http, sleep=lambda _: None)
        with self.assertRaises(QQError): transport.send_file("alice", self.sample(data=b"abcdefgh"), "sample.txt")
        self.assertEqual(len([c for c in self.calls if c[0] == "PUT"]), 3)
        self.assertFalse(any(c[1].endswith("messages") for c in self.calls))

    def test_invalid_part_coverage_does_not_send_message(self):
        self.config(qq=True); self.calls = []
        transport = QQTransport(self.app.config, http=self.fake_http)
        with self.assertRaises(QQError): transport.send_file("alice", self.sample(data=b"abcdefghijk"), "sample.txt")
        self.assertFalse(any(c[1].endswith("messages") for c in self.calls))

    def test_disabled_qq_performs_no_http(self):
        transport = QQTransport(self.app.config, http=lambda *a: self.fail("unexpected network"))
        with self.assertRaises(QQError): transport.send_text("alice", "test")

    def test_real_sdk_gateway_contract_without_login(self):
        import botpy
        self.config(qq=True)
        received = []
        gateway = QQGateway(self.app.config, lambda *args: received.append(args), self.app.store.event)
        async def fake_login(client, token):
            pass
        async def fake_init(client, token):
            await client.on_ready()
            message = types.SimpleNamespace(author=types.SimpleNamespace(user_openid="alice"), content="状态", id="fake-gateway-message")
            await client.on_c2c_message_create(message)
            gateway.stop_event.set()
        with patch.object(botpy.Client, "_bot_login", fake_login), patch.object(botpy.Client, "_bot_init", fake_init):
            gateway.run()
        self.assertEqual(received, [("alice", "状态", "fake-gateway-message")])


class PanelTests(Base):
    def setUp(self):
        super().setUp()
        self.server, url = serve(self.app, port=0, collector=lambda: [{"name": "mock.exe", "pid": 7}])
        self.base, self.token = url.split("/#")
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, data=None, token=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if token: headers["Authorization"] = "Bearer " + self.token
        if origin: headers["Origin"] = origin
        req = request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return json.loads(request.urlopen(req, timeout=5).read())

    def test_unauthenticated_api_rejected(self):
        with self.assertRaises(error.HTTPError) as ctx: self.call("/api/status", token=False)
        self.assertEqual(ctx.exception.code, 403)

    def test_foreign_origin_rejected(self):
        with self.assertRaises(error.HTTPError) as ctx: self.call("/api/status", origin="https://foreign.invalid")
        self.assertEqual(ctx.exception.code, 403)

    def test_config_secret_is_not_returned(self):
        self.config(qq=True)
        self.assertNotIn("fake-secret", json.dumps(self.call("/api/config")))

    def test_panel_uses_injected_process_source(self):
        self.assertEqual(self.call("/api/processes")[0]["pid"], 7)

    def test_file_preview_and_single_use_confirmation(self):
        self.config(qq=True); p = self.sample()
        preview = self.call("/api/files/prepare", {"path": str(p), "user": "alice"})
        self.assertFalse(self.qq.files)
        self.call("/api/files/send", {"confirmation": preview["confirmation"]})
        with self.assertRaises(error.HTTPError): self.call("/api/files/send", {"confirmation": preview["confirmation"]})
        self.assertEqual(len(self.qq.files), 1)

    def test_generic_settings_cannot_bypass_plugin_trust(self):
        cfg = self.app.config.get(); cfg["plugins"]["enabled"] = ["evil"]
        self.call("/api/config", cfg)
        self.assertEqual(self.app.config.get()["plugins"]["enabled"], [])


class FileSafeguardTests(Base):
    def test_executable_suffix_is_refused_and_recorded(self):
        self.config();
        probe=self.shared/"tool.exe"
        probe.write_bytes(b"MZ")
        with self.assertRaises(PermissionError):self.app.files.fingerprint(probe)
        self.assertTrue(any("tool.exe" in row["text"] for row in self.app.store.history(10)))

    def test_allowed_suffix_still_sends(self):
        self.config(qq=True)
        sample=self.shared/"note.txt"
        sample.write_text("hello",encoding="utf-8")
        self.app.send_file("alice",self.app.files.fingerprint(sample))
        self.assertEqual(len(self.qq.files),1)

    def test_test_file_is_written_into_authorized_folder_and_sent(self):
        self.config(qq=True)
        task=self.app.send_test_file("alice")
        self.assertEqual(task["kind"],"qq.file.send")
        self.assertTrue(any(path.name.startswith("灵梭测试") for path in self.shared.iterdir()))
        self.assertEqual(len(self.qq.files),1)

    def test_test_file_requires_an_authorized_folder(self):
        self.config(qq=True)
        cfg=self.app.config.get();cfg["files"]["roots"]=[];self.app.config.save(cfg)
        with self.assertRaises(ValueError):self.app.send_test_file("alice")


if __name__ == "__main__":
    unittest.main()
