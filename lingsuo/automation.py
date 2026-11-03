"""Opt-in local mouse tasks; no input, screen reads or threads at import/startup."""
import ctypes
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClickPlan:
    mode: str = "image"
    interval_ms: int = 500
    count: int = 10
    duration_s: int = 60
    delay_s: float = 3
    button: str = "left"
    template: str = ""
    region: tuple = (0, 0, 800, 600)
    threshold: float = .98

    def validate(self):
        if self.mode != "image" or self.button not in {"left", "right"}:
            raise ValueError("点击模式或鼠标按键无效")
        for value, low, high, name in ((self.interval_ms, 100, 60000, "点击间隔（毫秒）"), (self.count, 1, 10000, "次数"),
                                       (self.duration_s, 1, 3600, "最长运行秒数")):
            if type(value) is not int or not low <= value <= high: raise ValueError("参数范围不正确：" + name)
        if not isinstance(self.delay_s, (int, float)) or not math.isfinite(self.delay_s) or not 0 <= self.delay_s <= 30:
            raise ValueError("启动延时须为 0–30 秒")
        if self.mode == "image":
            if not isinstance(self.threshold, (int, float)) or not math.isfinite(self.threshold) or not .8 <= self.threshold <= 1:
                raise ValueError("相似度须为 0.80–1.00")
            if not isinstance(self.region, tuple) or len(self.region) != 4 or any(type(v) is not int for v in self.region):
                raise ValueError("识别范围须为 X、Y、宽、高四个整数")
            x, y, w, h = self.region
            if not (-65536 <= x <= 65536 and -65536 <= y <= 65536 and 8 <= w <= 8192 and 8 <= h <= 8192 and w*h <= 8_000_000):
                raise ValueError("识别区域无效或超过 800 万像素")
            if not self.template: raise ValueError("请选择目标图片")
        return self


class WindowsMouse:
    def __init__(self, api=None):
        from ctypes import wintypes as w
        class Mouse(ctypes.Structure):
            _fields_ = [("dx",w.LONG),("dy",w.LONG),("mouseData",w.DWORD),("dwFlags",w.DWORD),("time",w.DWORD),("dwExtraInfo",ctypes.c_size_t)]
        class Keyboard(ctypes.Structure):
            _fields_ = [("wVk",w.WORD),("wScan",w.WORD),("dwFlags",w.DWORD),("time",w.DWORD),("dwExtraInfo",ctypes.c_size_t)]
        class Hardware(ctypes.Structure):
            _fields_ = [("uMsg",w.DWORD),("wParamL",w.WORD),("wParamH",w.WORD)]
        class Union(ctypes.Union):
            _fields_ = [("mi",Mouse),("ki",Keyboard),("hi",Hardware)]
        class Input(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type",w.DWORD),("u",Union)]
        self.Input, self.Mouse = Input, Mouse
        self.api = api or ctypes.windll.user32
        if api is None:
            if hasattr(self.api,"SetThreadDpiAwarenessContext"):
                self.api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                self.api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
                self.api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            self.api.SendInput.argtypes = [w.UINT,ctypes.POINTER(Input),ctypes.c_int]; self.api.SendInput.restype = w.UINT
            self.api.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]; self.api.GetCursorPos.restype = w.BOOL
            self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]; self.api.GetAsyncKeyState.restype = ctypes.c_short

    def bounds(self):
        return tuple(self.api.GetSystemMetrics(n) for n in (76,77,78,79))

    def cursor(self):
        from ctypes import wintypes
        point = wintypes.POINT()
        if not self.api.GetCursorPos(ctypes.byref(point)): raise OSError("无法读取鼠标位置")
        return point.x, point.y

    def stop_requested(self):
        if self.api.GetAsyncKeyState(0x1B) & 0x8000: return True
        x, y = self.cursor(); left, top, _, _ = self.bounds()
        return x <= left + 2 and y <= top + 2

    def click(self, x, y, button):
        left, top, width, height = self.bounds()
        if not left <= x < left+width or not top <= y < top+height:
            raise ValueError("点击位置超出当前桌面范围")
        if width < 2 or height < 2: raise ValueError("桌面范围不可用")
        nx, ny = round((x-left)*65535/(width-1)), round((y-top)*65535/(height-1))
        down, up = (2,4) if button == "left" else (8,16)
        events = (self.Input * 3)()
        for i, (dx,dy,flags) in enumerate(((nx,ny,0xC001),(0,0,down),(0,0,up))):
            events[i].type = 0; events[i].mi = self.Mouse(dx,dy,0,flags,0,0)
        inserted = self.api.SendInput(3, events, ctypes.sizeof(self.Input))
        if inserted != 3:
            if inserted == 2:
                release = self.Input(); release.type = 0; release.mi = self.Mouse(0,0,0,up,0,0)
                self.api.SendInput(1, ctypes.byref(release), ctypes.sizeof(self.Input))
            raise OSError("系统未完整接受点击，任务已停止；请检查窗口权限或当前桌面状态")

    def capture(self, region):
        from PIL import ImageGrab
        x,y,w,h = region
        left,top,width,height = self.bounds()
        if x < left or y < top or x+w > left+width or y+h > top+height:
            raise ValueError("识别范围超出当前桌面，请重新填写 X、Y、宽、高")
        return ImageGrab.grab(bbox=(x,y,x+w,y+h), all_screens=True)



