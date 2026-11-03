"""Correctness regressions for all eight audited defects; temporary data only."""
import json
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import types
import unittest
from unittest.mock import MagicMock,patch
PROJECT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(PROJECT),str(PROJECT/"tests")]
from test_core import Base
from test_guidance import GuidanceBase
from lingsuo.desktop import DesktopApp
from lingsuo.observers import AnomalyObserver
from lingsuo.diagnostics import snapshot as diagnostics
from lingsuo.tasks import TaskCancelled

EVIDENCE={}
class RepairDesktopTests(GuidanceBase):
    def test_removed_mode_preferences_are_not_loaded_and_never_register_hotkey(self):
        w=self.window;w.hotkey_factory=MagicMock(side_effect=AssertionError("must not register"))
        w.automation_preferences["repeat"]={"click_hotkey":"F6","click_interval":"100"}
        with self.assertRaises(ValueError):w.automation_dialog("repeat")
        self.assertIsNone(self.app.automation.thread);w.hotkey_factory.assert_not_called()
        w.automation_dialog("image")
        self.assertEqual(w.vars["click_hotkey"].get(),"")

    def setUp(self):
        super().setUp();self.tk=tk.Tk();self.tk.withdraw();self.destroyed=False;self.release=[]
        self.tk.bind("<Destroy>",lambda event:setattr(self,"destroyed",True) if event.widget is self.tk else None)
        self.window=DesktopApp(self.tk,self.app,tray=False,confirm=lambda *a:True,open_url=MagicMock(),draft_choice=lambda _:"discard")
        self.window.show_error=MagicMock();self.addCleanup(self.finish)
    def pump(self,predicate):
        end=time.monotonic()+5
        while time.monotonic()<end:
            self.tk.update()
            if predicate():return
            time.sleep(.005)
        self.fail("probe timed out")
    def finish(self):
        for event in self.release:event.set()
        if not self.destroyed:
            self.window.draft_choice=lambda _:"discard";self.window.close();self.pump(lambda:self.destroyed)
    def barrier(self):
        entered=threading.Event();release=threading.Event();self.release.append(release);return entered,release
    def test_fixed_new_edit_marked_saved_after_older_save_finishes(self):
        w=self.window;w.buttons["setting_advanced"].invoke();self.tk.update();dialog=w.active_dialog
        entered,release=self.barrier();original=self.app.save_config
        def save(cfg):entered.set();release.wait(4);return original(cfg)
        with patch.object(self.app,"save_config",side_effect=save):
            w.vars["interval"].set("7");w.buttons["advanced_save"].invoke();self.assertTrue(entered.wait(1))
            w.vars["interval"].set("11");release.set();self.pump(lambda:not w.busy)
        choice=MagicMock(return_value="continue");w.draft_choice=choice
        closed_without_warning=w.allow_close_dialog(dialog)
        EVIDENCE["B1"]={"config_interval":self.app.config.get()["monitor"]["interval"],"visible_interval":w.vars["interval"].get(),"close_allowed_without_prompt":closed_without_warning,"draft_prompt_calls":choice.call_count}
        self.assertEqual(EVIDENCE["B1"]["config_interval"],7);self.assertEqual(w.vars["interval"].get(),"11");self.assertFalse(closed_without_warning);choice.assert_called_once()
    def test_fixed_qq_secret_typed_while_save_inflight_is_erased(self):
        w=self.window;w.vars["appid"].set("fake-app");w.vars["secret"].set("first-fixture-secret")
        entered,release=self.barrier();original=self.app.save_section
        def save(*args):entered.set();release.wait(4);return original(*args)
        with patch.object(self.app,"save_section",side_effect=save):
            w.buttons["quick_only_save"].invoke();self.assertTrue(entered.wait(1))
            w.vars["secret"].set("new-unsaved-fixture-secret");release.set();self.pump(lambda:not w.busy)
        EVIDENCE["B2"]={"new_secret_draft_preserved":bool(w.vars["secret"].get()),"form_dirty":w.qq_dirty(),"saved_first_value":self.app.config.get()["qq"]["secret"]=="first-fixture-secret"}
        self.assertEqual(w.vars["secret"].get(),"new-unsaved-fixture-secret");self.assertTrue(w.qq_dirty());self.assertTrue(EVIDENCE["B2"]["saved_first_value"])
    def test_fixed_exit_skips_dirty_non_qq_dialog(self):
        w=self.window;w.buttons["setting_advanced"].invoke();self.tk.update();w.vars["interval"].set("13")
        choice=MagicMock(return_value="continue");w.draft_choice=choice;w.close()
        EVIDENCE["B3"]={"exit_started":w.closing,"draft_prompt_calls":choice.call_count,"saved_value":self.app.config.get()["monitor"]["interval"]}
        self.assertFalse(w.closing);choice.assert_called_once()
    def test_fixed_actual_native_send_uses_settings_resource(self):
        self.config(True);self.gateway.connected=True;w=self.window;sample=self.sample()
        w.file_choice=self.app.files.fingerprint(sample);w.file_rows=[w.file_choice];w.vars["file_user"].set("alice")
        info=sample.stat();w.manager_rows=[{"name":sample.name,"path":str(sample),"size":info.st_size,"mtime":info.st_mtime}]
        w.fill(w.manager_tree,[("0",(sample.name,"1.0 KB","now"))]);w.manager_tree.selection_set("0")
        entered,release=self.barrier()
        def send(*args,**kwargs):entered.set();release.wait(4);return {}
        with patch.object(self.app,"send_file",side_effect=send):
            w.buttons["file_send_selected"].invoke();self.pump(lambda:entered.is_set())
            held=sorted(w.resources);keys=dict(w.pending_actions)
            w._active_action_key="permissions_save"
            accepted=w.submit(lambda:None);w._active_action_key=None
            EVIDENCE["B4"]={"resources_during_upload":held,"pending_keys":keys,"independent_settings_submission_accepted":accepted}
            self.assertEqual(held,["file-send"]);self.assertTrue(accepted);self.assertTrue(w.buttons["file_send_selected"].disabled)
            release.set();self.pump(lambda:not w.busy)
    def test_fixed_choose_other_folder_discards_permission_edits(self):
        w=self.window;w.buttons["setting_permissions"].invoke();self.tk.update()
        w.permission_path.set(str(self.shared));w.permission_values["manage"].set(True)
        choice=MagicMock(return_value="continue");w.draft_choice=choice
        other=self.root/"other";other.mkdir()
        with patch("lingsuo.desktop_permissions.filedialog.askdirectory",return_value=str(other)):w.buttons["permissions_add"].invoke()
        EVIDENCE["B5"]={"switched_folder":w.permission_path.get()==str(other),"previous_manage_draft":w.permission_values["manage"].get(),"draft_prompt_calls":choice.call_count}
        self.assertEqual(w.permission_path.get(),str(self.shared));self.assertTrue(w.permission_values["manage"].get());choice.assert_called_once()

    def test_records_query_controls_reach_old_failure_and_show_total(self):
        old=self.app.tasks.create("documents.save");self.app.tasks.update(old,state="failed")
        for _ in range(35):
            ident=self.app.tasks.create("documents.save");self.app.tasks.update(ident,state="completed")
        w=self.window;w.show("tasks");w.refresh()
        self.assertIn(old,w.task_tree.get_children());self.assertEqual(w.record_total,36)
        w.page_records(1);w.refresh();self.assertEqual(len(w.task_tree.get_children()),6)
        w.task_search.set(old);w.filter_records();w.refresh()
        self.assertEqual(w.task_tree.get_children(),(old,));self.assertIn("共 1 条",w.task_page_note.get())

    def test_qq_steps_require_real_connection_and_local_binding(self):
        w=self.window;self.assertEqual(w.qq_stage_index,0)
        with self.assertRaises(ValueError):w.qq_next()
        self.config(True);self.gateway.connected=True;w.reset_qq_form();w.qq_next();self.assertEqual(w.qq_stage_index,1)
        w.qq_next();self.assertEqual(w.qq_stage_index,2)

    def test_file_page_keeps_scope_visible_without_folding(self):
        w=self.window
        self.assertTrue(w.manager_tree.winfo_manager())
        self.assertTrue(w.folder_menu.winfo_manager())
        self.assertEqual(w.file_note.get(),"还没有授权目录")
        self.assertFalse(w.selbar_shown)






    def test_export_diagnostics_only_writes_explicit_new_temp_path(self):
        w=self.window;w.buttons["setting_diagnostics"].invoke();path=self.root/"diagnostic.json"
        with patch("lingsuo.desktop.filedialog.asksaveasfilename",return_value=str(path)):
            w.buttons["export_diagnostics"].invoke();self.pump(lambda:not w.busy)
        value=json.loads(path.read_text(encoding="utf-8"));self.assertIn("revision",value);self.assertNotIn(str(self.app.directory),path.read_text(encoding="utf-8"))

    def test_grouped_process_view_expands_instances_and_indicator_does_not_toggle(self):
        w=self.window;w.set_process_filter("all");w.processes_loaded([{"name":"fixture.exe","pid":1},{"name":"fixture.exe","pid":2}])
        w.process_view.set("按程序聚合");w.filter_processes()
        parent="program:fixture.exe";self.assertEqual(w.process_tree.get_children(parent),("1","2"))
        with patch.object(w.process_tree,"identify_element",return_value="Treeitem.indicator"):
            w.process_checkbox(types.SimpleNamespace(x=3,y=2))
        self.assertFalse(w.chosen);w.toggle_process(parent);self.assertIn("fixture.exe",w.chosen)
        w.process_view.set("逐个实例");w.filter_processes();self.assertEqual(w.process_tree.get_children(),("1","2"))

    def test_grouped_expansion_survives_view_switch_and_rebuild(self):
        w=self.window;w.set_process_filter("all");w.processes_loaded([{"name":"fixture.exe","pid":1},{"name":"fixture.exe","pid":2}])
        w.process_view.set("按程序聚合");w.filter_processes();parent="program:fixture.exe"
        w.process_tree.focus(parent);w.remember_group_open();self.assertIn("fixture.exe",w.expanded_groups)
        w.process_view.set("逐个实例");w.filter_processes()
        w.process_view.set("按程序聚合");w.filter_processes()
        self.assertTrue(w.process_tree.item(parent,"open"))
        w.process_tree.focus(parent);w.remember_group_close()
        w.process_view.set("逐个实例");w.filter_processes()
        w.process_view.set("按程序聚合");w.filter_processes()
        self.assertFalse(w.process_tree.item(parent,"open"))

    def test_existing_folder_picker_loads_saved_grants_instead_of_resetting_them(self):
        self.config();w=self.window;w.buttons["setting_permissions"].invoke();self.tk.update()
        with patch("lingsuo.desktop_permissions.filedialog.askdirectory",return_value=str(self.shared)):w.buttons["permissions_add"].invoke()
        self.assertTrue(w.permission_values["qq"].get());self.assertFalse(w.permission_values["manage"].get())

    def test_oversized_record_search_does_not_poison_background_state_queries(self):
        w=self.window;before=dict(w.record_query);w.task_search.set("x"*201);w.buttons["task_search"].invoke()
        w.show_error.assert_called_once();self.assertEqual(w.record_query,before)
        self.assertIn("task_page",w.load_snapshot())

