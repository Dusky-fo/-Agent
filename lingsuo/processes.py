"""Read the Windows process snapshot without opening or controlling any process."""
import csv
import io
import locale
import os
import re


def _decode_output(raw):
    if isinstance(raw, str):
        return raw
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    encodings = ['utf-8-sig']
    if os.name == 'nt':
        import ctypes
        encodings.append('cp%d' % ctypes.windll.kernel32.GetOEMCP())
    encodings.extend([locale.getpreferredencoding(False), 'gbk'])
    for encoding in dict.fromkeys(encodings):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise OSError('无法解码 Windows 进程列表')


def parse_tasklist(text):
    """Keep every PID, including multiple instances of the same executable."""
    result, seen = [], set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[0].strip():
            continue
        try:
            pid = int(row[1].strip())
        except ValueError:
            continue
        if pid < 0 or pid in seen:
            continue
        seen.add(pid)
        memory = row[4].strip() if len(row) >= 5 else ''
        digits = re.sub(r'\D', '', memory)
        result.append({'name': row[0].strip(), 'pid': pid,
                       'memory': memory or '—',
                       'memory_bytes': int(digits) * 1024 if digits else None})
    if not result:
        raise OSError('Windows 未返回有效进程列表，请刷新重试')
    return sorted(result, key=lambda p: (p['name'].casefold(), p['pid']))


def list_processes():
    """Collect in-process through Toolhelp; never spawn tasklist or a helper."""
    if os.name != 'nt':
        raise OSError('进程采集仅支持 Windows')
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        fn = getattr(api, name)
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        fn.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError('Windows 进程快照失败，请稍后重试')
    try:
        row = Entry(); row.dwSize = ctypes.sizeof(Entry)
        rows = []
        if not api.Process32FirstW(handle, ctypes.byref(row)):
            raise OSError('Windows 未返回进程快照，请刷新重试')
        while True:
            if row.szExeFile:
                rows.append({'name': row.szExeFile, 'pid': row.th32ProcessID,
                             'memory': '—', 'memory_bytes': None})
            if not api.Process32NextW(handle, ctypes.byref(row)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    raise OSError('Windows 进程快照未完成，请刷新重试')
                break
        return sorted(rows, key=lambda p: (p['name'].casefold(), p['pid']))
    finally:
        api.CloseHandle(handle)


def visible_window_pids():
    """Classify visible applications without activating, messaging, or changing windows."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.IsWindowVisible.argtypes = [wintypes.HWND]; user.IsWindowVisible.restype = wintypes.BOOL
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]; user.EnumWindows.restype = wintypes.BOOL
    pids = set()
    def visit(handle, _):
        rect, pid = wintypes.RECT(), wintypes.DWORD()
        if user.IsWindowVisible(handle) and user.GetWindowRect(handle, ctypes.byref(rect)):
            if rect.right - rect.left >= 60 and rect.bottom - rect.top >= 40:
                user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
                if pid.value: pids.add(pid.value)
        return True
    if not user.EnumWindows(callback_type(visit), 0): return None
    return pids


def working_set(pid):
    """Read a process memory counter; access-denied stays unknown."""
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
    if not handle: return None
    try:
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]; api.restype = wintypes.BOOL
        counter = Counters(); counter.cb = ctypes.sizeof(counter)
        return counter.WorkingSetSize if api(handle, ctypes.byref(counter), counter.cb) else None
    finally: kernel.CloseHandle(handle)


def process_catalog(collector=list_processes, windows=visible_window_pids, memory=working_set):
    rows = collector()
    try: visible = windows()
    except OSError: visible = None
    result = []
    for row in rows:
        try: value = memory(row["pid"])
        except OSError: value = None
        result.append({**row, "has_window":row["pid"] in visible if visible is not None else None,
                       "memory_bytes":value, "memory":"{:,} K".format(value // 1024) if value is not None else "无权限 / 未知"})
    return result
