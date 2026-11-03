"""Hidden native windows, temporary files, injected processes/QQ; no external I/O."""
import json
import shutil
import socket
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_guidance import GuidanceBase
from lingsuo.app import main
from lingsuo.desktop import DesktopApp


class NativeStartupTests(unittest.TestCase):
    def test_default_starts_native_window_without_http_browser_or_child(self):
        with tempfile.TemporaryDirectory() as d, patch("lingsuo.app.Runtime") as factory, \
                patch("lingsuo.app.acquire_lock") as lock, patch("lingsuo.desktop.run_desktop") as desktop, \
                patch("webbrowser.open", side_effect=AssertionError("browser forbidden")), \
                patch.object(socket.socket, "bind", side_effect=AssertionError("HTTP forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("child forbidden")):
            self.assertEqual(main(["--data-dir", d]), 0)
            factory.return_value.start.assert_called_once()
            desktop.assert_called_once_with(factory.return_value)
            factory.return_value.stop.assert_called_once()
            lock.return_value.close.assert_called_once()

    def test_headless_has_no_second_process_or_web_server(self):
        with tempfile.TemporaryDirectory() as d, patch("lingsuo.app.Runtime") as factory, \
                patch("lingsuo.app.acquire_lock"), patch("lingsuo.desktop.run_desktop") as desktop, \
                patch.object(socket.socket, "bind", side_effect=AssertionError("HTTP forbidden")), \
                patch("subprocess.Popen", side_effect=AssertionError("child forbidden")):
            factory.return_value.stop_event.wait.return_value = True
            self.assertEqual(main(["--headless", "--data-dir", d]), 0)
            desktop.assert_not_called()


class NativeDesktopTests(GuidanceBase):
    def test_admin_restart_cancel_keeps_current_window_running(self):
        with patch("lingsuo.restart.request_admin_restart",side_effect=PermissionError("fixture cancel")) as request:
            self.window.restart_as_admin()
            request.assert_called_once_with(self.app.directory)
            self.assertFalse(self.window.closing)
            self.window.show_error.assert_called_once()

    def test_admin_restart_accepted_waits_for_normal_close_and_ignores_repeat(self):
        with patch("lingsuo.restart.request_admin_restart") as request:
            self.window.restart_as_admin()
            self.assertTrue(self.window.closing)
            self.window.restart_as_admin()
            request.assert_called_once_with(self.app.directory)

    def test_admin_restart_respects_unsaved_draft_and_menu_queues_ui_event(self):
        self.window.vars["appid"].set("unsaved-fixture")
        self.window.draft_choice=lambda _:"continue"
        with patch("lingsuo.restart.request_admin_restart") as request:
            self.window.restart_as_admin()
            request.assert_not_called();self.assertFalse(self.window.closing)
        tray=MagicMock()
        self.window.tray_menu(tray)
        calls=tray.MenuItem.call_args_list
        restart=next(call for call in calls if call.args[0]=="以管理员身份重启")
        restart.args[1]()
        self.assertEqual(self.window.events.get_nowait(),("restart-admin",None))
        self.window.draft_choice=lambda _:"discard"

    def test_home_lists_saved_programs_and_effective_scopes_and_removes_revoked_items(self):
        cfg=self.app.config.get()
        cfg["monitor"]["process_names"]=["fixture.exe","other.exe"]
        cfg["files"]["roots"]=[str(self.shared)]
        self.app.config.save(cfg)
        self.window.show("home");self.window.refresh()
        programs=self.window.home_cards["monitor"]
        roots=self.window.home_cards["files"]
        self.assertEqual([row[0] for row in programs.rows],["fixture.exe","other.exe"])
        self.assertEqual(programs.rows,[("fixture.exe","状态未知"),("other.exe","状态未知")])
        self.assertEqual([row[1] for row in roots.rows],[str(self.shared)])
        self.assertTrue(roots.rows[0][0].endswith(":"))
        cfg["files"]["roots"]=[];cfg["monitor"]["process_names"]=[]
        self.app.config.save(cfg);self.window.refresh()
        self.assertEqual(roots.rows,[])
        self.assertEqual(programs.rows,[])

    def test_overview_qq_table_uses_saved_environment_and_actual_authorization(self):
        cfg=self.app.config.get();cfg["qq"]["is_sandbox"]=True
        self.app.config.save(cfg);self.window.refresh()
        rows=self.window.home_cards["qq"].rows
        self.assertIn(("运行环境","沙箱"),rows)
        self.assertIn(("本人授权","待绑定"),rows)
        self.assertIn(("通知订阅","0 人"),rows)
        self.assertFalse(self.app.files.roots());self.assertEqual(self.gateway.starts,0)

    def test_overview_activity_shows_latest_rows_without_hiding_records_and_header_shows_version(self):
        self.app.store.event("fixture.event","最近一条隔离记录")
        self.window.show("home");self.window.refresh()
        texts=[row[2] for row in self.window.home_activity_rows]
        self.assertIn("最近一条隔离记录",texts)
        self.assertLessEqual(len(texts),3)
        self.assertFalse(self.window.home_activity_empty.winfo_manager())
        self.assertEqual(self.window.home_todo_count.get(),"0 / 3")
        self.click("home_records");self.assertEqual(self.window.current_page(),"tasks")
        self.assertEqual(self.window.badges[3].winfo_manager(),"pack")
        self.assertEqual(self.window.badges[3].current,("版本 1.0",None))

    def test_home_routes_connected_but_unbound_user_to_binding_without_granting_files(self):
        cfg=self.app.config.get()
        cfg["qq"].update(enabled=True,appid="fake-app",secret="fake-secret",allowed_users=[])
        self.app.config.save(cfg);self.gateway.connected=True
        self.window.show("home");self.window.refresh()
        self.assertEqual(self.window.home_cards["qq"].state.get(),"已连接")
        self.assertIn("尚未绑定",self.window.home_cards["qq"].detail.get())
        self.assertEqual(self.window.home_cards["files"].state.get(),"未设置")
        self.click("home_qq")
        self.assertEqual(self.window.current_page(),"notifications")
        self.assertEqual(self.window.qq_stage_index,1)
        self.assertFalse(self.app.config.get()["qq"]["allowed_users"])
        self.assertFalse(self.app.files.roots())

    def test_home_real_events_and_buttons_route_without_starting_operations(self):
        self.app.store.event("fixture.event","临时隔离事件")
        self.window.show("home");self.window.refresh()
        texts=[row[2] for row in self.window.home_activity_rows]
        self.assertIn("临时隔离事件",texts)
        self.click("home_monitor");self.assertEqual(self.window.current_page(),"monitor")
        self.click("home_files");self.assertEqual(self.window.active_dialog.title(),"文件夹权限")
        self.assertFalse(self.app.files.roots());self.assertEqual(self.gateway.starts,0)
        self.click("home_records");self.assertEqual(self.window.current_page(),"tasks")

    def test_autoclick_is_removed_and_image_tool_stays_manual(self):
        self.assertNotIn("tool_repeat",self.window.buttons)
        with self.assertRaises(ValueError):self.window.automation_dialog("repeat")
        self.click("tool_image")
        self.assertEqual(self.window.automation_mode,"image")
        self.assertNotIn("click_speed",self.window.vars)
        self.assertIsNone(self.window.armed_plan);self.assertIsNone(self.app.automation.thread)

    def setUp(self):
        super().setUp()
        self.root = tk.Tk(); self.root.withdraw()
        self.destroyed = False
        self.root.bind("<Destroy>", self.on_destroy, add=True)
        self.confirm = MagicMock(return_value=True)
        self.open_url = MagicMock()
        self.window = DesktopApp(self.root, self.app, tray=False, confirm=self.confirm, open_url=self.open_url,draft_choice=lambda _:"discard")
        self.window.show_error = MagicMock()
        self.addCleanup(self.finish_window)

    def on_destroy(self, event):
        if event.widget == self.root: self.destroyed = True

    def pump(self, until=None):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            if not self.destroyed: self.root.update()
            if until() if until else not self.window.busy:
                if not self.destroyed:self.window.refresh()
                return
            time.sleep(.01)
        self.fail("native UI action did not finish")

    def finish_window(self):
        if not self.destroyed:
            self.window.close(); self.pump(lambda: self.destroyed)

    def click(self, key):
        self.window.buttons[key].invoke(); self.pump()

    def configured(self):
        self.config(qq=True)
        self.gateway.connected = True
        self.window.refresh()

    def test_native_pages_real_todo_and_navigation(self):
        self.assertEqual(set(self.window.pages), {"home", "monitor", "files", "notifications", "automation", "plugins", "settings", "tasks"})
        self.assertTrue(all(p.winfo_class() == "TFrame" for p in self.window.pages.values()))
        self.assertFalse(any(self.app.status()["todo"].values()))
        self.assertEqual(self.window.home_cards["monitor"].state.get(), "未启动")
        self.assertEqual(self.window.home_cards["qq"].state.get(), "未连接")
        self.assertEqual(self.window.home_cards["files"].state.get(), "未设置")
        self.assertFalse(hasattr(self.window, "articles"))
        self.window.nav["settings"].invoke(); self.pump()
        self.assertEqual(self.window.page_title.get(), "设置")

    def test_sidebar_settings_cards_route_to_real_features(self):
        self.window.nav["settings"].invoke(); self.pump()
        self.assertEqual(self.window.page_title.get(), "设置")
        self.click("setting_qq")
        self.assertEqual(self.window.tabs.select(), str(self.window.pages["notifications"]))
        self.assertFalse(getattr(self.window, "active_dialog", None))
        self.assertEqual(self.window.qq_sandbox_check.winfo_toplevel(), self.root)
        self.click("qq_cancel")
        self.window.show("files")
        self.assertEqual(self.window.tabs.select(), str(self.window.pages["files"]))
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])

    def test_advanced_card_adds_program_but_requires_monitor_save(self):
        self.click("setting_advanced")
        self.window.vars["manual_process"].set("fixture.exe")
        self.click("manual_add")
        self.assertIn("fixture.exe", self.window.chosen)
        self.assertNotIn("fixture.exe", self.app.config.get()["monitor"]["process_names"])
        self.click("save_monitor")
        self.assertEqual(self.app.config.get()["monitor"]["process_names"], ["fixture.exe"])

    def test_advanced_values_apply_without_granting_file_scope_or_qq(self):
        self.click("setting_advanced")
        self.window.vars["interval"].set("8"); self.window.vars["max_mb"].set("12")
        self.click("advanced_save")
        cfg = self.app.config.get()
        self.assertEqual(cfg["monitor"]["interval"], 8); self.assertEqual(cfg["files"]["max_mb"], 12)
        self.assertEqual(cfg["files"]["roots"], []); self.assertFalse(cfg["qq"]["enabled"])

    def test_process_search_select_save_and_pause_with_injected_snapshot(self):
        self.app.monitor.collector = lambda: [{"name": "fixture.exe", "pid": 1, "has_window":True}, {"name": "fixture.exe", "pid": 2, "has_window":True}, {"name": "other.exe", "pid": 3, "has_window":False}]
        self.click("read_processes")
        self.window.vars["process_query"].set("fixture")
        self.assertEqual(self.window.process_tree.get_children(), ("1", "2"))
        self.window.toggle_process("1")
        self.assertEqual(self.window.process_tree.item("2", "values")[0], "☑")
        self.click("save_monitor")
        self.assertEqual(self.app.config.get()["monitor"]["process_names"], ["fixture.exe"])
        self.window.vars["monitor_enabled"].set(False); self.click("save_monitor")
        self.assertFalse(self.app.config.get()["monitor"]["enabled"])

    def test_process_pid_search_filters_and_cancel_preserve_saved_selection(self):
        self.window.processes_loaded([{"name":"app.exe","pid":11,"has_window":True,"memory":"1,024 K"},
                                      {"name":"bg.exe","pid":22,"has_window":False,"memory":"无权限 / 未知"}])
        self.assertEqual(self.window.process_tree.get_children(), ("11",))
        self.window.set_process_filter("background")
        self.assertEqual(self.window.process_tree.get_children(), ("22",))
        self.window.toggle_process("22")
        self.window.vars["process_query"].set("11"); self.window.set_process_filter("all")
        self.assertEqual(self.window.process_tree.get_children(), ("11",))
        self.click("cancel_monitor")
        self.assertEqual(self.window.chosen, set())

    def test_skins_persist_without_restarting_backend_or_requesting_network(self):
        self.click("setting_appearance"); self.click("theme_ocean")
        self.assertEqual(json.loads((self.app.directory / "appearance.json").read_text())["theme"], "ocean")
        self.assertEqual(self.window.theme, "dark")
        self.assertEqual(self.gateway.starts, 0)

    def test_web_search_only_opens_explicit_encoded_query(self):
        self.click("setting_web")
        self.open_url.assert_not_called()
        self.window.vars["web_query"].set("彩虹岛 & 帮助")
        self.click("web_search")
        from urllib.parse import urlsplit, parse_qs
        url = self.open_url.call_args.args[0]
        self.assertEqual(urlsplit(url).netloc, "www.bing.com")
        self.assertEqual(parse_qs(urlsplit(url).query)["q"], ["彩虹岛 & 帮助"])


    def test_same_custom_key_toggles_and_parameter_edit_disarms(self):
        from test_automation import FakeMouse, FakeHotkey
        pumps=[]
        def factory(key):pumps.append(FakeHotkey(key));return pumps[-1]
        self.window.hotkey_factory=factory;self.app.automation.backend=FakeMouse()
        self.click("tool_image")
        for key,value in (("click_interval","500"),("click_hotkey","F7"),("click_template","fixture"),("region_x","0"),("region_y","0"),("region_w","30"),("region_h","30")):
            self.window.vars[key].set(value)
        self.click("automation_apply");pumps[0].press()
        self.pump(lambda:self.app.automation.status()["running"])
        pumps[0].press();self.pump(lambda:not self.app.automation.status()["running"])
        self.assertEqual(self.app.automation.backend.clicks,[])
        generation=self.window.automation_generation
        self.window.vars["click_interval"].set("600")
        self.assertIsNone(self.window.armed_plan);self.assertTrue(pumps[0].closed)
        self.window.events.put(("automation-toggle",generation))
        waited=[];self.root.after(350,lambda:waited.append(True));self.pump(lambda:bool(waited))
        self.assertFalse(self.app.automation.status()["running"])


    def test_dialog_opens_at_main_window_center(self):
        self.root.deiconify();self.root.geometry("1150x800+70+30");self.root.update()
        self.click("setting_appearance");dialog=self.window.active_dialog
        self.root.update()
        parent_center=(self.root.winfo_rootx()+self.root.winfo_width()/2,self.root.winfo_rooty()+self.root.winfo_height()/2)
        child_center=(dialog.winfo_rootx()+dialog.winfo_width()/2,dialog.winfo_rooty()+dialog.winfo_height()/2)
        self.assertLess(abs(child_center[0]-parent_center[0]),16)
        self.assertLess(abs(child_center[1]-parent_center[1]),40)

    def test_single_bound_qq_recipient_is_automatic(self):
        self.configured()
        cfg=self.app.config.get();cfg["qq"]["allowed_users"]=["alice"];self.app.config.save(cfg)
        self.window.refresh()
        self.assertEqual(self.window.vars["file_user"].get(),"alice")
        self.window.refresh()
        self.assertEqual(self.window.vars["file_user"].get(),"alice")
        self.assertEqual(self.window.vars["file_user"].get(),"alice")
        self.assertFalse(hasattr(self.window,"file_user_combo"))

    def test_official_platform_is_only_opened_on_explicit_click(self):
        self.open_url.assert_not_called()
        self.click("setting_qq")
        self.click("official")
        self.open_url.assert_called_once_with("https://q.qq.com/")

    def test_openid_never_grants_first_sender_without_local_save(self):
        self.configured()
        cfg = self.app.config.get(); cfg["qq"]["allowed_users"] = []; self.app.config.save(cfg)
        answer = self.app.commands.handle("stranger", "开启", "message")
        self.assertIn("stranger", answer)
        self.window.refresh()
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], [])
        self.click("setting_qq")
        self.window.vars["qq_openids"].set("owner")
        self.confirm.return_value = False; self.click("qq_save")
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], [])
        self.confirm.return_value = True; self.click("qq_save")
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"], ["owner"])
        self.assertEqual(self.app.store.subscribers(), ["owner"])
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])
        self.assertNotIn("pair_start", self.window.buttons)

    def test_credentials_check_is_real_backend_action_and_not_saved_or_connected(self):
        checker = MagicMock()
        checker.check_credentials.return_value = {"ok": True, "message": "test-double credential acceptance only"}
        self.app.transport_factory = lambda _: checker
        self.click("setting_qq")
        self.window.vars["appid"].set("fixture-app"); self.window.vars["secret"].set("fixture-secret")
        self.click("check_qq")
        checker.check_credentials.assert_called_once()
        self.assertFalse(self.app.config.get()["qq"]["enabled"])
        self.assertFalse(self.gateway.connected)
        self.assertEqual(self.window.message.get(), "test-double credential acceptance only")

    def test_saved_secret_uses_fixed_marker_and_draft_replace_preserves_blank_semantics(self):
        self.configured(); self.click("setting_qq")
        control = self.window.qq_secret_input
        original = self.app.config.get()["qq"]["secret"]
        self.assertEqual(control.entry.get(), "")
        self.assertEqual(self.window.vars["secret"].get(), "")
        self.assertTrue(control.mask_visible)
        self.assertEqual(control.placeholder.cget("text"), "●" * 12)
        self.assertNotIn(original, control.placeholder.cget("text"))
        self.click("qq_save")
        self.assertEqual(self.app.config.get()["qq"]["secret"], original)
        self.window.vars["secret"].set("fixture-replacement")
        self.assertFalse(control.mask_visible)
        self.assertEqual(control.entry.cget("show"), "●")
        self.click("qq_cancel")
        self.assertTrue(control.mask_visible)
        self.assertEqual(self.app.config.get()["qq"]["secret"], original)
        # Insert follows the same variable path as typing or pasting; no clipboard read.
        control.entry.insert(0, "fixture-replacement")
        self.click("qq_save")
        self.assertEqual(self.app.config.get()["qq"]["secret"], "fixture-replacement")
        self.assertEqual(control.entry.get(), "")
        self.assertTrue(control.mask_visible)

    def test_secret_copy_cut_and_shortcuts_never_reach_clipboard(self):
        control = self.window.qq_secret_input
        self.assertFalse(control.mask_visible)
        control.entry.insert(0, "fixture-new-secret")
        self.assertFalse(bool(int(control.entry.cget("exportselection"))))
        calls = []
        # Replace this interpreter's clipboard command; never touch the OS clipboard.
        self.root.tk.call("rename", "::clipboard", "::lingsuo_saved_clipboard")
        self.root.tk.createcommand("::clipboard", lambda *args: calls.append(args) or "")
        baseline = ttk.Entry(self.root)
        try:
            baseline.insert(0, "public-fixture"); baseline.selection_range(0, "end")
            baseline.event_generate("<<Copy>>")
            self.assertTrue(calls, "positive control must reach the stub clipboard")
            calls.clear()
            for event in ("<<Copy>>", "<<Cut>>", "<Control-c>", "<Control-C>", "<Control-x>",
                          "<Control-X>", "<Control-Insert>", "<Shift-Delete>"):
                self.assertTrue(control.entry.bind(event))
                control.entry.selection_range(0, "end")
                control.entry.event_generate(event)
            self.assertEqual(calls, [])
            self.assertEqual(control.entry.get(), "fixture-new-secret")
            self.assertEqual(control.entry.cget("show"), "●")
        finally:
            baseline.destroy()
            self.root.tk.deletecommand("::clipboard")
            self.root.tk.call("rename", "::lingsuo_saved_clipboard", "::clipboard")

    def test_save_and_connect_enables_qq_without_an_extra_hidden_switch(self):
        self.click("setting_qq")
        self.window.vars["appid"].set("fixture-app"); self.window.vars["secret"].set("fixture-secret")
        self.window.vars["qq_sandbox"].set(True)
        self.click("qq_save")
        self.assertEqual(self.gateway.starts, 0)
        self.click("connect_qq")
        self.assertTrue(self.app.config.get()["qq"]["enabled"])
        self.assertTrue(self.app.config.get()["qq"]["is_sandbox"])
        self.assertEqual(self.gateway.starts, 1)
        self.click("setting_notify_manage"); self.click("disable_qq")
        self.assertFalse(self.app.config.get()["qq"]["enabled"])

    def test_local_editor_saves_and_forwards_only_after_local_preview_confirmation(self):
        self.configured(); sample = self.sample(data=b"initial private note")
        self.app.save_section("local_data", {"roots":[str(self.shared)]})
        self.click("manager_edit")
        with patch("lingsuo.desktop.filedialog.askopenfilename", return_value=str(sample)): self.click("private_open")
        self.window.document_editor.delete("1.0", "end"); self.window.document_editor.insert("end", "edited note")
        self.click("private_save")
        self.assertEqual(sample.read_text(), "edited note")
        self.assertEqual(self.app.files.search(""), [])
        self.click("private_send"); self.assertEqual(self.app.store.due(), [])
        self.click("private_cancel_send"); self.assertEqual(self.app.store.due(), [])
        self.click("private_send"); self.click("private_confirm_send")
        self.assertEqual(self.app.store.due()[0]["text"], "edited note")
        self.assertEqual(self.qq.files, [])

    def test_native_folder_authorization_requires_confirmation(self):
        with patch("lingsuo.desktop_files.filedialog.askdirectory", return_value=str(self.shared)):
            self.confirm.return_value = False
            self.window.add_root(); self.pump()
            self.assertEqual(self.app.config.get()["files"]["roots"], [])
            self.confirm.return_value = True
            self.window.add_root(); self.pump()
        self.assertEqual(self.app.config.get()["files"]["roots"], [str(self.shared)])
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])

    def test_version_and_custom_checkmarks_match_saved_desktop_authorization(self):
        self.assertEqual(self.window.badges[3].current, ("版本 1.0", None))
        self.assertEqual(self.app.status()["version"], "1.0")
        self.assertTrue(self.window.monitor_check.find_withtag("tick"))
        self.window.monitor_check.invoke()
        self.assertFalse(self.window.monitor_check.find_withtag("tick"))
        self.confirm.return_value = False
        self.window.save_desktop(True); self.pump()
        self.assertFalse(self.app.config.get()["files"]["allow_desktop"])
        self.confirm.return_value = True
        self.window.save_desktop(True); self.pump()
        self.assertTrue(self.app.config.get()["files"]["allow_desktop"])

    def test_qq_original_fields_and_cancel_are_directly_on_binding_page(self):
        self.window.open_notifications(); self.pump()
        self.assertIsNone(self.window.test_tree)
        self.assertIsNone(self.window.history_tree)
        self.window.vars["appid"].set("unsaved"); self.window.vars["secret"].set("unsaved")
        self.window.qq_sandbox_check.invoke()
        self.assertTrue(self.window.qq_sandbox_check.find_withtag("tick"))
        self.assertEqual(self.window.qq_sandbox_check.winfo_toplevel(), self.root)
        self.assertEqual(self.window.buttons["qq_save"].winfo_toplevel(), self.root)
        self.click("qq_cancel")
        self.assertEqual(self.app.config.get()["qq"]["appid"], "")
        self.assertFalse(self.app.config.get()["qq"]["is_sandbox"])
        self.assertEqual(self.gateway.starts, 0)
        self.click("setting_qq")
        self.assertEqual(self.window.vars["appid"].get(), "")
        self.assertFalse(self.window.vars["qq_sandbox"].get())

    def test_folder_dropdown_aligns_with_its_trigger(self):
        self.configured(); self.window.refresh()
        menu = self.window.folder_menu
        menu.values = list(self.window.folder_map.keys()) or ["shared"]
        menu.open(); self.root.update()
        self.assertIsNotNone(menu.popup)
        self.assertLessEqual(abs(menu.popup.winfo_rootx() - menu.winfo_rootx()), 4)
        self.assertGreaterEqual(menu.popup.winfo_width(), menu.winfo_width())
        menu.close()

    def test_folder_dropdown_closes_when_clicking_away_and_reopens(self):
        self.configured(); self.window.refresh()
        menu = self.window.folder_menu
        menu.values = list(self.window.folder_map.keys()) or ["shared"]
        menu.open(); self.root.update()
        self.assertIsNotNone(menu.popup)
        menu.popup.event_generate("<Button-1>", x=-40, y=-40); self.root.update()
        self.assertIsNone(menu.popup)
        menu.toggle(); self.root.update()
        self.assertIsNotNone(menu.popup)
        menu.close()

    def test_file_preview_tracks_selection_without_uploading(self):
        self.configured(); sample = self.sample()
        self.window.refresh()
        self.window.load_folder(str(self.shared)); self.pump()
        listed=[str(self.window.manager_tree.item(i,"values")) for i in self.window.manager_tree.get_children()]
        self.assertTrue(any(sample.name in row for row in listed))
        self.window.manager_tree.selection_set("0"); self.window.file_selected(); self.pump()
        self.assertIn("KB", self.window.file_selection.get())
        self.assertTrue(self.window.selbar_shown)
        self.assertEqual(self.qq.files, [])

    def test_folder_revoke_preserves_separate_desktop_authorization(self):
        self.app.save_section("files", {"roots":[str(self.shared)], "allow_desktop":True})
        self.window.refresh()
        self.assertEqual(self.window.file_folder.get(),str(self.shared))
        self.window.remove_root(); self.pump()
        self.assertEqual(self.app.config.get()["files"]["roots"], [])
        self.assertTrue(self.app.config.get()["files"]["allow_desktop"])

    def test_file_picker_only_reaches_authorized_directories(self):
        outside = self.root_path_file()
        with self.assertRaises(PermissionError):
            self.app.files.fingerprint(outside)
        self.assertEqual(self.app.files.search("outside"), [])
        self.window.show_error.assert_not_called()

    def root_path_file(self):
        path = self.root_path / "outside.txt"
        path.write_bytes(b"temporary fixture")
        return path

    @property
    def root_path(self):
        # Base.root is the temporary Path; this suite owns Tk separately.
        return Path(self.tmp.name)

    def test_file_confirm_shows_scope_size_and_only_sends_after_accept(self):
        self.configured(); sample = self.sample()
        self.window.refresh()
        self.window.load_folder(str(self.shared)); self.pump()
        self.window.manager_tree.selection_set("0"); self.window.file_selected()
        self.confirm.return_value = False; self.click("file_send_selected"); self.pump()
        self.assertEqual(self.qq.files, [])
        text = self.confirm.call_args.args[1]
        self.assertIn(sample.name, text); self.assertNotIn("OpenID", text); self.assertIn("KB", text)
        self.confirm.return_value = True; self.click("file_send_selected"); self.pump()
        self.assertEqual(len(self.qq.files), 1)
        self.assertEqual(self.qq.files[0][1], b"example")

    def test_file_send_without_a_choice_asks_for_one_instead_of_uploading(self):
        self.configured(); self.sample()
        self.window.refresh()
        self.confirm.return_value = True
        for row in self.window.manager_tree.selection():
            self.window.manager_tree.selection_remove(row)
        self.click("file_send_selected")
        self.assertEqual(self.qq.files, [])
        self.assertTrue(self.window.show_error.called)

    def test_notification_acceptance_does_not_mark_user_received(self):
        self.configured(); self.window.vars["notify_user"].set("alice")
        self.click("setting_notify_manage")
        self.click("test_notify")
        row = self.app.status()["tests"][0]
        self.assertIsNone(row["accepted"]); self.assertIsNone(row["confirmed"])
        self.window.test_tree.selection_set(row["id"]); self.click("receipt")
        self.window.show_error.assert_called_once()
        self.app.flush_once(); self.window.refresh()
        self.assertIsNotNone(self.app.status()["tests"][0]["accepted"])
        self.assertIsNone(self.app.status()["tests"][0]["confirmed"])
        self.click("receipt")
        self.assertIsNotNone(self.app.status()["tests"][0]["confirmed"])
        self.click("unsubscribe"); self.assertNotIn("alice", self.app.store.subscribers())

    def test_notification_and_history_windows_do_not_appear_on_binding_page(self):
        self.window.open_notifications(); self.pump()
        self.assertIsNone(self.window.test_tree)
        self.click("setting_notify_manage")
        self.assertEqual(self.window.active_dialog.title(), "通知管理")
        first = self.window.test_tree
        self.assertNotEqual(first.winfo_toplevel(), self.root)
        self.click("setting_history")
        self.assertEqual(self.window.active_dialog.title(), "运行记录")
        self.assertFalse(first.winfo_exists())
        self.window.refresh()
        self.click("history_close")
        self.window.refresh()
        self.click("setting_notify_manage")
        self.assertNotEqual(self.window.test_tree, first)
        self.click("notification_close")
        self.window.refresh()
        self.assertFalse(getattr(self.window, "active_dialog").winfo_exists())

    def test_plugins_require_trust_then_restart_to_load(self):
        source = Path(__file__).resolve().parent.parent / "sample_plugins/example"
        shutil.copytree(source, self.app.plugins.directory / "example")
        self.click("plugins_refresh"); self.window.plugin_tree.selection_set("example")
        self.confirm.return_value = False; self.click("plugins_enable")
        self.assertFalse(self.app.config.get()["plugins"]["enabled"])
        self.confirm.return_value = True; self.click("plugins_enable")
        row = self.app.plugins.scan()[0]
        self.assertTrue(row["enabled"]); self.assertTrue(row["trusted"]); self.assertFalse(row["loaded"])
        self.click("plugins_disable"); self.assertFalse(self.app.config.get()["plugins"]["enabled"])

    def test_close_waits_for_worker_without_creating_child_process(self):
        ready, release = threading.Event(), threading.Event()
        def action(): ready.set(); release.wait(2)
        self.window.submit(action); self.assertTrue(ready.wait(1))
        self.window.close(); self.root.update()
        self.assertFalse(self.destroyed)
        release.set(); self.pump(lambda: self.destroyed)

    def test_close_window_uses_same_process_tray_and_fallback_can_cancel_exit(self):
        self.window.tray_ready = True
        self.window.hide(); self.assertEqual(self.root.state(), "withdrawn")
        # Exercise restore through the same UI event queue, without touching other windows.
        with patch.object(self.window, "restore") as restore:
            self.window.events.put(("restore", None)); self.pump(lambda: restore.called)
        self.window.tray_ready = False; self.confirm.return_value = False
        self.window.hide(); self.assertFalse(self.window.closing)
