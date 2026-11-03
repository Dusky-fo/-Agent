"""Temporary fixtures only; every external socket is forbidden by Base."""
import copy
import io
import json
import os
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from test_core import Base
from lingsuo.config import validate
from lingsuo.tasks import Tasks,TaskCancelled,TaskTimedOut
from lingsuo.qq import QQTransport,QQError,QQUncertain
from lingsuo.observers import FileLogObserver,AnomalyObserver,compile_patterns
from lingsuo.inbound import filename
from lingsuo.net import download,public_url
from lingsuo.tools import Caller,ToolExecutor,WebTasks


class TaskTests(Base):
    def test_success_owner_result_and_explicit_receipt(self):
        self.config(True);p=self.sample()
        row=self.app.send_file("alice",self.app.files.fingerprint(p),"msg")
        self.assertEqual(row["state"],"platform_accepted");self.assertIsNone(row["received"])
        with self.assertRaises(PermissionError):self.app.tasks.get(row["id"],"bob")
        self.app.commands.handle("alice","收到任务 "+row["id"],"m2")
        self.assertTrue(self.app.tasks.get(row["id"])["received"])

    def test_cancel_and_timeout_preserve_distinct_states(self):
        def cancelled(c):self.app.tasks.cancel(c.id);c.check()
        with self.assertRaises(TaskCancelled):self.app.tasks.run("test.cancel","",cancelled)
        self.assertEqual(self.app.tasks.recent()[0]["state"],"cancelled")
        def late(c):c.deadline=0;c.check()
        with self.assertRaises(TaskTimedOut):self.app.tasks.run("test.timeout","",late)
        self.assertEqual(self.app.tasks.recent()[0]["state"],"timed_out")

    def test_restart_marks_unfinished_but_does_not_reexecute(self):
        ident=self.app.tasks.create("test.interruption");Tasks(self.app.store)
        self.assertEqual(self.app.tasks.get(ident)["state"],"interrupted")

    def test_qq_cancel_does_not_wait_for_same_user_file_command_lock(self):
        self.config(True);ident=self.app.tasks.create("qq.file.send","alice");self.app.tasks.update(ident,state="running")
        lock=threading.Lock();self.app.commands.locks["alice"]=lock;lock.acquire();answers=[]
        thread=threading.Thread(target=lambda:answers.append(self.app.commands.handle("alice","取消任务 "+ident,"cancel-message")),daemon=True)
        try:thread.start();thread.join(1);self.assertFalse(thread.is_alive());self.assertTrue(answers)
        finally:
            lock.release();thread.join(1)
            self.app.tasks.update(ident,state="cancelled",step="fixture worker ended")

    def test_results_remain_valid_json_and_errors_hide_transport_bodies(self):
        task=self.app.tasks.run("test.result","",lambda c:{"large":"x"*(1024**2+1)})
        self.assertIn("summary",json.loads(task["result"]))
        with self.assertRaises(RuntimeError):self.app.tasks.run("test.error","",lambda c:(_ for _ in ()).throw(RuntimeError("secret-url")))
        self.assertNotIn("secret-url",self.app.tasks.recent()[0]["error"])

    def test_notification_tasks_share_outbox_and_cancel_and_receipt(self):
        self.config(True);self.app.gateway.connected=True
        ident=self.app.queue_test("alice");row=self.app.tasks.recent("alice")[0]
        self.assertTrue(row["id"].startswith("N"));self.assertEqual(row["state"],"queued")
        self.app.flush_once();self.assertEqual(self.app.tasks.get(row["id"])["state"],"platform_accepted")
        self.app.tasks.confirm_received(row["id"],"alice");self.assertTrue(self.app.status()["tests"][0]["confirmed"])
        self.app.store.enqueue("alice","next fixture notification","fixture.other",0);other=self.app.tasks.recent("alice")[0]
        self.app.tasks.cancel(other["id"],"alice");self.assertEqual(self.app.tasks.get(other["id"])["state"],"cancelled")


