"""Application resources, private local data, and Windows Known Folders."""
import os
import sys
from pathlib import Path


def resource_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LingSuoBasic"


def desktop_dir():
    if os.name == "nt":
        import ctypes
        import uuid
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
        value = ctypes.c_wchar_p()
        shell = ctypes.windll.shell32
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value)) == 0:
            try:
                return Path(value.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))
    return Path.home() / "Desktop"
