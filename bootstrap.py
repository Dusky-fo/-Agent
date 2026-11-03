"""Source launcher: one elevated host, project-only packages, no persistent redirector."""
import ctypes
import os
from pathlib import Path
import runpy
import site
import subprocess
import sys


def is_admin():
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevation_request(executable, args, directory):
    fn = ctypes.windll.shell32.ShellExecuteW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    fn.restype = ctypes.c_void_p
    result = fn(None, "runas", str(executable), subprocess.list2cmdline(args), str(directory), 1)
    if not result or result <= 32:
        raise PermissionError("未获得管理员授权，灵梭没有启动。可重新运行并在 Windows UAC 中确认。")


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    project = Path(__file__).resolve().parent
    development = any(flag in args for flag in ("--self-test", "--headless", "--no-admin"))
    if not development and not is_admin():
        if "--elevation-attempted" in args:
            raise PermissionError("当前进程仍没有管理员权限，已停止重复请求。")
        # Preserve the original user's directory even if UAC uses another administrator account.
        if "--data-dir" not in args:
            from_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LingSuoBasic"
            args.extend(["--data-dir", str(from_data)])
        actual_python = Path(sys._base_executable).with_name("pythonw.exe")
        elevation_request(actual_python, ["-I", str(project / "bootstrap.py"), *args, "--elevation-attempted"], project)
        return 0
    args = [arg for arg in args if arg not in ("--no-admin", "--elevation-attempted")]
    venv = project / ".venv"
    if not (venv / "Lib/site-packages").is_dir(): raise RuntimeError("缺少项目独立依赖环境 .venv")
    sys.prefix = sys.exec_prefix = str(venv)
    site.addsitedir(str(venv / "Lib/site-packages"))
    sys.path.insert(0, str(project))
    sys.argv = [str(project / "main.py"), *args]
    runpy.run_path(str(project / "main.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), "灵梭启动未完成", 0x10)
        raise SystemExit(1)
