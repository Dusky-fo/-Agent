"""Hold an existing local file against Windows write/delete during backup cleanup."""
import contextlib
import ctypes
import os
from pathlib import Path

@contextlib.contextmanager
def stable_original(path):
    if os.name!="nt":raise OSError("备份清理需要Windows文件共享锁，当前环境不执行")
    import msvcrt
    api=ctypes.WinDLL("kernel32",use_last_error=True)
    api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
    api.CloseHandle.argtypes=[ctypes.c_void_p];api.CloseHandle.restype=ctypes.c_int
    api.GetFinalPathNameByHandleW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32];api.GetFinalPathNameByHandleW.restype=ctypes.c_uint32
    path=Path(path).resolve(strict=True);handle=api.CreateFileW(str(path),0x80000000,1,None,3,0x08000000,None)
    if handle==ctypes.c_void_p(-1).value:raise PermissionError("原文件正在被写入或无法锁定，保留备份，请稍后重试")
    try:
        buffer=ctypes.create_unicode_buffer(32768);n=api.GetFinalPathNameByHandleW(handle,buffer,len(buffer),0)
        final=buffer.value
        if not 0<n<len(buffer) or final.startswith("\\\\?\\UNC\\"):raise PermissionError("无法核对锁定原文件的真实位置")
        if final.startswith("\\\\?\\"):final=final[4:]
        if Path(final).resolve()!=path:raise PermissionError("原文件位置发生变化，保留备份")
        fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY);handle=None
        with os.fdopen(fd,"rb") as stream:yield stream
    finally:
        if handle is not None:api.CloseHandle(handle)
