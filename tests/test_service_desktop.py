"""Own hidden Tk windows; no live QQ, mouse, browser or user file operations."""
import json
import time
import tkinter as tk
from unittest.mock import MagicMock,patch
from test_guidance import GuidanceBase
from lingsuo.desktop import DesktopApp


class ServiceDesktopTests(GuidanceBase):
    def setUp(self):
        super().setUp();self.root=tk.Tk();self.root.withdraw();self.confirm=MagicMock(return_value=True)
        self.window=DesktopApp(self.root,self.app,tray=False,confirm=self.confirm,open_url=MagicMock(),draft_choice=lambda _:"discard");self.window.show_error=MagicMock()
        self.addCleanup(self.finish)

    def pump(self):
        until=time.monotonic()+5
        while time.monotonic()<until:
            self.root.update()
            if not self.window.busy:self.window.refresh();return
            time.sleep(.01)
        self.fail("desktop fixture timed out")

    def click(self,key):self.window.buttons[key].invoke();self.pump()

    def finish(self):
        if self.root.winfo_exists():
            self.window.disarm_automation();self.window.pool.shutdown(wait=True)
            self.window.snapshot_pool.shutdown(wait=True)
            if self.window.poll_id:self.root.after_cancel(self.window.poll_id)
            self.root.destroy()

    def test_new_service_cards_are_opt_in_and_scope_saving_does_not_enable_qq(self):
        self.click("setting_watch");self.assertFalse(self.app.config.get()["watch"]["enabled"])
        self.click("watch_save");self.assertFalse(self.app.config.get()["watch"]["enabled"])
        self.click("setting_anomalies");self.window.vars["anomaly_memory_mb"].set("512");self.click("anomaly_save")
        self.assertEqual(self.app.config.get()["anomalies"]["memory_mb"],512)
        self.assertFalse(self.app.config.get()["anomalies"]["enabled"])
        self.click("setting_inbound");self.window.vars["inbox_directory"].set(str(self.shared));self.click("inbox_save")
        self.assertFalse(self.app.config.get()["inbound"]["enabled"]);self.assertFalse(self.app.config.get()["qq"]["enabled"])
        self.assertFalse(self.window.show_error.called)

    def test_native_manager_previews_then_moves_temporary_file_and_updates_tasks(self):
        self.app.save_section("file_manager",{"roots":[str(self.shared)]});src=self.sample("temporary.txt")
        self.window.refresh();self.window.load_folder(str(self.shared));self.pump()
        self.window.vars["manager_query"].set("temporary");self.window.search_files()
        self.window.manager_tree.selection_set("0");self.click("manager_rename")
        self.window.vars["manager_target"].set("renamed.txt");self.click("manager_preview")
        self.assertTrue(src.exists());self.assertFalse((self.shared/"renamed.txt").exists())
        self.click("manager_execute");self.assertFalse(src.exists());self.assertTrue((self.shared/"renamed.txt").exists())
        self.assertEqual(self.window.tabs.select(),str(self.window.pages["tasks"]))
        self.assertIn("本机文件变更",str([self.window.task_tree.item(i,"values") for i in self.window.task_tree.get_children()]))
        self.assertFalse(self.window.show_error.called)

    def test_editing_preview_target_invalidates_execution(self):
        self.app.save_section("file_manager",{"roots":[str(self.shared)]});src=self.sample()
        self.window.refresh();self.window.load_folder(str(self.shared));self.pump()
        self.window.manager_tree.selection_set("0");self.click("manager_rename")
        self.window.vars["manager_target"].set("first.txt");self.click("manager_preview")
        self.window.vars["manager_target"].set("changed.txt");self.click("manager_execute")
        self.assertTrue(src.exists());self.assertTrue(self.window.show_error.called);self.assertFalse((self.shared/"changed.txt").exists())

    def test_task_cancel_remains_available_while_desktop_worker_is_busy(self):
        ident=self.app.tasks.create("fixture.wait");self.window.show("tasks");self.window.refresh();self.window.task_tree.selection_set(ident)
        self.window.busy=True
        self.window.buttons["task_cancel"].invoke();self.window.busy=False
        self.assertTrue(self.app.tasks.get(ident)["cancel_requested"])

    def test_web_read_only_after_exact_local_confirmation_uses_fake_downloader(self):
        self.click("setting_webtasks");self.window.vars["web_task_url"].set("https://example.com/public")
        calls=[]
        def fake(url,out,*args,**kw):calls.append(url);out.write(b"<p>fixture page</p>");return {"content_type":"text/html","size":19}
        self.app.web.download=fake;self.confirm.return_value=False;self.click("web_task_read");self.assertFalse(calls)
        self.confirm.return_value=True;self.click("web_task_read");self.assertEqual(calls,["https://example.com/public"])
        self.assertEqual(self.app.tasks.recent()[0]["state"],"completed");self.assertFalse(self.window.show_error.called)
