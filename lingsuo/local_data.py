"""Local text documents, separate from QQ file sharing and private runtime files."""
import hashlib
import os
from pathlib import Path
import tempfile


class LocalDocuments:
    LIMIT = 1024 * 1024
    EXTENSIONS = {".txt", ".md", ".json", ".ini", ".cfg", ".csv", ".log"}

    def __init__(self, config, directory):
        self.config, self.directory = config, Path(directory).resolve()

    def authorize(self, path):
        p = Path(path).resolve(strict=True)
        if not p.is_file() or p.suffix.lower() not in self.EXTENSIONS:
            raise ValueError("只支持本地文本资料：TXT、MD、JSON、INI、CFG、CSV、LOG")
        if p.is_relative_to(self.directory): raise PermissionError("程序凭据和运行数据库不能在资料编辑器中打开")
        if str(p).startswith(("\\\\", "//")): raise PermissionError("暂不支持网络共享资料")
        if not any(p.is_relative_to(Path(r).resolve()) for r in self.config.get()["local_data"]["roots"]):
            raise PermissionError("请先在本机授权这个资料文件夹；该授权不会开放给 QQ")
        return p

    def read(self, path):
        p = self.authorize(path)
        with p.open("rb") as handle:
            if os.name == "nt":
                import ctypes
                import msvcrt
                fn = ctypes.windll.kernel32.GetFinalPathNameByHandleW
                fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
                final = ctypes.create_unicode_buffer(32768)
                count = fn(msvcrt.get_osfhandle(handle.fileno()), final, len(final), 0)
                if not 0 < count < len(final): raise PermissionError("无法核对已打开资料的真实路径")
                target = final.value
                if target.startswith("\\\\?\\UNC\\"): raise PermissionError("资料不能指向网络共享")
                if target.startswith("\\\\?\\"): target = target[4:]
                if self.authorize(target) != p: raise PermissionError("文件目标发生变化，请重新选择")
            raw = handle.read(self.LIMIT + 1)
        if len(raw) > self.LIMIT: raise ValueError("文本资料超过 1 MB，请使用专用编辑器处理")
        if b"\x00" in raw: raise ValueError("检测到二进制或 UTF-16 文件，本版只编辑 UTF-8 文本")
        try: text = raw.decode("utf-8-sig")
        except UnicodeDecodeError: raise ValueError("资料不是 UTF-8 文本，请先在其他编辑器中转换编码") from None
        return {"path":str(p), "name":p.name, "text":text, "sha256":hashlib.sha256(raw).hexdigest(), "bom":raw.startswith(b"\xef\xbb\xbf")}

    def save(self, expected, text):
        current = self.read(expected["path"])
        if current["sha256"] != expected["sha256"]: raise ValueError("文件已被其他程序修改，请重新打开核对后再保存")
        raw = text.encode("utf-8-sig" if expected.get("bom") else "utf-8")
        if len(raw) > self.LIMIT: raise ValueError("文本资料不能超过 1 MB")
        # Recoverable backup stays in excluded application data, never beside a QQ-shared file.
        backup = self.directory / "document-backups"; backup.mkdir(exist_ok=True)
        (backup / (current["sha256"] + ".bak")).write_bytes(current["text"].encode("utf-8-sig" if current["bom"] else "utf-8"))
        p = self.authorize(current["path"])
        fd, temporary = tempfile.mkstemp(dir=p.parent, prefix=".lingsuo-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as output: output.write(raw)
            if self.read(p)["sha256"] != expected["sha256"]: raise ValueError("保存前文件发生变化，请重新打开")
            os.replace(temporary, p)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return self.read(p)
