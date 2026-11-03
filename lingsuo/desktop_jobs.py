"""Native background jobs and versioned snapshots; Tk calls stay on the UI thread."""
import queue
import time
from .qq import QQError
from .tasks import TaskCancelled,TaskTimedOut

class DesktopJobs:
    ACTION_RESOURCES={
        **dict.fromkeys(("save_monitor","add_root","remove_root","advanced_save","permissions_save","permissions_remove","permissions_log","permissions_unlog","watch_save","anomaly_save","inbox_save","quick_connect","quick_only_save","qq_save","revoke","private_root"),"settings"),
        **dict.fromkeys(("search_files","manager_search","pick_file"),"file-search"),
        **dict.fromkeys(("manager_execute","manager_restore","private_save","manager_cleanup"),"file-write"),
        "send_file":"file-send","file_send_selected":"file-send","read_processes":"read-processes","web_task_read":"web-read","private_open":"document-read",
        "check_qq":"qq-check","connect_qq":"settings","disconnect_qq":"settings","disable_qq":"settings","quick_pair_start":"settings","quick_pair_confirm":"settings",
        "plugin_install":"plugin-settings","plugins_enable":"settings","plugins_disable":"settings","plugin_call":"plugin-call",
    }
    def submit(self, fn, done=None,*,resource=None,action=None):
        if self.closing:return False
        key=action or self._active_action_key or "action"
        resource=resource or self.ACTION_RESOURCES.get(key,"background")
        if key in self.pending_actions or resource in self.resources:
            self.message.set("同类操作正在进行，请等待；你仍可查看帮助、记录和其他功能。")
            return False
        self.pending_actions[key]=resource;self.resources.add(resource);self.busy=True
        widget=self.buttons.get(key)
        if widget and widget.winfo_exists():
            if not widget.disabled:widget.busy_label=widget.text
            widget.text="处理中…";widget.disabled=True;widget.configure(width=max(widget.winfo_reqwidth(),widget.font.measure(widget.text)+28));widget.draw()
        owner=getattr(self,"active_dialog",None)
        saved=self.dialog_inputs(owner) if owner and owner.winfo_exists() and getattr(owner,"save_action",(None,))[0]==key else None
        self.message.set("正在处理；可继续查看记录和帮助。")
        future=self.pool.submit(fn)
        future.add_done_callback(lambda result:self.events.put(("result",(result,done,key,widget,owner,saved))))
        return True

    def load_snapshot(self):
        options=dict(getattr(self,"record_query",{}));options["offset"]=getattr(self,"record_offset",0)
        return {"status":self.app.status(),"config":self.app.config.get(),"tasks":self.app.tasks.recent(),"task_page":self.app.tasks.query(**options),"history":self.app.store.history()}

    def request_snapshot(self):
        if self.closing or self.snapshot_future is not None:return
        self.next_snapshot=time.monotonic()+1
        epoch=self.state_epoch
        self.snapshot_future=self.snapshot_pool.submit(self.load_snapshot)
        self.snapshot_future.add_done_callback(lambda result:self.events.put(("snapshot",(epoch,result))))

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "result":
                    future,done,key,widget,owner,saved=value
                    self.resources.discard(self.pending_actions.pop(key,None));self.busy=bool(self.pending_actions)
                    self.next_snapshot=0
                    self.state_epoch+=1
                    try:
                        result = future.result()
                        if not self.closing:
                            self.message.set("操作已完成。")
                            previous=self._active_action_key;self._active_action_key=key
                            try:
                                if done: done(result)
                            finally:self._active_action_key=previous
                            if saved is not None and owner and owner.winfo_exists():self.mark_dialog_saved(owner,values=saved)
                    except (TaskCancelled,TaskTimedOut) as exc:
                        if not self.closing:self.message.set(str(exc)+"；请到任务记录查看真实结果，已完成步骤不会撤回。")
                    except (ValueError, PermissionError, OSError, QQError) as exc:
                        if not self.closing:
                            self.message.set(str(exc)); self.show_error(str(exc))
                    except Exception:
                        if not self.closing:
                            self.message.set("操作失败，请检查设置和帮助。")
                            self.show_error("操作失败，请检查设置和帮助。")
                    finally:
                        if key not in self.pending_actions and widget and widget.winfo_exists():
                            widget.text=widget.busy_label;widget.disabled=False;widget.draw()
                elif kind=="snapshot":
                    self.snapshot_future=None
                    epoch,future=value
                    if epoch!=self.state_epoch:
                        self.next_snapshot=0;continue
                    try:
                        snapshot=future.result();self.last_snapshot=snapshot
                        if not self.closing:self.refresh(snapshot)
                    except Exception:
                        if not self.closing:self.banner.set("状态暂不可用；正在重试，不会当作程序退出。")
                elif kind == "automation-toggle":
                    if value == self.automation_generation and self.armed_plan is not None and not self.closing:
                        try:self.toggle_automation()
                        except ValueError as exc:self.automation_status.set(str(exc))
                elif kind == "desktop-authorization-reset":
                    self.vars["desktop"].set(self.app.config.get()["files"]["allow_desktop"])
                elif kind=="file-task":self.file_task_id=value
                elif kind == "restore": self.restore()
                elif kind == "restart-admin": self.restart_as_admin()
                elif kind == "exit": self.close()
                elif kind == "tray-ready": self.tray_ready = True
                elif kind == "tray-error":
                    self.tray_ready = False
                    if not self.closing:
                        self.restore(); self.message.set("托盘不可用，主窗口仍可正常使用；关闭窗口将询问是否退出。")
        except queue.Empty:
            pass
        if self.closing and not self.busy and self.snapshot_future is None:
            self.pool.shutdown(wait=False)
            self.snapshot_pool.shutdown(wait=False)
            if self.icon: self.icon.stop()
            self.root.destroy()
            return
        if not self.closing:
            signal=getattr(self.app,"restore_signal",None)
            if signal and signal.consume():self.restore()
            if time.monotonic()>=self.next_snapshot:self.request_snapshot()
        self.poll_id = self.root.after(50, self._poll)

