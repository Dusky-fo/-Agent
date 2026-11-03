"""Allowlisted diagnostics: no identities, paths, contents, credentials or raw errors."""
import importlib.metadata
import json
import platform
from pathlib import Path
from .build_info import REVISION
from . import __version__

def snapshot(runtime):
    cfg=runtime.config.get();versions={}
    for package in ("psutil","python-docx","lxml","numpy","opencv-python-headless","qq-botpy","Pillow"):
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]="not-installed"
    with runtime.store.lock:
        states={r[0]:r[1] for r in runtime.store.db.execute("SELECT state,count(*) FROM tasks GROUP BY state")}
        queue={r[0]:r[1] for r in runtime.store.db.execute("SELECT state,count(*) FROM outbox GROUP BY state")}
    state=runtime.gateway.status().get("state","")
    safe_state=state if state in {"disabled","connected","connecting","disconnected","failed","error","stopped"} else "unknown"
    return {"version":__version__,"revision":REVISION,"system":platform.system(),"python":platform.python_version(),"dependencies":versions,
            "qq":{"configured":bool(cfg["qq"]["appid"] and cfg["qq"]["secret"]),"enabled":cfg["qq"]["enabled"],"sandbox":cfg["qq"]["is_sandbox"],"state":safe_state,"authorized_users":len(cfg["qq"]["allowed_users"])},
            "monitor":{"selected_programs":len(cfg["monitor"]["process_names"]),"enabled":cfg["monitor"]["enabled"],"sample_available":bool(runtime.monitor.sample_time),"sample_unknown":bool(runtime.monitor.sampling_error())},
            "task_counts":states,"notification_counts":queue,"watch_enabled":cfg["watch"]["enabled"],"anomalies_enabled":cfg["anomalies"]["enabled"],
            "scopes":{"shared":len(cfg["files"]["roots"]),"desktop":cfg["files"]["allow_desktop"],"private":len(cfg["local_data"]["roots"]),"manage":len(cfg["file_manager"]["roots"]),"observe":len(cfg["watch"]["roots"]),"logs":len(cfg["watch"]["logs"])},
            "privacy":"No IDs, paths, contents, raw error bodies, URLs or credentials included."}

def export(runtime,path):
    data=snapshot(runtime)
    with Path(path).open("x",encoding="utf-8") as handle:json.dump(data,handle,ensure_ascii=False,indent=2)
    return data