class ProtocolTests(Base):
    def http(self,method,url,body,headers,timeout):
        self.calls.append((method,url,json.loads(body) if method=="POST" else body))
        if "getAppAccessToken" in url:return {"access_token":"fixture","expires_in":7200}
        if url.endswith("upload_prepare"):return {"upload_id":"u","block_size":4,"parts":[{"index":1,"presigned_url":"https://upload.example/p"}]}
        if url.endswith("/files"):return {"file_info":"fixture-info"}
        if url.endswith("/messages"):return {"id":"fixture-message"}
        return {}

    def transport(self):self.config(True);self.calls=[];return QQTransport(self.app.config,http=self.http,sleep=lambda _:None)

    def test_real_protocol_numeric_fields_and_merge_and_receipt(self):
        transport=self.transport();result=transport.send_file("alice",self.sample(data=b"abcd"),"sample.txt","m")
        self.assertEqual(result["state"],"platform_accepted")
        prep=next(c[2] for c in self.calls if c[1].endswith("upload_prepare"));self.assertIs(type(prep["file_size"]),int)
        part=next(c[2] for c in self.calls if c[1].endswith("upload_part_finish"));self.assertEqual(part["part_index"],1);self.assertIs(type(part["block_size"]),int)
        self.assertEqual(next(c[2] for c in self.calls if c[1].endswith("/files")),{"upload_id":"u"})
        self.assertEqual(self.calls[-1][2]["content"]," ")

    def test_missing_receipt_or_lost_response_is_uncertain_without_repeat(self):
        transport=self.transport();original=self.http
        def missing(*args):return {} if args[1].endswith("/messages") else original(*args)
        transport.http=missing
        with self.assertRaises(QQUncertain):transport.send_file("alice",self.sample(data=b"abcd"),"f","m")
        with patch("urllib.request.OpenerDirector.open",side_effect=TimeoutError()):
            with self.assertRaises(QQUncertain):QQTransport._http("POST","https://api.bot.qq.com/v2/users/x/messages",b"{}",{},1)

    def test_part_finish_only_known_pending_error_retries(self):
        transport=self.transport();original=self.http;attempts=[]
        def retry(*args):
            if args[1].endswith("upload_part_finish"):
                attempts.append(1)
                if len(attempts)<3:return {"biz_code":40093001}
            return original(*args)
        transport.http=retry;transport.send_file("alice",self.sample(data=b"abcd"),"f","m");self.assertEqual(len(attempts),3)

    def test_no_recent_context_waits_and_expired_notification_times_out(self):
        self.config(True);self.app.gateway.connected=True;self.app.store.cancel_pending();self.app.queue_test("alice")
        self.app.flush_once();self.assertFalse(self.qq.texts)
        row=self.app.tasks.recent()[0];self.assertEqual(row["state"],"queued");self.assertIn("等待用户",row["error"])
        with self.app.store.lock,self.app.store.db:self.app.store.db.execute("UPDATE outbox SET created=0,due=0")
        self.app.flush_once();self.assertEqual(self.app.tasks.get(row["id"])["state"],"timed_out")

    def test_uncertain_notify_is_terminal_and_not_accepted(self):
        self.config(True);self.app.gateway.connected=True;self.app.queue_test("alice")
        with patch.object(self.qq,"send_text",side_effect=QQUncertain("uncertain")):
            self.app.flush_once();self.app.flush_once()
        row=self.app.tasks.recent()[0];self.assertEqual(row["state"],"uncertain");self.assertIsNone(self.app.status()["tests"][0]["accepted"])


