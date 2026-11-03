"""One local view of existing independent scopes; no grant implies another grant."""
import copy
import os
from pathlib import Path


KINDS=("private","qq","observe","manage","receive")


def canonical(value):return os.path.normcase(str(Path(value).resolve()))


def rows(config,desktop):
    found={}
    def add(path,kind):
        key=canonical(path)
        found.setdefault(key,{"path":str(Path(path).resolve()),**{k:False for k in KINDS},"logs":[]})[kind]=True
    for section,kind in (("local_data","private"),("files","qq"),("watch","observe"),("file_manager","manage")):
        for path in config[section]["roots"]:add(path,kind)
    if config["files"]["allow_desktop"]:add(desktop,"qq")
    if config["inbound"]["directory"]:add(config["inbound"]["directory"],"receive")
    for path in config["watch"]["logs"]:
        parent=str(Path(path).parent);key=canonical(parent)
        found.setdefault(key,{"path":str(Path(parent).resolve()),**{k:False for k in KINDS},"logs":[]})["logs"].append(path)
    private=[Path(p).resolve() for p in config["local_data"]["roots"]]
    for row in found.values():
        p=Path(row["path"])
        row["qq_blocked"]=row["qq"] and any(p.is_relative_to(r) for r in private)
        row["qq_partial"]=row["qq"] and not row["qq_blocked"] and any(r.is_relative_to(p) for r in private)
    return sorted(found.values(),key=lambda r:r["path"].casefold())


def apply_row(config,path,values,desktop,*,remove=False):
    if not isinstance(values,dict) or set(values)!=set(KINDS) or any(type(v) is not bool for v in values.values()):raise ValueError("目录权限格式无效")
    if remove:values={k:False for k in KINDS}
    cfg=copy.deepcopy(config);key=canonical(path);real=str(Path(path).resolve())
    for section,kind in (("local_data","private"),("files","qq"),("watch","observe"),("file_manager","manage")):
        paths=[p for p in cfg[section]["roots"] if canonical(p)!=key]
        if values[kind] and not (kind=="qq" and key==canonical(desktop)):paths.append(real)
        cfg[section]["roots"]=paths
    if key==canonical(desktop):cfg["files"]["allow_desktop"]=values["qq"]
    current=cfg["inbound"]["directory"]
    if values["receive"]:
        if current and canonical(current)!=key:raise ValueError("接收目录只有一个，请先取消原接收目录，再选择新的目录")
        cfg["inbound"]["directory"]=real
    elif current and canonical(current)==key:
        cfg["inbound"].update(directory="",enabled=False)
    if remove:cfg["watch"]["logs"]=[p for p in cfg["watch"]["logs"] if canonical(Path(p).parent)!=key]
    if not cfg["watch"]["roots"] and not cfg["watch"]["logs"]:cfg["watch"]["enabled"]=False
    return cfg
