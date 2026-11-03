"""Experience revision: temporary data, fake QQ/Win32 signals, own hidden Tk only."""
import copy
import json
import statistics
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock,patch
from test_core import Base
from test_guidance import GuidanceBase
from lingsuo.config import Config
from lingsuo.credentials import PREFIX,CredentialError,protect,unprotect
from lingsuo.desktop import DesktopApp
from lingsuo.permissions import rows,apply_row,KINDS
from lingsuo.qq import QQTransport
from lingsuo.instance import RestoreSignal,request_restore,event_name
from lingsuo.desktop_ux import task_sentence
from lingsuo.window_layout import initial_size

PERFORMANCE={}

class UXCoreTests(Base):
    def test_permission_view_deduplicates_paths_and_grants_stay_independent(self):
        cfg=self.app.config.get();grants={k:k=="manage" for k in KINDS}
        cfg=apply_row(cfg,self.shared,grants,self.shared);self.app.save_config(cfg)
        self.assertEqual(len(rows(cfg,self.shared)),1);self.assertFalse(cfg["files"]["allow_desktop"])
        self.assertFalse(cfg["watch"]["enabled"]);self.assertEqual(cfg["file_manager"]["roots"],[str(self.shared)])
        self.assertFalse(cfg["inbound"]["enabled"]);self.assertFalse(cfg["qq"]["allowed_users"])
        sample=self.sample()
        with self.assertRaises(PermissionError):self.app.files.fingerprint(sample)

    def test_private_precedence_and_removal_keep_runtime_enforcement(self):
        cfg=self.app.config.get();cfg=apply_row(cfg,self.shared,{k:k in {"qq","private","observe"} for k in KINDS},self.shared)
        self.app.save_config(cfg);self.assertTrue(rows(cfg,self.shared)[0]["qq_blocked"])
        with self.assertRaises(PermissionError):self.app.files.fingerprint(self.sample())
        cfg=apply_row(cfg,self.shared,{k:False for k in KINDS},self.shared,remove=True);self.app.save_config(cfg)
        self.assertEqual(rows(cfg,self.shared),[])
        private=self.shared/"private";private.mkdir()
        cfg=self.app.config.get();cfg["files"]["roots"]=[str(self.shared)];cfg["local_data"]["roots"]=[str(private)]
        root_row=next(r for r in rows(cfg,self.shared) if r["path"]==str(self.shared))
        self.assertTrue(root_row["qq_partial"]);self.assertFalse(root_row["qq_blocked"])

    def test_receiver_is_single_directory_and_does_not_enable_or_share(self):
        cfg=apply_row(self.app.config.get(),self.shared,{k:k=="receive" for k in KINDS},self.shared)
        self.assertEqual(cfg["inbound"]["directory"],str(self.shared));self.assertFalse(cfg["inbound"]["enabled"])
        self.assertFalse(cfg["files"]["allow_desktop"])
        other=self.root/"other";other.mkdir()
        with self.assertRaises(ValueError):apply_row(cfg,other,{k:k=="receive" for k in KINDS},self.shared)

    def test_c2c_hour_window_and_atomic_message_sequences_do_not_reset(self):
        store=self.app.store;store.remember_reply("alice","scope","one")
        with store.lock,store.db:store.db.execute("UPDATE reply_messages SET ts=?",(time.time()-600,))
        with ThreadPoolExecutor(max_workers=8) as pool:replies=list(pool.map(lambda _:store.reserve_reply("alice","scope","one"),range(12)))
        self.assertEqual(sorted(r[1] for r in replies if r),[1,2,3,4,5])
        store.remember_reply("alice","scope","one");self.assertIsNone(store.reserve_reply("alice","scope","one"))
        store.remember_reply("alice","scope","two");self.assertEqual(store.reserve_reply("alice","scope")[1],1)
        with store.lock,store.db:store.db.execute("UPDATE reply_messages SET ts=?",(time.time()-3601,))
        self.assertIsNone(store.reserve_reply("alice","scope"));self.assertIsNone(store.reserve_reply("bob","scope"))

    def test_file_and_reply_use_distinct_sequences_and_current_request_precedes_queue(self):
        self.config(True);sample=self.sample(data=b"fixture");calls=[]
        def http(method,url,body,headers,timeout):
            payload=json.loads(body) if method=="POST" else {}
            if "getAppAccessToken" in url:return {"access_token":"fixture","expires_in":7200}
            if url.endswith("upload_prepare"):return {"upload_id":"fixture","block_size":16,"parts":[{"index":1,"presigned_url":"https://upload.example/1"}]}
            if url.endswith("/files"):return {"file_info":"fixture"}
            if url.endswith("/messages"):calls.append(payload);return {"id":"fixture-receipt-"+str(len(calls))}
            return {}
        self.app.transport=QQTransport(self.app.config,http=http,sleep=lambda _:None);self.app.gateway.connected=True
        self.app.commands.select_file("alice",self.app.files.fingerprint(sample))
        self.app.store.enqueue("alice","older alert","old",0)
        original=self.app.commands.handle
        def handle(*args):self.app.flush_once();self.assertFalse(calls);return original(*args)
        with patch.object(self.app.commands,"handle",side_effect=handle):self.app.receive("alice","发送","fixture-send")
        self.assertEqual([p["msg_seq"] for p in calls],[2,1]);self.assertEqual([p["msg_type"] for p in calls],[7,0])
        self.app.flush_once();self.assertEqual(calls[-1]["msg_seq"],3)
        self.app.receive("alice","收到了","fixture-received");self.assertTrue(next(r for r in self.app.tasks.recent("alice") if r["kind"]=="qq.file.send")["received"])

    def test_revoke_during_upload_can_save_and_stops_later_submission(self):
        self.config(True);sample=self.sample();fp=self.app.files.fingerprint(sample)
        self.app.store.remember_reply("alice",self.app.qq_scope(self.app.config.get()["qq"]),"fixture")
        transport=QQTransport(self.app.config,http=lambda *a:self.fail("no real HTTP"));self.app.transport=transport
        def send(user,path,name,message_id,context,sequence):
            self.app.save_section("files",{"roots":[]});context.check()
            self.fail("revoked upload must stop")
        with patch.object(transport,"send_file",side_effect=send):
            with self.assertRaises(PermissionError):self.app.send_file("alice",fp)
        self.assertEqual(self.app.tasks.recent("alice")[0]["state"],"failed")

    def test_dpapi_roundtrip_app_binding_and_no_plaintext_on_disk(self):
        self.config(True);raw=self.app.config.path.read_text(encoding="utf-8")
        self.assertNotIn("fake-secret",raw);self.assertTrue(json.loads(raw)["qq"]["secret"].startswith(PREFIX))
        restored=Config(self.app.directory);self.assertEqual(restored.get()["qq"]["secret"],"fake-secret")
        blob=protect("fixture only","app-one");self.assertEqual(unprotect(blob,"app-one"),"fixture only")
        with self.assertRaises(CredentialError):unprotect(blob,"app-two")

    def test_legacy_secret_migration_and_locked_blob_preservation(self):
        cfg=self.config(True);self.app.config.path.write_text(json.dumps(cfg),encoding="utf-8")
        migrated=Config(self.app.directory);self.assertNotIn("fake-secret",migrated.path.read_text(encoding="utf-8"))
        blob=json.loads(migrated.path.read_text(encoding="utf-8"))["qq"]["secret"]
        with patch("lingsuo.config.unprotect",side_effect=CredentialError("fixture locked")):locked=Config(self.app.directory)
        self.assertFalse(locked.get()["qq"]["enabled"]);self.assertTrue(locked.secret_error)
        value=locked.get();value["monitor"]["interval"]=7;locked.save(value)
        self.assertEqual(json.loads(locked.path.read_text(encoding="utf-8"))["qq"]["secret"],blob)
        self.assertEqual(Config(self.app.directory).get()["qq"]["secret"],"fake-secret")

    def test_protection_failure_preserves_previous_configuration(self):
        self.config(True);before=self.app.config.path.read_bytes();cfg=self.app.config.get();cfg["qq"]["secret"]="replacement-fixture"
        with patch("lingsuo.config.protect",side_effect=CredentialError("fixture unavailable")):
            with self.assertRaises(CredentialError):self.app.config.save(cfg)
        self.assertEqual(self.app.config.path.read_bytes(),before)

    def test_backup_limit_refuses_before_moving_and_history_names_are_real(self):
        self.app.save_section("file_manager",{"roots":[str(self.shared)]});p=self.sample();plan=self.app.file_manager.preview([(p,self.shared/"new.txt")])
        with patch.object(self.app.file_manager,"backup_usage",return_value=512*1024**2):
            with self.assertRaises(ValueError):self.app.tasks.run("files.apply","",lambda c:self.app.file_manager.execute(plan["id"],c))
        self.assertTrue(p.exists());self.assertFalse((self.shared/"new.txt").exists())

    def test_stale_monitor_status_is_unknown_and_not_a_false_exit(self):
        self.app.monitor.collector=lambda:[{"pid":1,"name":"sample.exe"}];self.app.monitor.tick()
        self.app.monitor.sample_time=time.time()-60
        self.assertIn("过期",self.app.status()["monitor_error"]);self.assertFalse(self.app.store.history())

    def test_restore_signal_is_scoped_and_auto_consumed_using_fake_api(self):
        class Api:
            flag=False
            def CreateEventW(self,*args):self.name=args[-1];return 42
            def OpenEventW(self,*args):return 43 if args[-1]==self.name else 0
            def SetEvent(self,handle):self.flag=True;return 1
            def WaitForSingleObject(self,*args):value=0 if self.flag else 258;self.flag=False;return value
            def CloseHandle(self,handle):return 1
        api=Api();signal=RestoreSignal(self.app.directory,api=api)
        self.assertFalse(signal.consume());self.assertTrue(request_restore(self.app.directory,api=api));self.assertTrue(signal.consume());self.assertFalse(signal.consume())
        self.assertNotEqual(event_name(self.shared),event_name(self.app.directory));signal.close()

    def test_duplicate_launch_requests_restore_without_a_second_runtime(self):
        from lingsuo.app import main
        with patch("lingsuo.app.acquire_lock",side_effect=RuntimeError("existing")),patch("lingsuo.app.request_restore",return_value=True) as restore,patch("lingsuo.app.Runtime") as runtime:
            self.assertEqual(main(["--data-dir",str(self.app.directory)]),0);restore.assert_called_once();runtime.assert_not_called()

    def test_small_screen_geometry_and_honest_result_phrases(self):
        w,h=initial_size(1092,614);self.assertLessEqual(w,1092);self.assertLessEqual(h+70,614)
        self.assertEqual(initial_size(1920,1040),(1150,800))
        row={"received":None,"state":"platform_accepted","result":"{}","step":"","error":""}
        self.assertIn("尚未确认",task_sentence(row));row["state"]="uncertain";self.assertIn("避免重复",task_sentence(row))