class WatchTests(Base):
    def setup_watch(self):
        cfg=self.config();cfg["watch"].update(enabled=True,roots=[str(self.shared)],interval=1,merge_seconds=0)
        self.app.config.save(cfg);self.events=[];self.observer=FileLogObserver(self.app.config,self.app.directory,lambda *a:self.events.append(a))

    def test_baseline_changes_filter_pause_and_aggregation(self):
        self.setup_watch();p=self.sample();self.observer.tick(10);self.assertFalse(self.events)
        p.write_bytes(b"changed");self.sample("ignore.tmp");q=self.sample("new.txt");self.observer.tick(11)
        self.assertEqual(len(self.events),1);self.assertIn("修改 1",self.events[0][1]);self.assertIn("新增 1",self.events[0][1]);self.assertNotIn("ignore",self.events[0][1])
        q.unlink();self.observer.tick(12);self.assertIn("删除 1",self.events[-1][1])
        self.app.save_section("watch",{"enabled":False});self.sample("during.txt");self.observer.tick(13)
        self.app.save_section("watch",{"enabled":True});self.observer.tick(14);self.assertEqual(len(self.events),2)

    def test_unavailable_snapshot_keeps_baseline_not_deleted(self):
        self.setup_watch();self.sample();self.observer.tick(10)
        with patch.object(self.observer,"snapshot",side_effect=PermissionError()):self.observer.tick(11)
        self.assertTrue(self.observer.error);self.assertFalse(self.events);self.assertEqual(len(self.observer.snapshots),1)
        self.observer.tick(12);self.assertFalse(self.events)

    def test_log_incremental_utf8_rotation_and_copytruncate(self):
        self.setup_watch();p=self.sample("app.log",b"ERROR old\n")
        self.app.save_section("watch",{"roots":[],"logs":[str(p)]});self.observer.tick(10);self.assertFalse(self.events)
        with p.open("ab") as f:f.write("异常".encode()[:2])
        self.observer.tick(11);self.assertFalse(self.events)
        with p.open("ab") as f:f.write("异常".encode()[2:]+b"\n")
        self.observer.tick(12);self.assertIn("日志命中 1",self.events[-1][1]);self.assertNotIn("ERROR old",self.events[-1][1])
        p.rename(p.with_suffix(".old"));p.write_bytes(b"ERROR rotated\n");self.observer.tick(13);self.assertIn("日志轮转",self.events[-1][1])
        p.write_bytes(b"ERROR replacement with longer length\n");self.observer.tick(14);self.assertIn("日志命中 1",self.events[-1][1])

    def test_restricted_regex_and_runtime_private_scopes_rejected(self):
        for pattern in ("(a+)+$","a{2}","a|b",r"\1"):
            with self.assertRaises(ValueError):compile_patterns([pattern])
        self.assertTrue(compile_patterns([r"^ERR[0-9].$"]))
        with self.assertRaises(PermissionError):self.app.save_section("watch",{"roots":[str(self.app.directory)]})

    def test_missing_existing_watch_scope_does_not_block_unrelated_settings_or_pause(self):
        root=self.root/"observed";root.mkdir();self.app.save_section("watch",{"roots":[str(root)],"enabled":True})
        root.rmdir();self.app.save_section("notifications",{"cooldown":20})
        self.app.save_section("watch",{"enabled":False});self.assertFalse(self.app.config.get()["watch"]["enabled"])
        with self.assertRaises(ValueError):self.app.save_section("watch",{"roots":[],"enabled":True})

    def test_merge_waits_then_uses_existing_qq_queue(self):
        self.config(True);self.app.store.subscribe("alice",True)
        self.app.save_section("watch",{"enabled":True,"roots":[str(self.shared)],"merge_seconds":5,"interval":1})
        watcher=self.app.watch;watcher.tick(10);self.sample();watcher.tick(11);self.assertFalse(self.app.store.due())
        self.sample("second.txt");watcher.tick(16);self.assertEqual(len(self.app.store.due()),1);self.assertIn("新增 2",self.app.store.due()[0]["text"])


