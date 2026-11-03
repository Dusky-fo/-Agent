"""Explicitly trusted Python plugins. Manifests are declarations, NOT a sandbox."""
import hashlib
import importlib.util
import json
import re
import threading
import shutil
import tempfile
from pathlib import Path
from . import __version__


class Registry:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, fn, params=None):
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,80}", name) or name in self.tools or not callable(fn):
            raise ValueError("插件工具名称无效或重复")
        self.tools[name] = {"name": name, "description": str(description)[:200], "fn": fn, "params": params or {}}

    def validate(self, name, args):
        if name not in self.tools or not isinstance(args, dict):
            raise ValueError("工具不存在或参数无效")
        tool = self.tools[name]
        if set(args) - set(tool["params"]):
            raise ValueError("工具含未知参数")
        for key, spec in tool["params"].items():
            if key not in args:
                if spec.get("required"):
                    raise ValueError("缺少参数：" + key)
                continue
            expected = {"string": str, "integer": int, "boolean": bool}.get(spec["type"])
            if type(args[key]) is not expected or isinstance(args[key], str) and len(args[key]) > 1000:
                raise ValueError("参数类型或长度错误：" + key)
        return args

    def call(self, name, args):
        self.validate(name,args)
        return self.tools[name]["fn"](**args)

    def public(self):
        return [{k: v for k, v in t.items() if k != "fn"} for t in self.tools.values()]


def package_hash(directory):
    digest = hashlib.sha256()
    entries = [p for p in Path(directory).rglob("*") if "__pycache__" not in p.parts]
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in entries):
        raise ValueError("插件包不能包含链接或目录联接")
    files = sorted(p for p in entries if p.is_file())
    if not files or len(files) > 200:
        raise ValueError("插件包文件数量无效")
    for p in files:
        if p.is_symlink() or not p.resolve().is_relative_to(Path(directory).resolve()):
            raise ValueError("插件不能包含外部链接")
        digest.update(p.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(p.read_bytes())
    return digest.hexdigest()


class Plugins:
    def __init__(self, directory, config):
        self.directory, self.config = Path(directory), config
        self.directory.mkdir(parents=True, exist_ok=True)
        self.registries, self.loaded, self.errors = {}, {}, {}
        self.lock = threading.RLock()

    def install(self, source):
        """Copy a reviewed local package; validate declarations without importing code."""
        source = Path(source)
        if source.is_symlink() or getattr(source, "is_junction", lambda: False)(): raise ValueError("插件来源不能是链接或目录联接")
        digest = package_hash(source)
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8-sig"))
        ident = manifest.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", ident): raise ValueError("插件标识无效")
        target = self.directory / ident
        with self.lock:
            if target.exists(): raise ValueError("已存在同名插件，未覆盖。请先退出程序并备份处理旧版本。")
            with tempfile.TemporaryDirectory(dir=self.directory.parent, prefix="plugin-staging-") as temporary:
                staging = Path(temporary)
                package = staging / ident
                shutil.copytree(source, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                row = Plugins(staging, self.config).scan()[0]
                if row.get("error") or row.get("hash") != digest or not (package / "plugin.py").is_file():
                    raise ValueError("插件清单、兼容版本、入口或内容校验失败，未安装")
                package.rename(target)
        return ident

    def scan(self):
        rows = []
        cfg = self.config.get()["plugins"]
        for folder in sorted(self.directory.iterdir()):
            if not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(self.directory.resolve()):
                continue
            try:
                manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
                if manifest["id"] != folder.name or not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", folder.name):
                    raise ValueError("插件标识与目录名不匹配")
                # 1.0 retains the original 0.1.0 plugin registration API.
                if manifest["core_version"] not in {__version__, "0.1.0"} or not isinstance(manifest["permissions"], list):
                    raise ValueError("核心版本不兼容或权限声明无效")
                for key in ("name", "version"):
                    if not isinstance(manifest[key], str):
                        raise ValueError("插件清单字段无效")
                if any(not isinstance(p, str) for p in manifest["permissions"]):
                    raise ValueError("插件权限声明无效")
                digest = package_hash(folder)
                declared = manifest.get("tools", [])
                if not isinstance(declared, list) or any(not isinstance(n, str) or not n.startswith(folder.name + ".") for n in declared):
                    raise ValueError("工具声明无效")
                rows.append({**manifest, "declared_tools": declared, "hash": digest, "enabled": folder.name in cfg["enabled"],
                             "trusted": cfg["trusted_hashes"].get(folder.name) == digest,
                             "loaded": self.loaded.get(folder.name) == digest,
                             "error": self.errors.get(folder.name, ""), "tools": self.registries.get(folder.name, Registry()).public()})
            except Exception:
                rows.append({"id": folder.name, "name": folder.name, "error": "插件清单无效，未加载", "loaded": False})
        return rows

    def load_enabled(self):
        for row in self.scan():
            if not row.get("enabled") or not row.get("trusted") or row.get("error"):
                continue
            ident = row["id"]
            try:
                folder = self.directory / ident
                module_path = folder / "plugin.py"
                if module_path.is_symlink() or not module_path.resolve().is_relative_to(folder.resolve()):
                    raise ValueError("插件入口越界")
                registry = Registry()
                spec = importlib.util.spec_from_file_location("lingsuo_plugin_" + ident, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.register(registry)
                declared = set(row["declared_tools"])
                # Runtime names must be namespaced; no core tools can be overridden.
                if any(not name.startswith(ident + ".") for name in registry.tools):
                    raise ValueError("插件工具须使用插件名前缀")
                if set(registry.tools) != declared:
                    raise ValueError("实际注册工具与清单不一致")
                self.registries[ident] = registry
                self.loaded[ident] = row["hash"]
            except Exception:
                self.errors[ident] = "插件加载失败，核心继续运行；检查依赖和入口"

    def configure(self, ident, enabled, trusted=False):
        with self.lock:
            row = next((r for r in self.scan() if r["id"] == ident), None)
            if not row or "hash" not in row:
                raise ValueError("插件清单无效")
            cfg = self.config.get()
            if enabled:
                if not trusted:
                    raise ValueError("启用前须明确确认信任此插件的代码及权限")
                cfg["plugins"]["trusted_hashes"][ident] = row["hash"]
                cfg["plugins"]["enabled"] = sorted(set(cfg["plugins"]["enabled"] + [ident]))
            else:
                cfg["plugins"]["enabled"] = [p for p in cfg["plugins"]["enabled"] if p != ident]
                self.registries.pop(ident, None)
                self.loaded.pop(ident, None)
            self.config.save(cfg)

    def call(self, ident, name, args):
        with self.lock:
            row = next((r for r in self.scan() if r["id"] == ident), {})
            if not all(row.get(k) for k in ("enabled", "trusted", "loaded")):
                raise PermissionError("插件未启用、内容已变化或需要重启")
            result = self.registries[ident].call(name, args)
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded) > 10000:
                raise ValueError("插件返回结果过大")
            return result
