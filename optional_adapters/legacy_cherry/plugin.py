"""Explicit optional legacy adapter. Imports dependencies only on a user tool call."""
import importlib.util
import json
import sys
from pathlib import Path


def status():
    root = Path(__file__).resolve().parent
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8-sig"))
    if not settings.get("root") or not Path(settings["root"]).is_dir():
        raise ValueError("请先配置该可选适配包自己的数据根目录")
    name = "_lingsuo_optional_legacy_cherry"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / "legacy/__init__.py", submodule_search_locations=[str(root / "legacy")])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    module_name = name + ".cherry_ctl"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, root / "legacy/cherry_ctl.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    module = sys.modules[module_name]
    module.configure(settings["root"], settings.get("start_key"))
    return {"targets": module.list_targets()}


def register(registry):
    registry.register("legacy_cherry.status", "读取旧适配目标状态（仅用户主动调用）", status)