class AnomalyTests(Base):
    def prepare(self):
        cfg=self.config();cfg["monitor"]["process_names"]=["sample.exe"]
        cfg["anomalies"].update(enabled=True,sustain_seconds=2,absence_seconds=2,hang_seconds=2,memory_mb=10,cpu_percent=50)
        self.app.config.save(cfg);self.events=[];self.values={1:{"created":1,"cpu_seconds":0,"memory_mb":20,"hung":True}}
        self.app.monitor.last=[{"pid":1,"name":"sample.exe"}];self.app.monitor.seen_names={"sample.exe"};self.app.monitor.sample_time=time.time()
        self.observer=AnomalyObserver(self.app.config,self.app.monitor,lambda *e:self.events.append(e),types.SimpleNamespace(sample=lambda rows:self.values))

    def test_sustain_no_repeat_recovery_and_cpu_normalization(self):
        self.prepare()
        with patch("os.cpu_count",return_value=1):
            for t in (10,11,12,13):self.values[1]["cpu_seconds"]=t;self.observer.tick(t)
        self.assertEqual(len([e for e in self.events if e[0]=="anomaly.alert"]),3)
        with patch("os.cpu_count",return_value=1):
            self.values[1]["cpu_seconds"]=14;self.observer.tick(14)
        self.assertEqual(len(self.events),3)
        self.values[1].update(cpu_seconds=14,memory_mb=1,hung=False);self.observer.tick(15)
        self.assertEqual(len([e for e in self.events if e[0]=="anomaly.recovered"]),3)

    def test_unknown_sampling_does_not_exit_or_recover(self):
        self.prepare();self.observer.tick(10);self.observer.tick(12);count=len(self.events)
        self.values[1]=None;self.observer.tick(13);self.assertEqual(len(self.events),count)
        self.assertTrue(self.observer.error);self.assertTrue(self.observer.active)
        self.app.monitor.error="unknown";self.app.monitor.last=[];self.observer.tick(20);self.assertEqual(len(self.events),count)

    def test_disabled_rules_never_invoke_process_metrics(self):
        with patch.object(self.app.anomalies.sampler,"sample",side_effect=AssertionError("no real metrics")):
            self.app.anomalies.tick(10)
        self.assertFalse(self.app.anomalies.active)

    def test_absence_only_previously_seen_and_actual_recovery(self):
        self.prepare();self.values={};self.app.monitor.last=[];self.app.monitor.seen_names=set()
        self.observer.tick(10);self.observer.tick(30);self.assertFalse(self.events)
        self.app.monitor.seen_names={"sample.exe"};self.observer.tick(31);self.observer.tick(33);self.assertIn("持续退出",self.events[-1][1])
        self.app.monitor.last=[{"pid":1,"name":"sample.exe"}];self.observer.tick(34);self.assertEqual(self.events[-1][0],"anomaly.recovered")

    def test_pid_reuse_restarts_sustain_even_after_unknown(self):
        self.prepare();self.observer.tick(10);self.values[1]=None;self.observer.tick(11)
        self.values[1]={"created":2,"cpu_seconds":0,"memory_mb":20,"hung":False};self.observer.tick(12);self.assertFalse(self.events)
        self.observer.tick(14);self.assertEqual(len(self.events),1)


