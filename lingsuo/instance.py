"""Signal only another LingSuo instance for the same local data directory."""
import ctypes
import hashlib
import os
from pathlib import Path


def event_name(directory):
    identity=os.path.normcase(str(Path(directory).resolve())).encode("utf-8")
    return "Local\\LingSuo.Restore."+hashlib.sha256(identity).hexdigest()[:32]


def kernel_api():
    api=ctypes.WinDLL("kernel32",use_last_error=True)
    api.CreateEventW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_wchar_p];api.CreateEventW.restype=ctypes.c_void_p
    api.OpenEventW.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_wchar_p];api.OpenEventW.restype=ctypes.c_void_p
    api.SetEvent.argtypes=[ctypes.c_void_p];api.SetEvent.restype=ctypes.c_int
    api.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];api.WaitForSingleObject.restype=ctypes.c_uint32
    api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
    return api


def request_restore(directory,api=None):
    if api is None and os.name!="nt":return False
    api=api or kernel_api();handle=api.OpenEventW(0x0002,False,event_name(directory))
    if not handle:return False
    try:return bool(api.SetEvent(handle))
    finally:api.CloseHandle(handle)


class RestoreSignal:
    def __init__(self,directory,api=None):
        self.api=None;self.handle=None
        if api is None and os.name!="nt":return
        self.api=api or kernel_api();self.handle=self.api.CreateEventW(None,False,False,event_name(directory))
        if not self.handle:raise OSError("无法建立灵梭实例唤醒信号")

    def consume(self):return bool(self.handle and self.api.WaitForSingleObject(self.handle,0)==0)

    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None
