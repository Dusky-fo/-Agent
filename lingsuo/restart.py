"""Explicit Windows elevation with a graceful single-instance handoff."""
import ctypes
import os
from pathlib import Path
import sys


def request_admin_restart(directory):
    from bootstrap import elevation_request
    directory=Path(directory).resolve()
    args=["--data-dir",str(directory),"--wait-for-process",str(os.getpid())]
    if getattr(sys,"frozen",False):
        executable=Path(sys.executable).resolve()
        working=executable.parent
    else:
        working=Path(__file__).resolve().parents[1]
        launcher=working/"bootstrap.py"
        executable=Path(sys._base_executable).with_name("pythonw.exe")
        if not launcher.is_file() or not executable.is_file():
            raise FileNotFoundError("找不到灵梭启动文件，请检查工程位置和 Python 环境。")
        args=["-I",str(launcher),*args,"--elevation-attempted"]
    elevation_request(executable,args,working)


def process_api():
    api=ctypes.WinDLL("kernel32",use_last_error=True)
    api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    api.OpenProcess.restype=ctypes.c_void_p
    api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    api.WaitForSingleObject.restype=ctypes.c_uint32
    api.CloseHandle.argtypes=[ctypes.c_void_p]
    api.CloseHandle.restype=ctypes.c_int
    return api


def wait_for_previous_process(pid, *, api=None, last_error=None):
    if pid<=0 or pid==os.getpid():raise ValueError("重启等待的进程编号无效")
    if api is None and os.name!="nt":raise OSError("管理员重启只支持 Windows")
    api=api or process_api()
    handle=api.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only: cannot terminate it.
    if not handle:
        if (last_error or ctypes.get_last_error)()==87:return  # Already exited.
        raise OSError("无法核对旧灵梭是否退出，已停止启动新实例。")
    try:
        result=api.WaitForSingleObject(handle,120000)
        if result!=0:
            raise TimeoutError("旧灵梭尚未正常退出或无法确认退出，未启动第二个实例。")
    finally:api.CloseHandle(handle)
