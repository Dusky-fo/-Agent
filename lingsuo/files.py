"""Explicit roots, handle checks, expiring fingerprints, and private upload snapshots."""
import contextlib
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from .paths import desktop_dir


class FileService:
    def __init__(self, config, data_dir, desktop=desktop_dir, event=None):
        self.config, self.data_dir, self.desktop = config, Path(data_dir).resolve(), desktop
        self.event = event or (lambda *_: None)

    def blocked(self, path):
        suffixes = {s.lower() for s in self.config.get()["files"]["blocked_extensions"]}
        return Path(path).suffix.lower() in suffixes

    def roots(self):
        cfg = self.config.get()["files"]
        paths = list(cfg["roots"])
        if cfg["allow_desktop"]:
            paths.insert(0, str(self.desktop()))
        result = []
        private = [Path(r).resolve() for r in self.config.get()["local_data"]["roots"]]
        for value in paths:
            p = Path(value).resolve()
            if p.is_dir() and not str(p).startswith(("\\\\", "//")) and p not in result and not any(p.is_relative_to(r) for r in private):
                result.append(p)
        return result

    def authorize(self, value):
        if not isinstance(value, (str, Path)) or ":" in str(value)[2:]:
            raise ValueError("文件路径无效")
        p = Path(value).resolve(strict=True)
        if not p.is_file() or not any(p.is_relative_to(r) for r in self.roots()):
            raise PermissionError("文件不在已授权共享目录中")
        if p.is_relative_to(self.data_dir):
            raise PermissionError("不能发送本程序的凭据、日志或运行数据")
        if any(p.is_relative_to(Path(root).resolve()) for root in self.config.get()["local_data"]["roots"]):
            raise PermissionError("这是仅供本机软件访问的资料目录，QQ 文件接口无权访问")
        if p.name.casefold() in {"desktop.ini", "ntuser.dat"}:
            raise PermissionError("不支持发送该系统文件")
        if self.blocked(p):
            self.event("file.blocked", "已拦下不允许发送的文件类型：" + p.name)
            raise PermissionError("这个类型不能通过 QQ 发送：" + p.suffix.lower())
        return p

    @contextlib.contextmanager
    def checked_open(self, path):
        p = self.authorize(path)
        with p.open("rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise PermissionError("只允许普通文件")
            if os.name == "nt":
                import ctypes
                import msvcrt
                buf = ctypes.create_unicode_buffer(32768)
                fn = ctypes.windll.kernel32.GetFinalPathNameByHandleW
                fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
                n = fn(msvcrt.get_osfhandle(f.fileno()), buf, len(buf), 0)
                if not 0 < n < len(buf):
                    raise PermissionError("无法核对已打开文件的真实路径")
                final = buf.value
                if final.startswith("\\\\?\\UNC\\"):
                    raise PermissionError("不允许网络文件")
                if final.startswith("\\\\?\\"):
                    final = final[4:]
                if self.authorize(final) != p:
                    raise PermissionError("文件目标已改变，请重新选择")
            yield f

    def fingerprint(self, path):
        p = self.authorize(path)
        limit = self.config.get()["files"]["max_mb"] * 1024**2
        digest, total = hashlib.sha256(), 0
        with self.checked_open(p) as f:
            while chunk := f.read(1024**2):
                total += len(chunk)
                if total > limit:
                    raise ValueError("文件超过当前大小限制")
                digest.update(chunk)
        if not total:
            raise ValueError("暂不发送空文件")
        return {"path": str(p), "name": p.name, "size": total, "sha256": digest.hexdigest()}

    @contextlib.contextmanager
    def snapshot(self, expected):
        spool = self.data_dir / "spool"
        spool.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=spool, suffix=".upload")
        try:
            digest, total = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as out, self.checked_open(expected["path"]) as source:
                while chunk := source.read(1024**2):
                    total += len(chunk)
                    if total > min(expected["size"], self.config.get()["files"]["max_mb"] * 1024**2):
                        raise ValueError("文件已变化或超过限制，请重新选择")
                    digest.update(chunk)
                    out.write(chunk)
            if total != expected["size"] or digest.hexdigest() != expected["sha256"]:
                raise ValueError("文件内容已变化，请重新选择并确认")
            yield Path(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def search(self, keyword="", desktop_only=False):
        cfg = self.config.get()["files"]
        roots = self.roots()
        if desktop_only:
            target = self.desktop().resolve()
            roots = [r for r in roots if r == target] if cfg["allow_desktop"] else []
        result, visited = [], 0
        for root in roots:
            for folder, dirs, files in os.walk(root, followlinks=False):
                visited += 1
                if visited > 1500:
                    return result
                depth = len(Path(folder).relative_to(root).parts)
                private = [Path(r).resolve() for r in self.config.get()["local_data"]["roots"]]
                dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink() and not d.startswith(".") and
                                 not any((Path(folder) / d).resolve().is_relative_to(r) for r in private)) if depth < cfg["max_depth"] else []
                for name in sorted(files):
                    if keyword.casefold() not in name.casefold():
                        continue
                    try:
                        p = self.authorize(Path(folder) / name)
                        size = p.stat().st_size
                        if 0 < size <= cfg["max_mb"] * 1024**2:
                            result.append({"path": str(p), "name": p.name, "size": size})
                    except (OSError, ValueError):
                        continue
                    if len(result) >= 20:
                        return result
        return result