class InboxTests(Base):
    def prepare(self):
        self.config(True);self.inbox_root=self.root/"inbox";self.inbox_root.mkdir()
        self.app.save_section("inbound",{"enabled":True,"directory":str(self.inbox_root),"max_mb":1})
        self.downloads=[]
        def fake(url,out,limit,**kw):self.downloads.append(url);kw["check"]();out.write(b"fixture");return {"size":7}
        self.app.inbox.download=fake
        self.attachment={"filename":"fixture.txt","url":"https://multimedia.nt.qq.com.cn/download?fixture=1"}

    def test_offer_is_metadata_only_and_receive_is_explicit_single_use(self):
        self.prepare();self.app.receive("alice","","attach1",[self.attachment]);self.assertFalse(self.downloads)
        self.assertIn("尚未下载",self.qq.texts[-1][1]);self.app.receive("alice","接收","attach2")
        self.assertEqual((self.inbox_root/"fixture.txt").read_bytes(),b"fixture");self.assertEqual(self.app.tasks.recent("alice")[0]["state"],"completed")
        self.app.receive("alice","接收","attach2");self.assertEqual(len(self.downloads),1)
        self.assertEqual(self.app.config.get()["files"]["roots"],[str(self.shared)])

    def test_unknown_user_disabled_revoke_and_expiry_no_download(self):
        self.prepare()
        with self.assertRaises(PermissionError):self.app.inbox.offer("stranger",[self.attachment])
        self.app.inbox.offer("alice",[self.attachment]);self.app.inbox.pending["alice"]=(0,[self.attachment])
        with self.assertRaises(ValueError):self.app.inbox.receive("alice")
        self.app.inbox.offer("alice",[self.attachment]);self.app.save_section("inbound",{"enabled":False})
        with self.assertRaises(PermissionError):self.app.inbox.receive("alice")
        self.assertFalse(self.downloads)

    def test_multiple_selection_collision_and_failure_cleanup(self):
        self.prepare();self.app.inbox.offer("alice",[self.attachment,self.attachment])
        with self.assertRaises(ValueError):self.app.inbox.receive("alice",None)
        target=self.inbox_root/"fixture.txt";target.write_bytes(b"existing")
        with self.assertRaises(ValueError):self.app.inbox.receive("alice",1)
        self.assertEqual(target.read_bytes(),b"existing")
        target.unlink();self.app.inbox.offer("alice",[self.attachment])
        def fail(url,out,*a,**kw):out.write(b"partial");raise TimeoutError()
        self.app.inbox.download=fail
        with self.assertRaises(TimeoutError):self.app.inbox.receive("alice")
        self.assertEqual(list(self.inbox_root.iterdir()),[])

    def test_filename_and_url_escape_and_private_dns_rejected(self):
        self.prepare()
        for name in ("../secret","CON.txt","a\\b.txt","a:","..","trail."):
            with self.assertRaises(ValueError):filename(name)
        for url in ("http://multimedia.nt.qq.com.cn/x","https://evil.example/x","https://multimedia.nt.qq.com.cn.evil.example/x"):
            with self.assertRaises((ValueError,PermissionError)):self.app.inbox.offer("alice",[{**self.attachment,"url":url}])
        with patch("socket.getaddrinfo",return_value=[(2,1,6,"",("127.0.0.1",443))]):
            with self.assertRaises(PermissionError):download(self.attachment["url"],io.BytesIO(),100)


