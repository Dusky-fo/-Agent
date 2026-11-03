"""Opt-in headless UI check. Requires Playwright; never starts the real QQ gateway."""
import json
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lingsuo.runtime import Runtime
from lingsuo.panel import serve
from test_core import FakeQQ
from test_guidance import FakeGateway


class OfflineCredentials:
    def __init__(self, config): pass
    def check_credentials(self): return {"ok": True, "message": "凭据检查通过（隔离测试接口）"}


def run():
    from playwright.sync_api import sync_playwright, expect
    output = Path(__file__).resolve().parents[1] / "work/guidance-ui"
    output.mkdir(parents=True, exist_ok=True)
    failures, requests = [], []
    original_connect = socket.socket.connect
    def guarded_connect(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        raise AssertionError("External connections forbidden in UI fixture")
    with patch.object(socket.socket, "connect", guarded_connect), tempfile.TemporaryDirectory(prefix="lingsuo-ui-") as directory:
        tmp = Path(directory)
        shared = tmp / "shared"; shared.mkdir(); (shared / "fixture.txt").write_text("temporary file only", encoding="utf-8")
        qq = FakeQQ()
        processes = lambda: [{"name": "fixture.exe", "pid": 77}, {"name": "fixture.exe", "pid": 78}, {"name": "other.exe", "pid": 79}]
        app = Runtime(tmp / "data", collector=processes, transport=qq, desktop=lambda: shared,
                      gateway_factory=FakeGateway, transport_factory=OfflineCredentials)
        server, url = serve(app, port=0, collector=processes)
        app.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True,
                    args=["--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run",
                          "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"])
                context = browser.new_context(viewport={"width": 1280, "height": 960}, color_scheme="dark")
                def route(req):
                    if urlsplit(req.request.url).hostname == "127.0.0.1": req.continue_()
                    else: requests.append(req.request.url); req.abort()
                context.route("**/*", route)
                page = context.new_page(); page.on("pageerror", lambda error: failures.append(str(error)))
                page.goto(url)
                expect(page.locator('#setupProgress')).to_have_text('0 / 4 项已就绪')
                page.screenshot(path=str(output / 'home-dark.png'), full_page=True)
                page.get_by_role('button', name='第一次使用？查看帮助', exact=True).click()
                expect(page.locator('#help-first')).to_have_attribute('open', '')
                page.locator('#helpQuery').fill('配对码')
                expect(page.locator('#help-binding')).to_be_visible()
                page.locator('#nav-monitor').click(); page.locator('#refreshProcesses').click()
                page.locator('#processQuery').fill('fixture')
                expect(page.locator('.process-item')).to_have_count(1)
                page.locator('.process-item input').check(); page.locator('#saveMonitor').click()
                expect(page.locator('#monitorFeedback')).to_contain_text('已保存')
                assert app.config.get()['monitor']['process_names'] == ['fixture.exe']
                page.locator('#nav-notifications').click()
                page.locator('#appid').fill('fixture-app'); page.locator('#secret').fill('fixture-secret')
                page.locator('#connectQQ').click()
                expect(page.locator('#qqState')).to_have_text('offline test double')
                page.locator('#startPairing').click()
                expect(page.locator('#pairingState')).to_contain_text('绑定窗口已开启')
                app.receive('fixture-user', '开启', 'ui-message-start')
                expect(page.locator('.pair-code')).to_be_visible(timeout=7000)
                row = app.pairing.status()['requests'][0]
                assert row['code'] in qq.texts[-1][1]
                page.on('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='这是我，确认绑定', exact=True).click()
                expect(page.locator('#bindingFeedback')).to_contain_text('已绑定')
                assert app.config.get()['qq']['allowed_users'] == ['fixture-user']
                page.locator('#testNotify').click()
                expect(page.locator('#testFeedback')).to_contain_text('尚不代表手机收到')
                app.flush_once()
                received = page.get_by_role('button', name='我确认已收到此编号通知', exact=True)
                expect(received).to_be_visible(timeout=7000); received.click()
                expect(page.locator('#testResults')).to_contain_text('用户已确认收到')
                page.locator('#nav-files').click(); page.locator('#roots').fill(str(shared)); page.locator('#saveFiles').click()
                expect(page.locator('#filesFeedback')).to_contain_text('已保存')
                page.locator('#query').fill('fixture'); page.locator('#searchFiles').click()
                page.get_by_role('button', name='选择发送', exact=True).click()
                expect(page.locator('#confirmDetails')).to_contain_text('fixture-user')
                page.locator('#confirmSend').click()
                expect(page.locator('#filesFeedback')).to_contain_text('平台')
                assert len(qq.files) == 1
                page.locator('#nav-home').click(); expect(page.locator('#setupProgress')).to_have_text('4 / 4 项已就绪')
                page.locator('#nav-help').click(); page.locator('#helpQuery').fill('')
                page.locator('#help-binding summary').click()
                page.screenshot(path=str(output / 'help-dark.png'), full_page=True)
                page.set_viewport_size({'width':390, 'height':844})
                page.locator('#nav-home').click()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile horizontal overflow'
                page.screenshot(path=str(output / 'home-mobile.png'), full_page=True)
                assert not failures, failures
                assert not requests, requests
                browser.close()
        finally:
            server.shutdown(); server.server_close(); app.stop()
    result = {'ok':True, 'javascript_errors':failures, 'external_requests':requests,
              'flows':['actual-home-checklist','help-search','searchable-process-picker','save-and-connect','local-pair-confirmation',
                       'accepted-vs-confirmed-receipt','file-confirm-send','390px-responsive-layout'],
              'real_qq':False, 'real_user_files':False, 'headless_only':True}
    (output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__': run()
