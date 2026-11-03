import copy
import json
import os
import threading
from pathlib import Path
from .credentials import PREFIX,protect,unprotect,CredentialError

DEFAULT = {
    "version": 1,
    "monitor": {"enabled": True, "process_names": [], "interval": 5},
    "qq": {"enabled": False, "appid": "", "secret": "", "allowed_users": [], "is_sandbox": False},
    "files": {"allow_desktop": False, "roots": [], "max_mb": 30, "max_depth": 4,
             "blocked_extensions": [".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".lnk", ".scr", ".com", ".pif", ".jar"]},
    "local_data": {"roots": []},
    "watch": {"enabled":False,"interval":3,"roots":[],"include":["*"],"exclude":[".*","*.tmp"],"logs":[],"keywords":["ERROR","异常","Traceback"],"patterns":[],"merge_seconds":5,"max_files":5000},
    "anomalies": {"enabled":False,"cpu_percent":90,"memory_mb":2048,"sustain_seconds":30,"absence_seconds":60,"hang_seconds":30},
    "inbound": {"enabled":False,"directory":"","max_mb":30},
    "file_manager": {"roots":[]},
    "notifications": {"cooldown": 30, "max_attempts": 3},
    "panel": {"port": 18765},
    "plugins": {"enabled": [], "trusted_hashes": {}}
}


def validate(data):
    # Additive migration of this project's older configuration; never import the old app.
    if isinstance(data, dict):
        if isinstance(data.get("qq"), dict): data["qq"].setdefault("is_sandbox", False)
        data.setdefault("local_data", {"roots": []})
        if isinstance(data.get("files"), dict): data["files"].setdefault("blocked_extensions", list(DEFAULT["files"]["blocked_extensions"]))
        for key in ("watch","anomalies","inbound","file_manager"):data.setdefault(key,copy.deepcopy(DEFAULT[key]))
    if not isinstance(data, dict) or set(data) != set(DEFAULT):
        raise ValueError("配置字段不完整或含未知字段")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("不支持此配置版本")
    for section in DEFAULT:
        if isinstance(DEFAULT[section], dict):
            if not isinstance(data[section], dict) or set(data[section]) != set(DEFAULT[section]):
                raise ValueError("配置段字段不正确：" + section)
    for section, key, low, high in (("monitor", "interval", 1, 3600), ("files", "max_mb", 1, 200),
                                  ("files", "max_depth", 0, 10), ("notifications", "cooldown", 0, 3600),
                                  ("notifications", "max_attempts", 1, 5), ("panel", "port", 1024, 65535)):
        v = data[section][key]
        if type(v) is not int or not low <= v <= high:
            raise ValueError("数值范围不正确：%s.%s" % (section, key))
    for section,key,low,high in (("watch","interval",1,3600),("watch","merge_seconds",0,300),("watch","max_files",100,20000),("anomalies","cpu_percent",1,100),("anomalies","memory_mb",1,1048576),("anomalies","sustain_seconds",1,3600),("anomalies","absence_seconds",1,86400),("anomalies","hang_seconds",1,3600),("inbound","max_mb",1,200)):
        if type(data[section][key]) is not int or not low<=data[section][key]<=high:raise ValueError("数值范围不正确：%s.%s"%(section,key))
    for section in ("watch","anomalies","inbound"):
        if type(data[section]["enabled"]) is not bool:raise ValueError("开关须为布尔值")
    for section,key in (("watch","roots"),("watch","logs"),("watch","include"),("watch","exclude"),("watch","keywords"),("watch","patterns"),("file_manager","roots")):
        value=data[section][key]
        if not isinstance(value,list) or len(value)>100 or any(not isinstance(v,str) or not v.strip() or len(v)>1024 for v in value):raise ValueError("列表格式无效：%s.%s"%(section,key))
    from .observers import compile_patterns
    compile_patterns(data["watch"]["patterns"])
    if data["watch"]["enabled"] and not (data["watch"]["roots"] or data["watch"]["logs"]):raise ValueError("先授权至少一个观察目录或日志文件，再启用观察")
    for section,key in (("watch","roots"),("watch","logs"),("file_manager","roots")):
        if any(not Path(p).is_absolute() or p.startswith(("\\\\","//")) for p in data[section][key]):raise ValueError("只支持本机绝对路径")
    inbox=data["inbound"]["directory"]
    if not isinstance(inbox,str) or (inbox and (not Path(inbox).is_absolute() or inbox.startswith(("\\\\","//")))):raise ValueError("接收目录须为本机绝对路径")
    if data["inbound"]["enabled"] and not inbox:raise ValueError("先授权接收目录再启用附件接收")
    for section, key in (("monitor", "enabled"), ("qq", "enabled"), ("qq", "is_sandbox"), ("files", "allow_desktop")):
        if type(data[section][key]) is not bool:
            raise ValueError("开关必须是布尔值")
    for section,key in (("files","roots"),("files","blocked_extensions"),("local_data","roots"),("plugins","enabled")):
        v = data[section][key]
        if not isinstance(v, list) or len(v) > 200 or any(not isinstance(s, str) or not s.strip() or len(s) > 1024 for s in v):
            raise ValueError("列表格式不正确：%s.%s" % (section, key))
    for suffix in data["files"]["blocked_extensions"]:
        if not suffix.startswith(".") or any(c in suffix for c in "/\\:*?\"<>| ") or len(suffix) > 16:
            raise ValueError("禁发后缀须写成 .exe 这样的形式")
    for n in data["monitor"]["process_names"]:
        if not n.lower().endswith(".exe") or any(c in n for c in "/\\:\r\n"):
            raise ValueError("监控进程须填写程序文件名，例如 notepad.exe")
    if not isinstance(data["plugins"]["trusted_hashes"], dict):
        raise ValueError("插件信任记录格式不正确")
    for key in ("appid", "secret"):
        if not isinstance(data["qq"][key], str) or len(data["qq"][key]) > 1024:
            raise ValueError("QQ 配置格式不正确")
    if data["qq"]["enabled"] and not all(data["qq"][k].strip() for k in ("appid", "secret")):
        raise ValueError("启用 QQ 前须填写 AppID 和 AppSecret")
    if any(not Path(r).is_absolute() or str(r).startswith(("\\\\", "//")) for section in ("files", "local_data") for r in data[section]["roots"]):
        raise ValueError("共享目录须为本机绝对路径，不支持网络目录")
    return data


class Config:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "config.json"
        self.lock = threading.RLock()
        self.secret_error="";self.protected_secret=""
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding="utf-8-sig"))
            qq=data.get("qq",{}) if isinstance(data,dict) else {}
            value=qq.get("secret","") if isinstance(qq,dict) else ""
            if isinstance(value,str) and value.startswith(PREFIX):
                self.protected_secret=value
                try:data["qq"]["secret"]=unprotect(value,data["qq"]["appid"])
                except CredentialError as exc:
                    self.secret_error=str(exc);data["qq"]["secret"]="";data["qq"]["enabled"]=False
            self.data = validate(data)
            if value and not self.protected_secret:
                try:self.save(self.data)
                except (CredentialError,OSError):
                    self.secret_error="旧密钥未能迁移到 Windows 凭据保护，原配置保留；请重新填写密钥，本地监控仍可使用"
                    self.data["qq"]["enabled"]=False
        else:
            self.data = copy.deepcopy(DEFAULT)
            self.save(self.data)

    def get(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def public(self):
        result = self.get()
        result["qq"]["has_secret"] = bool(result["qq"]["secret"])
        result["qq"]["secret"] = ""
        return result

    def save(self, data):
        data = validate(copy.deepcopy(data))
        with self.lock:
            serialized=copy.deepcopy(data)
            same_app=not hasattr(self,"data") or data["qq"]["appid"]==self.data["qq"]["appid"]
            blob=protect(data["qq"]["secret"],data["qq"]["appid"]) if data["qq"]["secret"] else self.protected_secret if self.secret_error and same_app else ""
            serialized["qq"]["secret"]=blob
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
            self.data = data
            self.protected_secret=blob
            if data["qq"]["secret"] or not same_app:self.secret_error=""
