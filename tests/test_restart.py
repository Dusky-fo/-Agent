"""No real elevation, child processes, termination, or runtime user data."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from lingsuo.app import main
from lingsuo.restart import request_admin_restart,wait_for_previous_process


class AdminRestartTests(unittest.TestCase):
    def test_source_restart_preserves_data_and_uses_project_isolated_launcher(self):
        with tempfile.TemporaryDirectory() as data,patch("bootstrap.elevation_request") as request:
            request_admin_restart(data)
            executable,args,working=request.call_args.args
            self.assertEqual(executable.name,"pythonw.exe")
            self.assertEqual(args[:2],["-I",str(working/"bootstrap.py")])
            self.assertEqual(args[args.index("--data-dir")+1],str(Path(data).resolve()))
            self.assertEqual(args[args.index("--wait-for-process")+1],str(os.getpid()))
            self.assertIn("--elevation-attempted",args)
            self.assertNotIn("--no-admin",args)

    def test_frozen_restart_uses_current_executable(self):
        with tempfile.TemporaryDirectory() as data,patch.object(sys,"frozen",True,create=True),patch("bootstrap.elevation_request") as request:
            request_admin_restart(data)
            executable,args,_=request.call_args.args
            self.assertEqual(executable,Path(sys.executable).resolve())
            self.assertNotIn("-I",args)
            self.assertIn("--wait-for-process",args)

    def test_wait_has_synchronize_only_access_and_always_closes_handle(self):
        api=MagicMock();api.OpenProcess.return_value=77;api.WaitForSingleObject.return_value=0
        wait_for_previous_process(812345,api=api)
        api.OpenProcess.assert_called_once_with(0x00100000,False,812345)
        api.WaitForSingleObject.assert_called_once_with(77,120000)
        api.CloseHandle.assert_called_once_with(77)
        api.reset_mock();api.WaitForSingleObject.return_value=258
        with self.assertRaises(TimeoutError):wait_for_previous_process(812345,api=api)
        api.CloseHandle.assert_called_once_with(77)

    def test_exited_parent_and_access_denied_are_not_conflated(self):
        api=MagicMock();api.OpenProcess.return_value=0
        wait_for_previous_process(812345,api=api,last_error=lambda:87)
        with self.assertRaises(OSError):wait_for_previous_process(812345,api=api,last_error=lambda:5)
        api.WaitForSingleObject.assert_not_called()
        with self.assertRaises(ValueError):wait_for_previous_process(os.getpid(),api=api)

    def test_failed_handoff_does_not_create_runtime_or_acquire_data_lock(self):
        with tempfile.TemporaryDirectory() as data,patch("lingsuo.restart.wait_for_previous_process",side_effect=TimeoutError("fixture")) as wait,patch("lingsuo.app.Runtime") as runtime,patch("lingsuo.app.acquire_lock") as lock:
            with self.assertRaises(TimeoutError):main(["--data-dir",data,"--wait-for-process","812345"])
            wait.assert_called_once_with(812345)
            runtime.assert_not_called();lock.assert_not_called()