class WindowsHotkey:
    def __init__(self, key):
        from ctypes import wintypes as w
        if key not in {"F%s" % n for n in range(2,12)}: raise ValueError("请设置 F2–F11 中的一个启停快捷键")
        self.api = ctypes.windll.user32; self.message = w.MSG()
        self.api.RegisterHotKey.argtypes = [w.HWND,ctypes.c_int,w.UINT,w.UINT]; self.api.RegisterHotKey.restype = w.BOOL
        self.api.UnregisterHotKey.argtypes = [w.HWND,ctypes.c_int]
        self.api.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG),w.HWND,w.UINT,w.UINT,w.UINT]
        self.api.PeekMessageW.restype = w.BOOL
        self.key = key

    def open(self):
        self.api.PeekMessageW(ctypes.byref(self.message),None,0,0,0)
        if not self.api.RegisterHotKey(None,0x5A31,0x4000,0x70+int(self.key[1:])-1):
            raise ValueError("快捷键已被占用或无法注册，请换一个键；点击任务未启用")

    def poll(self):
        pressed = False
        while self.api.PeekMessageW(ctypes.byref(self.message),None,0,0,1):
            if self.message.message == 0x312 and self.message.wParam == 0x5A31: pressed = True
        return pressed

    def close(self): self.api.UnregisterHotKey(None,0x5A31)


class HotkeyToggle:
    """No registration until explicit local configuration; callback does not inject input."""
    def __init__(self, factory=WindowsHotkey):
        self.factory = factory; self.stop_event = threading.Event(); self.ready = threading.Event()
        self.thread = None; self.error = None

    def bind(self, key, callback):
        if self.thread and self.thread.is_alive(): raise ValueError("快捷键仍在使用，请先关闭旧配置")
        self.stop_event.clear(); self.ready.clear(); self.error = None
        def listen():
            pump = None; opened = False
            try:
                pump = self.factory(key); pump.open(); opened = True; self.ready.set()
                while not self.stop_event.wait(.03):
                    if pump.poll(): callback()
            except Exception as exc:
                self.error = str(exc) if isinstance(exc,ValueError) else "快捷键监听失败，点击任务未启用"
                self.ready.set()
            finally:
                if opened: pump.close()
        self.thread = threading.Thread(target=listen,daemon=True,name="local-toggle-hotkey"); self.thread.start()
        if not self.ready.wait(2):
            self.stop(); raise ValueError("快捷键注册超时，点击任务未启用")
        if self.error: raise ValueError(self.error)

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(2)


