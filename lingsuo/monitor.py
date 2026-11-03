"""Generic snapshot transitions extracted from the old Monitor._transition design."""
import threading
import time
from .processes import list_processes


class ProcessMonitor:
    def __init__(self, config, emit, collector=list_processes):
        self.config, self.emit, self.collector = config, emit, collector
        self.previous = None
        self.seen_names = set()
        self.signature = None
        self.last = []
        self.error = ""
        self.sample_time=0
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.RLock()

    def sampling_error(self,now=None):
        """One validity decision for the desktop and anomaly rules."""
        with self.lock:
            cfg=self.config.get()["monitor"]
            if not cfg["enabled"]:return ""
            if self.error:return self.error
            if not self.sample_time:return "尚未取得有效进程采样，状态未知"
            if (time.time() if now is None else now)-self.sample_time>max(15,3*cfg["interval"]):
                return "进程状态已过期，等待重新采集；不能据此判断程序正常或退出"
            if self.thread is not None and not self.thread.is_alive() and not self.stop_event.is_set():return "进程采集服务已停止，状态未知"
            return ""

    @staticmethod
    def same_identity(left,right):
        return left["name"].casefold()==right["name"].casefold() and ("created" not in left or "created" not in right or left["created"]==right["created"])

    def tick(self):
        cfg = self.config.get()["monitor"]
        signature = (cfg["enabled"], tuple(sorted(n.casefold() for n in cfg["process_names"])))
        with self.lock:
            if signature != self.signature:
                self.previous, self.seen_names, self.signature = None, set(), signature
            if not cfg["enabled"]:
                self.last, self.previous, self.error = [], None, ""
                return
            try:
                rows = self.collector()
            except Exception:
                self.error = "进程采集失败，下一轮重试"
                return
            selected = set(signature[1])
            current = {p["pid"]: p for p in rows if p["name"].casefold() in selected}
            self.last, self.error = list(current.values()), ""
            self.sample_time=time.time()
            if self.previous is not None:
                for pid, row in self.previous.items():
                    if pid not in current or not self.same_identity(row,current[pid]):
                        self.emit("process.exit", "%s 已退出（PID %s）" % (row["name"], pid), "exit:%s" % row["name"].casefold())
                for pid, row in current.items():
                    if pid not in self.previous or not self.same_identity(row,self.previous[pid]):
                        kind = "process.restore" if row["name"].casefold() in self.seen_names else "process.start"
                        self.emit(kind, "%s %s（PID %s）" % (row["name"], "已恢复" if kind.endswith("restore") else "已启动", pid), "start:%s" % row["name"].casefold())
            self.seen_names.update(p["name"].casefold() for p in current.values())
            self.previous = current

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="process-monitor")
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(self.config.get()["monitor"]["interval"])

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(25)
