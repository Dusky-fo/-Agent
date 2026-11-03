"""Bounded, opt-in directory/log observation and sustained process anomaly rules."""
import codecs
import fnmatch
import json
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path


def compile_patterns(values):
    result=[]
    for value in values:
        if len(value)>100 or any(c in value for c in "*+{}()|?") or re.search(r"\\[1-9]",value):
            raise ValueError("受限正则仅支持固定文字、字符类、点、锚点和字符转义；不支持量词、分组、分支或反向引用")
        try:result.append(re.compile(value,re.IGNORECASE))
        except re.error:raise ValueError("日志正则格式无效") from None
    return result


def safe_path(value, private, must_exist=True):
    path=Path(value)
    if not path.is_absolute() or str(path).startswith(("\\\\","//")):raise PermissionError("只允许本机绝对路径")
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,"is_junction",lambda:False)():raise PermissionError("不监控链接或目录联接")
    resolved=path.resolve(strict=must_exist)
    if resolved.is_relative_to(Path(private).resolve()):raise PermissionError("程序凭据和运行数据目录不允许加入此范围")
    return resolved


class FileLogObserver:
    def __init__(self,config,directory,emit):
        self.config,self.directory,self.emit=config,Path(directory),emit
        self.signature=None;self.snapshots={};self.logs={};self.pending=Counter();self.names=[];self.since=None
        self.error="";self.last_tick=0;self.stop_event=threading.Event();self.lock=threading.RLock()

    def status(self):
        with self.lock:return {"error":self.error,"files":len(self.snapshots),"logs":len(self.logs),"pending":sum(self.pending.values())}

    def snapshot(self,cfg):
        found={}
        for value in cfg["roots"]:
            root=safe_path(value,self.directory)
            if not root.is_dir():raise ValueError("授权监控目录不存在")
            def walk_error(_):raise OSError("监控目录部分内容无法读取，保留原基线")
            for folder,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
                if self.stop_event.is_set():return None
                dirs[:]=[d for d in sorted(dirs) if not any(fnmatch.fnmatch(d,p) for p in cfg["exclude"]) and not (Path(folder)/d).is_symlink() and not getattr(Path(folder)/d,"is_junction",lambda:False)() and not (Path(folder)/d).resolve().is_relative_to(self.directory.resolve())]
                if len(Path(folder).relative_to(root).parts)>=10:dirs[:]=[]
                for name in sorted(files):
                    if not any(fnmatch.fnmatch(name,p) for p in cfg["include"]) or any(fnmatch.fnmatch(name,p) for p in cfg["exclude"]):continue
                    p=safe_path(Path(folder)/name,self.directory)
                    if not p.is_file():continue
                    stat=p.stat();found[str(p)]=(stat.st_size,stat.st_mtime_ns)
                    if len(found)>cfg["max_files"]:raise ValueError("监控文件数量超过限制，请缩小目录范围；原基线保留")
        return found

    def add(self,kind,name,now,count=1):
        self.pending[kind]+=count
        if len(self.names)<5 and name not in self.names:self.names.append(name)
        if self.since is None:self.since=now

    def read_log(self,value,cfg,patterns,now):
        path=safe_path(value,self.directory)
        if not path.is_file():raise ValueError("指定日志不存在")
        key=str(path);stat=path.stat();identity=(stat.st_dev,stat.st_ino)
        state=self.logs.get(key)
        if state is None:
            self.logs[key]={"identity":identity,"offset":stat.st_size,"decoder":codecs.getincrementaldecoder("utf-8")("replace"),"tail":"","anchor":b""}
            with path.open("rb") as handle:
                handle.seek(max(0,stat.st_size-32));self.logs[key]["anchor"]=handle.read(32)
            return
        with path.open("rb") as handle:
            rotated=identity!=state["identity"] or stat.st_size<state["offset"]
            if not rotated and state["anchor"]:
                handle.seek(max(0,state["offset"]-len(state["anchor"])));rotated=handle.read(len(state["anchor"]))!=state["anchor"]
            if rotated:
                state={"identity":identity,"offset":0,"decoder":codecs.getincrementaldecoder("utf-8")("replace"),"tail":"","anchor":b""};self.logs[key]=state
                self.add("日志轮转",path.name,now)
            handle.seek(state["offset"]);raw=handle.read(65536)
            state["offset"]+=len(raw)
            handle.seek(max(0,state["offset"]-32));state["anchor"]=handle.read(min(32,state["offset"]))
        decoded=state["tail"]+state["decoder"].decode(raw)
        lines=decoded.split("\n");state["tail"]=lines.pop()[-4096:]
        hits=0
        for line in lines:
            line=line[:4096]
            if any(word.casefold() in line.casefold() for word in cfg["keywords"]) or any(p.search(line) for p in patterns):hits+=1
        if hits:self.add("日志命中",path.name,now,hits)

    def tick(self,now=None):
        now=time.monotonic() if now is None else now
        cfg=self.config.get()["watch"];signature=json.dumps(cfg,sort_keys=True)
        with self.lock:
            if signature!=self.signature:
                self.signature=signature;self.snapshots={};self.logs={};self.pending.clear();self.names=[];self.since=None;self.last_tick=0;self.initial=True
            if not cfg["enabled"]:self.error="";return
            if now-self.last_tick<cfg["interval"]:return
            self.last_tick=now
            try:
                current=self.snapshot(cfg)
                if current is None:return
                if not self.initial:
                    for path,info in current.items():
                        if path not in self.snapshots:self.add("新增",Path(path).name,now)
                        elif info!=self.snapshots[path]:self.add("修改",Path(path).name,now)
                    for path in self.snapshots.keys()-current.keys():self.add("删除",Path(path).name,now)
                self.snapshots=current;self.initial=False
                patterns=compile_patterns(cfg["patterns"])
                errors=[]
                for path in cfg["logs"]:
                    if self.stop_event.is_set():return
                    try:self.read_log(path,cfg,patterns,now)
                    except (OSError,ValueError,PermissionError):errors.append("有日志无法读取，未作为日志内容异常报警")
                self.error="；".join(dict.fromkeys(errors))
                if self.pending and now-self.since>=cfg["merge_seconds"]:
                    text="文件/日志变化："+"，".join("%s %s"%(key,count) for key,count in self.pending.items())+"；文件："+"、".join(self.names)
                    self.emit("watch.changed",text,"watch.changed")
                    self.pending.clear();self.names=[];self.since=None
            except (OSError,ValueError,PermissionError) as exc:
                self.error=str(exc) if isinstance(exc,ValueError) else "监控范围不可用或无权限，保留原基线并重试"