class ManagementTests(Base):
    def prepare(self):self.config();self.app.save_section("file_manager",{"roots":[str(self.shared)]});self.manager=self.app.file_manager

    def run_plan(self,plan):return self.app.tasks.run("files.apply","",lambda c:self.manager.execute(plan["id"],c))

    def test_preview_does_not_modify_and_move_and_recover_keep_contents(self):
        self.prepare();src=self.sample(data=b"original");dst=self.shared/"new.txt"
        plan=self.manager.preview([(src,dst)]);self.assertTrue(src.exists());self.assertFalse(dst.exists())
        row=self.run_plan(plan);self.assertEqual(row["state"],"completed");self.assertFalse(src.exists());self.assertEqual(dst.read_bytes(),b"original")
        self.app.tasks.run("files.recover","",lambda c:self.manager.recover(plan["id"],c));self.assertEqual(src.read_bytes(),b"original");self.assertFalse(dst.exists())
        with self.assertRaises(ValueError):self.run_plan(plan)

    def test_changed_file_conflict_revocation_and_expired_plan(self):
        self.prepare();src=self.sample();dst=self.shared/"new.txt";plan=self.manager.preview([(src,dst)])
        src.write_bytes(b"changed")
        with self.assertRaises(ValueError):self.run_plan(plan)
        self.assertTrue(src.exists());self.assertFalse(dst.exists())
        dst.write_bytes(b"conflict")
        with self.assertRaises(ValueError):self.manager.preview([(src,dst)])
        plan=self.manager.preview([(src,self.shared/"other.txt")]);self.manager.plans[plan["id"]]["expires"]=0
        with self.assertRaises(ValueError):self.run_plan(plan)
        with self.assertRaises(PermissionError):self.manager.preview([(src,self.root/"escape.txt")])

    def test_recovery_never_overwrites_modified_destination_or_existing_source(self):
        self.prepare();src=self.sample();dst=self.shared/"new.txt";plan=self.manager.preview([(src,dst)]);self.run_plan(plan)
        dst.write_bytes(b"modified")
        with self.assertRaises(ValueError):self.app.tasks.run("files.recover","",lambda c:self.manager.recover(plan["id"],c))
        self.assertEqual(dst.read_bytes(),b"modified");self.assertFalse(src.exists())

    def test_organize_multi_files_and_cancel_mid_copy_is_recoverable(self):
        self.prepare();a=self.sample("a.txt");b=self.sample("b.md");plan=self.manager.organize([a,b],self.shared)
        self.run_plan(plan);self.assertTrue((self.shared/"txt/a.txt").exists());self.assertTrue((self.shared/"md/b.md").exists())
        self.assertEqual(self.manager.history()[0]["files"],2)
        plan=self.manager.preview([(self.shared/"txt/a.txt",self.shared/"c.txt")])
        with patch.object(self.manager,"move_exclusive",side_effect=TaskCancelled("cancelled before atomic rename")):
            with self.assertRaises(TaskCancelled):self.run_plan(plan)
        self.assertTrue((self.shared/"txt/a.txt").exists());self.assertFalse((self.shared/"c.txt").exists())
        self.assertEqual(next(r for r in self.manager.history() if r["id"]==plan["id"])["state"],"partial")

    def test_local_scope_is_not_qq_scope_and_private_runtime_is_forbidden(self):
        self.prepare();p=self.sample()
        cfg=self.app.config.get();cfg["files"]["roots"]=[];self.app.config.save(cfg)
        self.assertEqual(len(self.manager.search("sample")),1)
        with self.assertRaises(PermissionError):self.app.files.fingerprint(p)
        with self.assertRaises(PermissionError):self.app.save_section("file_manager",{"roots":[str(self.app.directory)]})

    def test_docx_text_roundtrip_html_escaping_and_collision(self):
        self.prepare();src=self.sample("note.txt","你好\n<script>not executable</script>".encode());docx=self.shared/"note.docx"
        preview=self.app.conversion.preview(src,docx);self.assertFalse(docx.exists())
        self.app.tasks.run("documents.convert","",lambda c:self.app.conversion.execute(preview,c))
        extracted=self.shared/"roundtrip.txt";preview=self.app.conversion.preview(docx,extracted)
        self.app.tasks.run("documents.convert","",lambda c:self.app.conversion.execute(preview,c))
        self.assertIn("你好",extracted.read_text(encoding="utf-8"))
        output=self.shared/"note.html";preview=self.app.conversion.preview(src,output)
        self.app.tasks.run("documents.convert","",lambda c:self.app.conversion.execute(preview,c));self.assertIn("&lt;script&gt;",output.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):self.app.conversion.preview(src,output)
        with self.assertRaises(ValueError):self.app.conversion.preview(src,self.shared/"unsupported.pdf")


