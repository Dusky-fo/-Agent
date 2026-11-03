"""Exercise Toolhelp ABI with an injected kernel, without reading real processes."""
import ctypes
import unittest
from unittest.mock import patch
from lingsuo.processes import list_processes


class Kernel:
    def __init__(self, rows, error=18):
        self.rows = iter(rows)
        self.error = error
        self.closed = []
        # Callable instances accept ctypes signature attributes like native functions.
        class Fn:
            def __init__(self, fn): self.fn = fn
            def __call__(self, *args): return self.fn(*args)
        self.CreateToolhelp32Snapshot = Fn(lambda flags, pid: 71)
        self.Process32FirstW = Fn(self.next)
        self.Process32NextW = Fn(self.next)
        self.CloseHandle = Fn(lambda handle: self.closed.append(handle))

    def next(self, handle, pointer):
        row = next(self.rows, None)
        if row is None:
            ctypes.set_last_error(self.error)
            return False
        target = pointer._obj
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            assert target.dwSize == 568
        target.szExeFile, target.th32ProcessID = row
        return True


@unittest.skipUnless(hasattr(ctypes, "WinDLL"), "Windows native ABI only")
class NativeProcessTests(unittest.TestCase):
    def test_unicode_and_duplicate_program_names_without_query_child(self):
        kernel = Kernel([("中文.exe", 22), ("中文.exe", 23), ("a.exe", 10)])
        with patch("ctypes.WinDLL", return_value=kernel), patch("subprocess.Popen", side_effect=AssertionError("no helper")):
            rows = list_processes()
        self.assertEqual([r["pid"] for r in rows], [10, 22, 23])
        self.assertEqual(rows[1]["name"], "中文.exe")
        self.assertEqual(kernel.closed, [71])

    def test_incomplete_snapshot_raises_instead_of_false_exit(self):
        kernel = Kernel([("a.exe", 10)], error=5)
        with patch("ctypes.WinDLL", return_value=kernel), self.assertRaises(OSError):
            list_processes()
        self.assertEqual(kernel.closed, [71])

    def test_first_entry_failure_also_closes_handle(self):
        kernel = Kernel([])
        with patch("ctypes.WinDLL", return_value=kernel), self.assertRaises(OSError):
            list_processes()
        self.assertEqual(kernel.closed, [71])
