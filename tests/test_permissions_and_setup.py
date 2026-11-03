"""Permissions, environments, UAC requests and local editing with isolated data only."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest
from test_guidance import GuidanceBase
from lingsuo.config import Config, DEFAULT
from lingsuo.qq import QQTransport, QQError
from lingsuo.processes import process_catalog
import bootstrap


class LocalDataTests(GuidanceBase):
    def authorize_private(self):
        self.config(qq=True)
        self.app.save_section("local_data", {"roots":[str(self.shared)]})

    def test_private_data_cannot_be_qq_searched_or_uploaded_even_under_shared_root(self):
        self.authorize_private(); sample = self.sample(data=b"private local notes")
        self.assertEqual(self.app.files.search(""), [])
        with self.assertRaises(PermissionError): self.app.files.fingerprint(sample)
        self.assertEqual(self.qq.files, [])
        self.assertEqual(self.app.documents.read(sample)["text"], "private local notes")

    def test_local_document_save_backup_and_stale_version_protection(self):
        self.authorize_private(); sample = self.sample(data=b"old text\r\n")
        expected = self.app.documents.read(sample)
        saved = self.app.documents.save(expected, "new text\r\n")
        self.assertEqual(sample.read_bytes(), b"new text\r\n")
        backups = list((self.app.directory / "document-backups").glob("*.bak"))
        self.assertEqual(backups[0].read_bytes(), b"old text\r\n")
        with self.assertRaises(ValueError): self.app.documents.save(expected, "stale overwrite")
        self.assertEqual(saved["text"], "new text\r\n")

    def test_private_editor_rejects_runtime_binary_external_and_oversized_files(self):
        self.authorize_private()
        for path in [self.app.config.path, self.root / "not-authorized.txt"]:
            if not path.exists(): path.write_bytes(b"outside")
            with self.assertRaises(PermissionError): self.app.documents.read(path)
        with self.assertRaises(ValueError): self.app.documents.read(self.sample("binary.txt", b"zero\x00byte"))
        with self.assertRaises(ValueError): self.app.documents.read(self.sample("large.txt", b"x" * (1024**2 + 1)))

    def test_only_explicit_excerpt_is_queued_not_the_file_or_path(self):
        self.authorize_private(); sample = self.sample(data=b"private whole document")
        self.assertEqual(self.app.store.due(), [])
        self.app.queue_local_text("alice", "approved excerpt")
        self.app.gateway.connected = True; self.app.flush_once()
        self.assertEqual(self.qq.texts[0][1], "approved excerpt")
        self.assertNotIn(str(sample), self.qq.texts[0][1]); self.assertEqual(self.qq.files, [])
        for user, text in [("stranger", "text"), ("alice", "x" * 2501)]:
            with self.assertRaises((ValueError, PermissionError)): self.app.queue_local_text(user, text)

    def test_private_authorization_revokes_an_already_prepared_file_send(self):
        self.config(qq=True); sample = self.sample()
        prepared = self.app.files.fingerprint(sample)
        self.app.save_section("local_data", {"roots":[str(self.shared)]})
        with self.assertRaises(PermissionError): self.app.send_file("alice", prepared)
        self.assertEqual(self.qq.files, [])


class OptionalGamePluginTests(GuidanceBase):
    def test_install_does_not_load_and_readonly_adapter_uses_injected_processes(self):
        source = Path(__file__).resolve().parents[1] / "optional_adapters/latale_readonly"
        ident = self.app.plugins.install(source)
        self.assertEqual(ident, "latale_readonly")
        self.assertFalse(self.app.plugins.scan()[0]["enabled"])
        with self.assertRaises(PermissionError): self.app.plugins.call(ident, ident + ".status", {})
        self.app.plugins.configure(ident, True, True); self.app.plugins.load_enabled()
        with patch("lingsuo.processes.list_processes", return_value=[{"name":"LaTaleClient_x64.exe","pid":123}, {"name":"other.exe","pid":7}]):
            result = self.app.plugins.call(ident, ident + ".status", {})
        self.assertEqual(result["instances"], 1)
        self.assertEqual(result["processes"][0]["pid"], 123)
        with self.assertRaises(ValueError): self.app.plugins.install(source)


class QQEnvironmentTests(GuidanceBase):
    def test_sandbox_http_and_credential_endpoint_and_business_errors(self):
        self.config(qq=True); self.app.save_section("qq", {"is_sandbox":True})
        calls = []
        def http(method, url, body, headers, timeout):
            calls.append(url)
            return {"access_token":"fixture", "expires_in":7200} if url.endswith("getAppAccessToken") else {"err_code":40034005, "message":"secret must never leak"}
        transport = QQTransport(self.app.config, http=http)
        with self.assertRaises(QQError) as error: transport.send_text("fixture-user", "sample")
        self.assertEqual(calls[0], "https://api.bot.qq.com/app/getAppAccessToken")
        self.assertTrue(calls[1].startswith("https://sandbox.api.sgroup.qq.com/"))
        self.assertIn("40034005", str(error.exception)); self.assertNotIn("secret must never leak", str(error.exception))

    def test_environment_switch_clears_binding_and_prevents_cross_environment_receipts(self):
        self.config(qq=True); self.app.queue_test("alice")
        self.app.save_section("qq", {"is_sandbox":True})
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], [])
        self.assertEqual(self.app.status()["tests"], [])
        self.assertEqual(self.app.store.queue_summary()["cancelled"], 1)

    def test_old_project_config_migrates_additive_fields_without_importing_original_data(self):
        old = copy.deepcopy(DEFAULT); del old["local_data"]; del old["qq"]["is_sandbox"]
        self.app.config.path.write_text(json.dumps(old), encoding="utf-8")
        config = Config(self.app.directory)
        self.assertFalse(config.get()["qq"]["is_sandbox"])
        self.assertEqual(config.get()["local_data"]["roots"], [])


class StartupPermissionTests(unittest.TestCase):
    def test_unprivileged_start_requests_uac_once_with_original_data_directory(self):
        with patch("bootstrap.is_admin", return_value=False), patch("bootstrap.elevation_request") as request, patch("bootstrap.runpy.run_path") as run:
            self.assertEqual(bootstrap.main([]), 0)
            args = request.call_args.args[1]
            self.assertIn("--elevation-attempted", args); self.assertIn("--data-dir", args)
            self.assertIn("-I", args); run.assert_not_called()

    def test_failed_elevation_never_silently_runs_or_repeats(self):
        with patch("bootstrap.is_admin", return_value=False), patch("bootstrap.elevation_request") as request, patch("bootstrap.runpy.run_path") as run:
            with self.assertRaises(PermissionError): bootstrap.main(["--elevation-attempted"])
            request.assert_not_called(); run.assert_not_called()


class ProcessCatalogTests(unittest.TestCase):
    def test_application_classification_and_memory_unknown_are_explicit(self):
        rows = process_catalog(lambda:[{"name":"app.exe","pid":1}, {"name":"bg.exe","pid":2}], lambda:{1}, lambda pid:1024 if pid == 1 else None)
        self.assertTrue(rows[0]["has_window"]); self.assertEqual(rows[0]["memory"], "1 K")
        self.assertFalse(rows[1]["has_window"]); self.assertIsNone(rows[1]["memory_bytes"])

    def test_window_classification_failure_does_not_invent_application_flags(self):
        rows = process_catalog(lambda:[{"name":"app.exe","pid":1}], lambda:None, lambda pid:None)
        self.assertIsNone(rows[0]["has_window"])