class TemplateMatcher:
    def load(self, path):
        import numpy as np
        from PIL import Image
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 10*1024**2: raise ValueError("目标图片不存在或超过 10 MB")
        with Image.open(path) as image:
            if image.format not in {"PNG","JPEG","BMP"} or not (8 <= image.width <= 1024 and 8 <= image.height <= 1024):
                raise ValueError("请选择 8–1024 像素的 PNG、JPEG 或 BMP 小图")
            if image.convert("RGBA").getextrema()[3][0] < 255:
                raise ValueError("目标图片须包含实际背景，不能使用透明图片")
            result = np.asarray(image.convert("RGB")).copy()
        if float(result.std(axis=(0,1)).max()) < 5: raise ValueError("目标图片缺少可识别细节，请截取包含文字或图案的小图")
        return result

    def locate(self, image, template, threshold):
        import cv2
        import numpy as np
        source = np.asarray(image.convert("RGB"))
        h,w = template.shape[:2]
        if source.shape[0] < h or source.shape[1] < w: raise ValueError("目标图片比识别区域更大")
        scores = cv2.matchTemplate(source, template, cv2.TM_SQDIFF_NORMED)
        low, _, point, _ = cv2.minMaxLoc(scores)
        if not math.isfinite(low) or 1-low < threshold: return None, "未找到满足相似度的目标"
        x,y = point
        scores[max(0,y-h+1):y+h, max(0,x-w+1):x+w] = 2
        second = float(scores.min())
        if 1-second >= threshold: return None, "有多个匹配目标，请缩小识别范围"
        return (x+w//2,y+h//2), "匹配相似度 %.3f" % (1-low)


class AutomationEngine:
    def __init__(self, backend=None, matcher=None, tasks=None):
        self.backend, self.matcher = backend, matcher or TemplateMatcher()
        self.tasks,self.task_id=tasks,None
        self.lock = threading.RLock(); self.stop_event = threading.Event(); self.thread = None
        self.state = {"running":False,"message":"尚未启动点击任务","clicks":0}

    def status(self):
        with self.lock: return dict(self.state)

    def update(self, **values):
        with self.lock: self.state.update(values)

    def start(self, plan):
        plan.validate()
        with self.lock:
            if self.thread and self.thread.is_alive(): raise ValueError("已有点击任务运行，请先停止")
            self.stop_event.clear()
            if self.tasks:
                self.task_id=self.tasks.create("mouse."+plan.mode)
                self.tasks.update(self.task_id,state="running",step="本机已明确启动，等待启动延时")
            self.state = {"running":True,"message":"准备启动；再次按启停键或 Esc 可停止","clicks":0}
            self.thread = threading.Thread(target=self.run, args=(plan,), daemon=True, name="local-click-task")
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(2)

    def interrupted(self, backend):
        if self.tasks and self.task_id and self.tasks.get(self.task_id)["cancel_requested"]:self.stop_event.set()
        if self.stop_event.is_set() or backend.stop_requested():
            self.stop_event.set(); return True
        return False

    def wait(self, seconds, backend):
        deadline = time.monotonic()+seconds
        while not self.interrupted(backend):
            remaining = deadline-time.monotonic()
            if remaining <= 0: return False
            self.stop_event.wait(min(.03, remaining))
        return True

    def run(self, plan):
        failure="";reason="duration_limit"
        try:
            backend = self.backend or WindowsMouse()
            if self.wait(plan.delay_s, backend): return
            deadline = time.monotonic()+plan.duration_s
            template = self.matcher.load(plan.template)
            while not self.interrupted(backend) and time.monotonic() < deadline:
                image = backend.capture(plan.region)
                target, note = self.matcher.locate(image, template, plan.threshold)
                self.update(message=note)
                if target: target = (target[0]+plan.region[0],target[1]+plan.region[1])
                if target and not self.interrupted(backend) and time.monotonic() < deadline:
                    backend.click(*target,plan.button)
                    count = self.status()["clicks"]+1
                    self.update(clicks=count, message="已点击 %s 次；再次按启停键或 Esc 停止" % count)
                    if self.tasks:self.tasks.update(self.task_id,progress=min(99,int(100*count/plan.count)),step="已点击 %s/%s 次"%(count,plan.count))
                    if count >= plan.count:
                        reason="count_limit";self.update(message="已完成 %s 次点击" % count); return
                if self.wait(plan.interval_ms/1000, backend): return
            if not self.stop_event.is_set(): self.update(message="已达到最长运行时间，任务停止")
        except Exception as exc:
            failure="点击任务失败，请检查参数或图像组件"
            # File paths, pixels, and raw exception bodies are never logged.
            self.update(message=str(exc) if isinstance(exc, (ValueError,OSError,ImportError)) else "点击任务失败，已停止；请检查参数和图像组件")
        finally:
            if self.stop_event.is_set(): self.update(message="点击任务已停止")
            self.update(running=False)
            if self.tasks and self.task_id:
                import json
                state="failed" if failure else "cancelled" if self.stop_event.is_set() else "completed"
                self.tasks.update(self.task_id,state=state,progress=100 if state=="completed" else min(99,self.tasks.get(self.task_id)["progress"]),step=self.status()["message"],error=failure,result=json.dumps({"clicks":self.status()["clicks"],"stop_reason":"requested" if self.stop_event.is_set() else reason}))
