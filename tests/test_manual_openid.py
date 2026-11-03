"""Local OpenID authorization; no live QQ or external file operations."""
from unittest.mock import patch
from test_guidance import GuidanceBase


class ManualOpenIDTests(GuidanceBase):
    def test_open_reply_exposes_only_own_id_without_authorizing(self):
        self.config(qq=True)
        reply = self.app.commands.handle("stranger", "开启", "message")
        self.assertIn("stranger", reply)
        self.assertNotIn("alice", reply)
        self.assertNotIn("stranger", self.app.config.get()["qq"]["allowed_users"])
        self.assertNotIn("stranger", self.app.store.subscribers())
        self.assertFalse(self.app.pairing.status()["requests"])
        self.assertIn("尚未授权", self.app.commands.handle("stranger", "搜索文件 secret", "message2"))

    def test_parse_deduplicates_and_rejects_personal_numbers_or_paths(self):
        self.assertEqual(self.app.parse_openids("owner,owner，second\nthird-ID"), ["owner", "second", "third-ID"])
        for value in ("123456789", "../secret", "C:\\Users\\file", "a,b" * 40000):
            with self.assertRaises(ValueError): self.app.parse_openids(value)

    def test_local_save_subscribes_new_keeps_existing_unsubscribed_and_revokes_removed(self):
        self.config(qq=True)
        self.app.store.subscribe("alice", False)
        self.app.store.subscribe("bob", True)
        self.app.save_manual_users(["alice", "owner"])
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["alice", "owner"])
        self.assertEqual(self.app.store.subscribers(), ["owner"])
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])
        self.assertEqual(self.gateway.starts, 0)

    def test_missing_credentials_and_subscription_failure_do_not_grant_identity(self):
        with self.assertRaises(ValueError): self.app.save_manual_users(["owner"])
        self.config(qq=True)
        with patch.object(self.app.store, "subscribe", side_effect=OSError("fixture DB error")):
            with self.assertRaises(OSError): self.app.save_manual_users(["owner"])
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["alice", "bob"])