class ProcessMetrics:
    def sample(self,rows):
        import psutil
        values={}
        for row in rows:
            try:
                process=psutil.Process(row["pid"]);cpu=process.cpu_times()
                values[row["pid"]]={"created":process.create_time(),"cpu_seconds":cpu.user+cpu.system,"memory_mb":process.memory_info().rss/1024**2,"hung":None}
            except (psutil.Error,OSError):values[row["pid"]]=None
        try:
            import ctypes
            from ctypes import wintypes as w
            api=ctypes.windll.user32;cb=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
            api.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
            api.IsWindowVisible.argtypes=[w.HWND];api.IsHungAppWindow.argtypes=[w.HWND]
            api.EnumWindows.argtypes=[cb,w.LPARAM]
            def visit(hwnd,_):
                if api.IsWindowVisible(hwnd):
                    pid=w.DWORD();api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                    if values.get(pid.value) is not None:
                        values[pid.value]["hung"]=bool(values[pid.value]["hung"]) or bool(api.IsHungAppWindow(hwnd))
                return True
            if not api.EnumWindows(cb(visit),0):raise OSError("无法判断窗口响应")
        except (AttributeError,OSError):
            for value in values.values():
                if value is not None:value["hung"]=None
        return values


class AnomalyObserver:
    def __init__(self,config,monitor,emit,sampler=None):
        self.config,self.monitor,self.emit=config,monitor,emit
        self.sampler=sampler or ProcessMetrics();self.signature=None;self.previous={};self.pending={};self.active=set();self.error="";self.last_tick=0
        self.lock=threading.RLock()
        self.identities={}

    def status(self):
        with self.lock:return {"error":self.error,"active":len(self.active)}

    def condition(self,key,value,seconds,now,label):
        if value is None:self.pending.pop(key,None);return
        if value:
            self.pending.setdefault(key,now)
            if key not in self.active and now-self.pending[key]>=seconds:
                self.active.add(key);self.emit("anomaly.alert",label,"anomaly:"+key)
        else:
            self.pending.pop(key,None)
            if key in self.active:
                self.active.remove(key);self.emit("anomaly.recovered",label+"：已恢复正常","recover:"+key)

    def tick(self,now=None):
        now=time.monotonic() if now is None else now;cfg=self.config.get();rules=cfg["anomalies"]
        signature=json.dumps([rules,cfg["monitor"]],sort_keys=True)
        with self.lock:
            if signature!=self.signature:
                self.signature=signature;self.previous={};self.pending={};self.active=set();self.last_tick=0
                self.identities={}
            if not rules["enabled"] or not cfg["monitor"]["enabled"]:self.error="";return
            if now-self.last_tick<1:return
            self.last_tick=now
            with self.monitor.lock:
                if self.monitor.sampling_error():
                    self.pending.clear();self.previous.clear();self.error="进程采集未知，暂停异常计时，不报告退出或恢复";return
                rows=[dict(r) for r in self.monitor.last];seen=set(self.monitor.seen_names)
            try:metrics=self.sampler.sample(rows)
            except Exception:
                self.pending.clear();self.previous.clear();self.error="资源指标采集失败，未当作零值或退出";return
            self.error="";current_names={r["name"].casefold() for r in rows};next_previous={};live_keys=set()
            for row in rows:
                pid=row["pid"];name=row["name"].casefold();value=metrics.get(pid)
                if value is None:
                    self.error="部分指标无权限或不可用，相关计时暂停"
                    for kind in ("cpu","memory","hung"):
                        key=kind+":"+str(pid);self.pending.pop(key,None);live_keys.add(key)
                    continue
                previous=self.previous.get(pid);cpu=None
                if pid in self.identities and self.identities[pid]!=value.get("created"):
                    for kind in ("cpu","memory","hung"):
                        key=kind+":"+str(pid);self.pending.pop(key,None);self.active.discard(key)
                self.identities[pid]=value.get("created")
                if previous and previous[1].get("created")==value.get("created") and now>previous[0]:
                    cpu=max(0,min(100,100*(value["cpu_seconds"]-previous[1]["cpu_seconds"])/(now-previous[0])/max(1,os.cpu_count() or 1)))
                next_previous[pid]=(now,dict(value))
                for kind,bad,seconds,title in (("cpu",None if cpu is None else cpu>=rules["cpu_percent"],rules["sustain_seconds"],"CPU持续超限"),("memory",None if value.get("memory_mb") is None else value["memory_mb"]>=rules["memory_mb"],rules["sustain_seconds"],"内存持续超限"),("hung",value.get("hung"),rules["hang_seconds"],"窗口持续无响应")):
                    key=kind+":"+str(pid);live_keys.add(key)
                    self.condition(key,bad,seconds,now,"%s（PID %s）%s"%(row["name"],pid,title))
            self.previous=next_previous
            self.identities={pid:created for pid,created in self.identities.items() if pid in {row["pid"] for row in rows}}
            for name in cfg["monitor"]["process_names"]:
                key="absent:"+name.casefold();live_keys.add(key)
                self.condition(key,name.casefold() not in current_names if name.casefold() in seen else None,rules["absence_seconds"],now,name+"持续退出/缺失")
            for key in set(self.pending)|self.active:
                if key not in live_keys:self.pending.pop(key,None);self.active.discard(key)