class ToolAndWebTests(Base):
    def test_bounded_https_read_rejects_redirect_and_oversize_without_network(self):
        class FakeConnection:
            status=200;body=b"fixture";length="7"
            def __init__(self,*args,**kw):self.stream=io.BytesIO(self.body)
            def request(self,*args,**kw):pass
            def getresponse(self):return self
            def getheader(self,key,default=None):return self.length if key=="Content-Length" else "text/plain"
            def read(self,n):return self.stream.read(n)
            def close(self):pass
        with patch("socket.getaddrinfo",return_value=[(2,1,6,"",("93.184.216.34",443))]),patch("http.client.HTTPSConnection",FakeConnection):
            out=io.BytesIO();self.assertEqual(download("https://example.com/x",out,100)["size"],7)
            self.assertEqual(out.getvalue(),b"fixture")
            FakeConnection.status=302
            with self.assertRaises(ValueError):download("https://example.com/x",io.BytesIO(),100)
            FakeConnection.status=200;FakeConnection.length="9999"
            with self.assertRaises(ValueError):download("https://example.com/x",io.BytesIO(),100)

    def test_notification_merges_keep_latest_text_and_count(self):
        self.app.store.enqueue("alice","one","same",30);self.app.store.enqueue("alice","two","same",30)
        rows=self.app.store.due();self.assertEqual(len(rows),1);self.assertEqual(rows[0]["merges"],2)
        self.assertIn("two",rows[0]["text"]);self.assertIn("合并 2",rows[0]["text"])

    def test_mouse_tasks_use_fake_backend_and_never_auto_start(self):
        from lingsuo.automation import AutomationEngine,ClickPlan
        class Backend:
            def __init__(self):self.clicks=[]
            def stop_requested(self):return False
            def click(self,*args):self.clicks.append(args)
        class Matcher:
            def load(self,path):return object()
            def locate(self,*args):return ((10,20),"fixture")
        Backend.capture=lambda self,region:None
        backend=Backend();engine=AutomationEngine(backend=backend,matcher=Matcher(),tasks=self.app.tasks)
        self.assertFalse(backend.clicks);self.assertFalse(self.app.tasks.recent())
        engine.start(ClickPlan(template="fixture",interval_ms=100,count=1,duration_s=1,delay_s=0))
        engine.thread.join(2);self.assertEqual(len(backend.clicks),1)
        row=self.app.tasks.recent()[0];self.assertEqual(row["state"],"completed");self.assertEqual(json.loads(row["result"])["clicks"],1)

    def test_origin_permission_and_schema_cannot_be_supplied_in_parameters(self):
        self.config();qq=Caller("alice","qq",frozenset({"files.write","files.read"}))
        with self.assertRaises(PermissionError):self.app.tools.execute("files.search",{"query":""},qq)
        local=Caller("","local",frozenset())
        with self.assertRaises(PermissionError):self.app.tools.execute("files.search",{"query":""},local)
        with self.assertRaises(ValueError):self.app.tools.execute("files.search",{"query":"","permissions":"files.read"},Caller("","local",frozenset({"files.read"})))

    def test_workflow_preflights_all_steps_and_cancels_between_steps(self):
        executor=ToolExecutor(self.app.tasks);calls=[]
        executor.register("test.one","fixture",lambda c:calls.append(1) or {"ok":True},{},{"run"})
        caller=Caller("","local",frozenset({"run"}))
        with self.assertRaises(PermissionError):executor.workflow([{"tool":"test.one","parameters":{}},{"tool":"unknown.tool","parameters":{}}],caller)
        self.assertFalse(calls)
        executor.register("test.cancel","fixture",lambda c:self.app.tasks.cancel(c.id) or {},{},{"run"})
        with self.assertRaises(TaskCancelled):executor.workflow([{"tool":"test.cancel","parameters":{}},{"tool":"test.one","parameters":{}}],caller)
        self.assertFalse(calls)
        row=executor.workflow([{"tool":"test.one","parameters":{}},{"tool":"test.one","parameters":{}}],caller)
        self.assertEqual(len(json.loads(row["result"])["steps"]),2)

    def test_web_requires_exact_one_use_consent_and_filters_scripts(self):
        calls=[]
        def fake(url,out,*a,**kw):calls.append(url);out.write(b'<h1>Public title</h1><script>ignore()</script><p>public text</p>');return {"size":75,"content_type":"text/html"}
        web=WebTasks(fake);url="https://example.com/public";token=web.grant(url)
        row=self.app.tasks.run("web.read","",lambda c:web.read(url,token,c));result=json.loads(row["result"])
        self.assertIn("Public title",result["text"]);self.assertNotIn("ignore()",result["text"])
        with self.assertRaises(PermissionError):self.app.tasks.run("web.read","",lambda c:web.read(url,token,c))
        token=web.grant(url)
        with self.assertRaises(PermissionError):self.app.tasks.run("web.read","",lambda c:web.read(url+"/other",token,c))
        self.assertEqual(len(calls),1)

    def test_public_url_rejects_credentials_cleartext_nonstandard_port_and_controls(self):
        for url in ("http://example.com","https://u:p@example.com","https://example.com:8443","https://example.com/\r\nx"):
            with self.assertRaises(ValueError):public_url(url)


if __name__=="__main__":unittest.main()