class UXDesktopTests(GuidanceBase):
    def setUp(self):
        super().setUp();self.root=tk.Tk();self.root.withdraw();self.confirm=MagicMock(return_value=True)
        self.destroyed=False
        self.root.bind("<Destroy>",lambda event:setattr(self,"destroyed",True) if event.widget is self.root else None)
        self.window=DesktopApp(self.root,self.app,tray=False,confirm=self.confirm,open_url=MagicMock(),draft_choice=lambda _:"discard")
        self.window.show_error=MagicMock();self.addCleanup(self.finish)

    def pump(self,predicate=None):
        end=time.monotonic()+5
        while time.monotonic()<end:
            self.root.update()
            if predicate() if predicate else not self.window.busy:
                if predicate is None:self.window.refresh()
                return
            time.sleep(.005)
        self.fail("experience fixture timed out")

    def click(self,key):self.window.buttons[key].invoke();self.pump()

    def finish(self):
        self.window.draft_choice=lambda _:"discard";self.window.close();self.pump(lambda:self.destroyed)

    def test_seven_navigation_groups_six_settings_and_child_features_remain(self):
        self.assertEqual(len(self.window.nav),7);self.assertEqual(len(self.window.settings_cards),6)
        self.window.show("files");self.assertEqual(self.window.current_page(),"files")
        self.window.nav["plugins"].invoke();self.assertEqual(self.window.current_page(),"plugins")
        self.assertTrue(self.window.buttons["tool_image"].winfo_exists());self.assertTrue(self.window.buttons["setting_webtasks"].winfo_exists())

    def test_window_size_is_fixed_and_overview_has_no_scroll_strip(self):
        self.assertEqual(self.window.root.resizable(),(0,0))
        home=self.window.pages["home"]
        self.assertFalse(any(child.winfo_class()=="Canvas" for child in home.winfo_children()))

    def test_returning_to_a_page_starts_at_the_top(self):
        self.window.show("settings"); self.pump()
        canvas=next(iter(self.window.scroll_frames.values()),None)
        self.assertIsNotNone(canvas)
        canvas.yview_moveto(0.6); self.pump()
        self.window.show("home"); self.pump()
        self.window.show("settings"); self.pump()
        self.assertEqual(canvas.yview()[0],0.0)

    def test_qq_navigation_and_background_refresh_preserve_drafts(self):
        self.window.vars["appid"].set("draft-app");self.window.vars["secret"].set("draft-secret");self.window.vars["qq_openids"].set("draft-owner")
        self.window.show("monitor");self.window.open_notifications();self.window.refresh()
        self.assertEqual(self.window.vars["appid"].get(),"draft-app");self.assertEqual(self.window.vars["secret"].get(),"draft-secret")
        self.click("qq_cancel");self.assertFalse(self.window.qq_dirty())

    def test_dialog_continue_discard_and_save_preserve_configuration(self):
        self.click("setting_advanced");old=self.window.active_dialog;self.window.vars["interval"].set("9")
        self.window.draft_choice=lambda _:"continue";self.click("setting_appearance")
        self.assertIs(self.window.active_dialog,old);self.assertEqual(self.window.vars["interval"].get(),"9")
        self.window.draft_choice=lambda _:"save";self.click("advanced_done")
        self.assertEqual(self.app.config.get()["monitor"]["interval"],9);self.assertTrue(old.winfo_exists())
        self.click("setting_appearance");self.assertFalse(old.winfo_exists());self.assertEqual(self.window.active_dialog.title(),"外观皮肤")

    def test_dialog_failed_save_keeps_values_and_does_not_mark_saved(self):
        self.click("setting_advanced");old=self.window.active_dialog;self.window.vars["interval"].set("11")
        with patch.object(self.app,"save_config",side_effect=OSError("fixture write failure")):self.click("advanced_save")
        self.window.draft_choice=lambda _:"continue";self.click("advanced_done")
        self.assertTrue(old.winfo_exists());self.assertEqual(self.window.vars["interval"].get(),"11")

    def test_quick_pairing_uses_local_confirmation_not_first_sender(self):
        self.window.vars["appid"].set("fake-app");self.window.vars["secret"].set("fake-secret")
        self.click("quick_connect");self.assertTrue(self.gateway.connected)
        self.click("quick_pair_start");self.app.receive("stranger","开启","stranger-request");self.app.receive("owner","开启","owner-request")
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"],[]);self.window.refresh()
        request=next(r for r in self.app.pairing.status()["requests"] if r["user"]=="owner")
        self.window.pairing_tree.selection_set(request["id"]);self.confirm.return_value=False;self.click("quick_pair_confirm")
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"],[])
        self.confirm.return_value=True;self.click("quick_pair_confirm")
        self.assertEqual(self.app.config.get()["qq"]["allowed_users"],["owner"]);self.assertFalse(self.window.qq_dirty())
        self.assertFalse(self.app.files.roots());self.assertFalse(self.app.config.get()["inbound"]["enabled"])

    def test_permission_matrix_changes_only_selected_use_and_requires_grant_confirmation(self):
        self.click("setting_permissions");self.window.permission_path.set(str(self.shared));self.window.permission_values["manage"].set(True)
        self.confirm.return_value=False;self.click("permissions_save");self.assertFalse(self.app.config.get()["file_manager"]["roots"])
        self.confirm.return_value=True;self.click("permissions_save")
        self.assertEqual(self.app.config.get()["file_manager"]["roots"],[str(self.shared)])
        self.assertFalse(self.app.files.roots());self.assertFalse(self.app.config.get()["watch"]["enabled"])

    def test_long_job_does_not_block_navigation_or_task_detail(self):
        started=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def wait():started.set();release.wait(4)
        self.window._active_action_key="long-fixture";self.window.submit(wait,resource="file-send");self.window._active_action_key=None
        self.assertTrue(started.wait(1));self.window.show("settings");self.assertEqual(self.window.current_page(),"settings")
        self.window._active_action_key="read-fixture";self.assertTrue(self.window.submit(lambda:1,resource="read-processes"));self.window._active_action_key=None
        self.assertFalse(self.window.submit(lambda:None,resource="file-send"));release.set();self.pump()

    def test_periodic_state_read_runs_off_tk_and_does_not_refresh_hidden_tables(self):
        called=[];main=threading.get_ident();original=self.window.load_snapshot
        def load():called.append(threading.get_ident());return original()
        with patch.object(self.window,"load_snapshot",side_effect=load):
            self.window.request_snapshot();self.pump(lambda:self.window.snapshot_future is None)
        self.assertTrue(called);self.assertTrue(all(i!=main for i in called))
        self.window.show("home")
        with patch.object(self.window,"fill",wraps=self.window.fill) as fill:
            self.window.refresh(self.window.last_snapshot)
            self.assertFalse(any(call.args[0] is self.window.task_tree for call in fill.call_args_list))

    def test_row_click_outside_checkbox_changes_group_selection(self):
        self.window.processes_loaded([{"name":"fixture.exe","pid":1},{"name":"fixture.exe","pid":2}])
        with patch.object(self.window.process_tree,"identify_row",return_value="1"):
            self.window.process_checkbox(type("Event",(),{"x":300,"y":20})())
        self.assertIn("fixture.exe",self.window.chosen)
        self.assertTrue(self.window.process_tree.item("1","image"));self.assertTrue(self.window.process_tree.item("2","image"))

    def test_snapshot_from_before_completed_change_is_not_rendered(self):
        self.window.request_snapshot();self.window.snapshot_future.result(timeout=2)
        self.window.state_epoch+=1
        with patch.object(self.window,"refresh") as render:
            self.root.after_cancel(self.window.poll_id);self.window._poll()
            render.assert_not_called()

    def test_thousand_synthetic_process_rows_have_measured_responsive_filter(self):
        self.window.set_process_filter("all")
        self.window.processes_loaded([{"name":"fixture%s.exe"%i,"pid":i+1,"has_window":True,"memory":"未知"} for i in range(1000)])
        values=[]
        for _ in range(12):
            start=time.perf_counter();self.window.filter_processes();values.append((time.perf_counter()-start)*1000)
        p95=sorted(values)[-1]
        PERFORMANCE.update(rows=1000,samples_ms=values,filter_max_ms=p95,filter_median_ms=statistics.median(values),scope="synthetic warm Treeview filtering only; not total application latency")
        self.assertLess(p95,100,"synthetic filtering exceeded target; report actual measurement")
