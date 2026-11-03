from unittest.mock import patch
from test_core import Base
from lingsuo.intents import file_request, selection_number


class ConversationTests(Base):
    def test_local_phrase_rules_keep_file_names_and_desktop_scope(self):
        for phrase,query,desktop in [("帮我把桌面上的操作手册.pdf发给我","操作手册.pdf",True),
                                     ("请找一下报告","报告",False),("发我共享目录里的预算.xlsx","预算.xlsx",False),
                                     ('把“会议 2026.txt”发给我','会议 2026.txt',False)]:
            with self.subTest(phrase=phrase):
                result = file_request(phrase)
                self.assertEqual((result.query,result.desktop_only),(query,desktop))
        self.assertIsNone(file_request("不要把报告发给我"))
        self.assertIsNone(file_request("昨天收到文件了吗"))
        self.assertEqual(selection_number("第二个"),2)
        self.assertEqual(selection_number("选20号"),20)

    def test_unique_file_needs_only_plain_send_and_deduplicates(self):
        cfg = self.config(qq=True); cfg["files"]["allow_desktop"] = True; self.app.config.save(cfg)
        self.sample("操作手册.pdf")
        self.app.receive("alice","帮我把桌面的操作手册发给我","one")
        self.assertIn("已选",self.qq.texts[-1][1]); self.assertEqual(self.qq.files,[])
        self.app.receive("alice","发送","confirm")
        self.app.receive("alice","发送","confirm")
        self.assertEqual(len(self.qq.files),1)
        self.assertEqual(self.qq.files[0][0],"alice")
        self.assertIn("平台",self.qq.texts[-1][1])

    def test_multiple_results_choose_natural_number_then_send(self):
        self.config(qq=True); self.sample("报告1.txt"); self.sample("报告2.txt")
        answer = self.app.commands.handle("alice","发我报告","m")
        self.assertIn("编号",answer); self.assertEqual(self.qq.files,[])
        answer = self.app.commands.handle("alice","第二个","m2")
        self.assertIn("报告2.txt",answer); self.assertEqual(self.qq.files,[])
        self.app.commands.handle("alice","发吧","m3")
        self.assertEqual(self.qq.files[0][2],"报告2.txt")

    def test_other_user_expiry_changed_content_and_repeat_cannot_send(self):
        self.config(qq=True); sample = self.sample("报告.txt")
        commands = self.app.commands
        commands.handle("alice","发我报告","1")
        self.assertIn("确认无效",commands.handle("bob","发送","2"))
        commands.pending["alice"]["expires"] = 0
        self.assertIn("过期",commands.handle("alice","发送","3"))
        commands.handle("alice","发我报告","4"); sample.write_text("changed")
        with self.assertRaises(ValueError): commands.handle("alice","发送","5")
        self.assertEqual(self.qq.files,[])
        self.assertIn("确认无效",commands.handle("alice","发送","6"))

    def test_new_failed_request_or_cancellation_discards_old_selection(self):
        self.config(qq=True); self.sample("报告.txt")
        commands = self.app.commands
        for new_request in ("找一下不存在的文件","把桌面的报告发给我","不要发了",r"发我 C:\private.txt"):
            commands.handle("alice","发我报告","select")
            try:commands.handle("alice",new_request,"new")
            except ValueError:pass
            self.assertNotIn("file",commands.pending.get("alice",{}))
            self.assertIn("确认无效",commands.handle("alice","发送","send"))
        self.assertEqual(self.qq.files,[])

    def test_plain_language_does_not_bypass_authorization_or_unknown_intents(self):
        self.config(qq=True); self.sample("报告.txt")
        with patch.object(self.app.files,"search",side_effect=AssertionError("unauthorized search")):
            answer = self.app.commands.handle("stranger","把桌面的报告发给我","m")
        self.assertIn("尚未授权",answer)
        self.assertIn("未识别",self.app.commands.handle("alice","随便帮我弄一下","m2").replace("还没识别","未识别"))
        self.assertIn("监控",self.app.commands.handle("alice","看看监控","m3"))
        self.app.commands.handle("alice","有变化告诉我","m4")
        self.assertIn("alice",self.app.store.subscribers())
        self.app.commands.handle("alice","关掉提醒","m5")
        self.assertNotIn("alice",self.app.store.subscribers())
