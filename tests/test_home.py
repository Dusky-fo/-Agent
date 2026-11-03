"""Overview must not mistake saved settings or unknown data for healthy services."""
import copy
import unittest
from lingsuo.config import DEFAULT
from lingsuo.desktop_home import overview_states, overview_items, overview_program_rows, overview_tables


class OverviewStateTests(unittest.TestCase):
    def setUp(self):
        self.cfg=copy.deepcopy(DEFAULT)
        self.status={"local_running":False,"monitor_error":"","processes":[],"qq":{"connected":False,"message":"尚未连接"},"users":[],"roots":[]}

    def test_monitor_saved_unknown_paused_and_running_are_distinct(self):
        self.cfg["monitor"]["process_names"]=["fixture.exe"]
        self.assertEqual(overview_states(self.status,self.cfg)["monitor"][0],"未运行")
        self.status.update(local_running=True,monitor_error="进程采集失败")
        self.assertEqual(overview_states(self.status,self.cfg)["monitor"][0],"状态未知")
        self.cfg["monitor"]["enabled"]=False
        self.assertEqual(overview_states(self.status,self.cfg)["monitor"][0],"已暂停")
        self.cfg["monitor"]["enabled"]=True;self.status["monitor_error"]=""
        state=overview_states(self.status,self.cfg)["monitor"]
        self.assertEqual(state[0],"监控中");self.assertIn("0 个运行实例",state[1])

    def test_connected_is_not_bound_or_file_authorized(self):
        self.status["qq"]["connected"]=True
        values=overview_states(self.status,self.cfg)
        self.assertEqual(values["qq"][0],"已连接")
        self.assertEqual(values["qq"][2],"完成绑定")
        self.assertEqual(values["qq"][3],"warn")
        self.assertEqual(values["files"][0],"未设置")
        self.status["users"]=[{"user":"fixture"}]
        self.status["roots"]=["temporary authorized folder"]
        values=overview_states(self.status,self.cfg)
        self.assertEqual(values["qq"][3],"ok")
        self.assertEqual(values["files"][0],"已授权")

    def test_failure_explanation_is_preserved(self):
        self.status["qq"]["message"]="凭据校验失败，请检查 AppID"
        self.assertEqual(overview_states(self.status,self.cfg)["qq"][1],self.status["qq"]["message"])

    def test_program_list_groups_instances_and_never_reuses_counts_when_unknown(self):
        self.cfg["monitor"]["process_names"]=["Fixture.exe","Other.exe"]
        self.status.update(local_running=True,processes=[{"name":"fixture.EXE","pid":1},{"name":"Fixture.exe","pid":2}])
        self.assertEqual(overview_items(self.status,self.cfg)["monitor"],["Fixture.exe · 2 个实例","Other.exe · 0 个实例"])
        self.status["monitor_error"]="采集失败"
        self.assertEqual(overview_items(self.status,self.cfg)["monitor"],["Fixture.exe · 状态未知","Other.exe · 状态未知"])
        self.cfg["monitor"]["enabled"]=False
        self.assertIn("已暂停",overview_items(self.status,self.cfg)["monitor"][0])

    def test_authorization_lists_only_effective_directories_without_expanding_drive(self):
        self.cfg["files"]["roots"]=[r"G:\unavailable"]
        self.assertEqual(overview_items(self.status,self.cfg)["files"],["尚未授权共享目录"])
        self.status["roots"]=[r"G:\资料\共享",r"C:\Users\Example\Desktop"]
        self.assertEqual(overview_items(self.status,self.cfg)["files"],[r"G 盘 · G:\资料\共享",r"C 盘 · C:\Users\Example\Desktop"])

    def test_table_merges_repeated_saved_names_without_duplicate_row_ids(self):
        self.cfg["monitor"]["process_names"]=["fixture.exe","Fixture.EXE","fixture.exe"]
        self.status.update(local_running=True,processes=[{"name":"fixture.exe","pid":1}])
        self.assertEqual(overview_program_rows(self.status,self.cfg),[("fixture.exe","1 个实例")])

    def test_unified_tables_do_not_expose_credentials_or_expand_authorized_scope(self):
        self.cfg["qq"].update(secret="fake-secret-never-display",appid="fake-app",is_sandbox=True)
        self.cfg["files"]["roots"]=[r"G:\unavailable"]
        self.status["roots"]=[r"G:\only-shared"]
        self.status["users"]=[{"user":"fake-openid","subscribed":True},{"user":"other-openid","subscribed":False}]
        tables=overview_tables(self.status,self.cfg)
        self.assertEqual(tables["files"],[("G:",r"G:\only-shared")])
        self.assertEqual(tables["qq"],[("运行环境","沙箱"),("本人授权","2 人"),("通知订阅","1 人")])
        self.assertNotIn("fake-secret",str(tables));self.assertNotIn("fake-openid",str(tables))