class RepairCoreTests(Base):
    def test_fixed_stale_snapshot_still_counts_absence_alert(self):
        cfg=self.config();cfg["monitor"]["process_names"]=["fixture.exe"];cfg["anomalies"].update(enabled=True,absence_seconds=2)
        self.app.config.save(cfg);mon=self.app.monitor;mon.last=[];mon.seen_names={"fixture.exe"};mon.sample_time=time.time()-100;mon.error=""
        events=[];observer=AnomalyObserver(self.app.config,mon,lambda *e:events.append(e),types.SimpleNamespace(sample=lambda _:{}))
        observer.tick(10);observer.tick(12)
        EVIDENCE["B6"]={"display_reports_stale":"过期" in self.app.status()["monitor_error"],"emitted_alert_despite_stale_snapshot":any(e[0]=="anomaly.alert" for e in events),"observer_error":observer.error}
        self.assertTrue(EVIDENCE["B6"]["display_reports_stale"]);self.assertFalse(EVIDENCE["B6"]["emitted_alert_despite_stale_snapshot"]);self.assertTrue(observer.error)
    def test_fixed_same_pid_different_program_transition_is_missed(self):
        cfg=self.config();cfg["monitor"]["process_names"]=["old.exe","new.exe"];self.app.config.save(cfg)
        events=[];mon=self.app.monitor;mon.emit=lambda *e:events.append(e)
        mon.collector=lambda:[{"pid":99,"name":"old.exe"}];mon.tick()
        mon.collector=lambda:[{"pid":99,"name":"new.exe"}];mon.tick()
        EVIDENCE["B7"]={"program_changed_under_same_pid":True,"events_emitted":len(events),"current_name":mon.last[0]["name"]}
        self.assertEqual([e[0] for e in events],["process.exit","process.start"]);self.assertEqual(mon.last[0]["name"],"new.exe")
    def test_fixed_attention_filter_cannot_see_old_failure_outside_recent_30(self):
        tasks=self.app.tasks;old=tasks.create("fixture.old.failure","");tasks.update(old,state="failed",error="fixture error")
        for _ in range(35):
            ident=tasks.create("fixture.completed","");tasks.update(ident,state="completed")
        recent=tasks.query(attention=True)["rows"]
        EVIDENCE["B8"]={"old_failure_still_in_database":tasks.get(old)["state"]=="failed","default_query_rows":len(recent),"old_failure_visible_to_ui_sort":any(r["id"]==old for r in recent)}
        self.assertTrue(EVIDENCE["B8"]["old_failure_visible_to_ui_sort"]);self.assertTrue(EVIDENCE["B8"]["old_failure_still_in_database"])

    def test_task_query_enforces_owner_states_literal_search_and_dates(self):
        alice=self.app.tasks.create("fixture.%name","alice");bob=self.app.tasks.create("fixture.other","bob")
        self.app.tasks.update(alice,state="failed");self.app.tasks.update(bob,state="failed")
        self.assertEqual([r["id"] for r in self.app.tasks.query(owner="alice",states=["failed"],text="%name")["rows"]],[alice])
        self.assertFalse(self.app.tasks.query(owner="alice",text=bob)["rows"])
        self.assertFalse(self.app.tasks.query(owner="alice",since=time.time()+1)["rows"])
        with self.assertRaises(ValueError):self.app.tasks.query(limit=100000)
        with self.assertRaises(ValueError):self.app.tasks.query(states=["invalid"])

    def restored_fixture(self):
        self.app.save_section("file_manager",{"roots":[str(self.shared)]});src=self.sample();plan=self.app.file_manager.preview([(src,self.shared/"renamed.txt")])
        manager=self.app.file_manager
        self.app.tasks.run("files.apply","",lambda ctx:manager.execute(plan["id"],ctx))
        self.app.tasks.run("files.recover","",lambda ctx:manager.recover(plan["id"],ctx))
        return manager,src,plan["id"]

    def test_cleanup_redundant_backup_preserves_locked_original(self):
        manager,src,ident=self.restored_fixture();expected=src.read_bytes();preview=manager.cleanup_preview(ident)
        class Context:
            def progress(self,*args):
                with self_outer.assertRaises(PermissionError):src.write_bytes(b"must not write while cleanup holds original")
        self_outer=self
        manager.cleanup(preview,Context())
        self.assertEqual(src.read_bytes(),expected);self.assertEqual(manager.backup_usage(),0);self.assertEqual(manager.details(ident)["state"],"cleaned")
        with self.assertRaises(ValueError):manager.recover(ident,types.SimpleNamespace(progress=lambda *args:None))

    def test_cleanup_rejects_original_changes_and_preserves_backup(self):
        manager,src,ident=self.restored_fixture();preview=manager.cleanup_preview(ident);before=manager.backup_usage();src.write_bytes(b"new original")
        with self.assertRaises(ValueError):manager.cleanup(preview,types.SimpleNamespace(progress=lambda *args:None))
        self.assertEqual(manager.backup_usage(),before);self.assertEqual(src.read_bytes(),b"new original")

    def test_cleanup_requires_restored_record_and_rechecks_authorization(self):
        self.app.save_section("file_manager",{"roots":[str(self.shared)]});src=self.sample();manager=self.app.file_manager
        plan=manager.preview([(src,self.shared/"moved.txt")]);self.app.tasks.run("files.apply","",lambda c:manager.execute(plan["id"],c))
        with self.assertRaises(ValueError):manager.cleanup_preview(plan["id"])
        self.app.tasks.run("files.recover","",lambda c:manager.recover(plan["id"],c));preview=manager.cleanup_preview(plan["id"])
        self.app.save_section("file_manager",{"roots":[]})
        with self.assertRaises(PermissionError):manager.cleanup(preview,types.SimpleNamespace(progress=lambda *args:None))
        self.assertGreater(manager.backup_usage(),0)

    def test_cleanup_cancelled_before_delete_can_be_rechecked_and_resumed(self):
        manager,src,ident=self.restored_fixture();before=src.read_bytes()
        def cancel(*args):raise TaskCancelled("fixture cancel")
        with self.assertRaises(TaskCancelled):manager.cleanup(manager.cleanup_preview(ident),types.SimpleNamespace(progress=cancel))
        self.assertGreater(manager.backup_usage(),0);self.assertEqual(src.read_bytes(),before)
        manager.cleanup(manager.cleanup_preview(ident),types.SimpleNamespace(progress=lambda *a:None));self.assertEqual(manager.backup_usage(),0)

    def test_diagnostics_allowlist_does_not_include_fixture_identity_or_contents(self):
        self.config(True);self.app.store.event("test","sensitive-fixture-log-body");task=self.app.tasks.create("documents.save","alice")
        self.app.tasks.update(task,result=json.dumps({"text":"sensitive-fixture-document-body"}))
        encoded=json.dumps(diagnostics(self.app))
        for forbidden in ("fake-secret","fake-app","alice","bob",str(self.shared),"sensitive-fixture-log-body","sensitive-fixture-document-body"):
            self.assertNotIn(forbidden,encoded)

