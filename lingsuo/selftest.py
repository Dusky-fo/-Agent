"""Packaged smoke test: no UI, process enumeration, credentials, or external requests."""
import json
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch
from .runtime import Runtime
from .plugins import Registry
from .paths import resource_dir


def run(report_path=None):
    def denied(*args, **kwargs):
        raise AssertionError("Self-test must not connect to a network")

    class FakeQQ:
        def send_file(self, user, path, name, message_id=None):
            assert path.read_bytes() == b"sample-only"
            assert user == "test-user"

        def send_text(self, *args):
            pass

    with patch.object(socket.socket, "connect", denied), patch("os.system", return_value=0), tempfile.TemporaryDirectory(prefix="lingsuo-selftest-") as tmp:
        # Validate frozen imports without creating a client, tray icon, or connection.
        import botpy
        assert hasattr(botpy, "Client")
        from .app import tray_image
        assert tray_image().size == (64, 64)
        from .desktop import DesktopApp
        assert callable(DesktopApp)
        assert (resource_dir() / "resources/help.json").is_file()
        root = Path(tmp)
        shared = root / "shared"
        shared.mkdir()
        sample = shared / "sample.txt"
        sample.write_bytes(b"sample-only")
        events = []
        app = Runtime(root / "data", collector=lambda: [{"name": "sample.exe", "pid": 10}], transport=FakeQQ(), desktop=lambda: shared)
        cfg = app.config.get()
        cfg["monitor"]["process_names"] = ["sample.exe"]
        cfg["files"]["roots"] = [str(shared)]
        cfg["qq"].update(enabled=True, appid="TEST", secret="TEST", allowed_users=["test-user"])
        app.config.save(cfg)
        app.monitor.tick()
        app.monitor.collector = lambda: []
        app.monitor.tick()
        assert any(r["kind"] == "process.exit" for r in app.store.history())
        app.send_file("test-user", app.files.fingerprint(sample), "fake-message")
        app.store.subscribe("test-user", True)
        app.stop()
        restored = Runtime(root / "data", collector=lambda: [], transport=FakeQQ())
        assert restored.store.subscribers() == ["test-user"]
        restored.stop()
        registry = Registry()
        registry.register("example.ping", "sample", lambda: "pong")
        assert registry.call("example.ping", {}) == "pong"
    from . import __version__
    result = {"ok": True, "version": __version__, "checks": ["process-exit", "authorized-snapshot-send", "subscription-restart", "plugin-registry", "qq-sdk-import", "tray-image", "native-desktop-import", "help-resource"],
              "real_qq": False, "real_process_enumeration": False, "ui_started": False}
    print(json.dumps(result, ensure_ascii=True))
    if report_path:
        Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0
