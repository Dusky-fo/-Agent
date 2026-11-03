"""Synthetic pixels and injected input/keyboard backends only; never click the desktop."""
import ctypes
import queue
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from lingsuo.automation import AutomationEngine, ClickPlan, HotkeyToggle, TemplateMatcher, WindowsMouse
from lingsuo.window_layout import centered_position


class FakeMouse:
    def __init__(self,image=None):self.clicks=[];self.image=image;self.capture_count=0;self.cancel=False
    def click(self,*args):self.clicks.append(args)
    def stop_requested(self):return self.cancel
    def capture(self,region):self.capture_count+=1;return self.image
    def cursor(self):return (12,34)


class FakeHotkey:
    def __init__(self,key):self.key=key;self.presses=queue.Queue();self.opened=False;self.closed=False
    def open(self):self.opened=True
    def close(self):self.closed=True
    def poll(self):
        try:return self.presses.get_nowait()
        except queue.Empty:return False
    def press(self):self.presses.put(True)


class AutomationTests(unittest.TestCase):
    def test_removed_autoclick_modes_cannot_start_or_create_tasks(self):
        engine=AutomationEngine(backend=FakeMouse());self.addCleanup(engine.stop)
        for mode in ("repeat","cursor","fixed"):
            with self.assertRaises(ValueError):engine.start(ClickPlan(mode=mode,template="fixture"))
        self.assertIsNone(engine.thread)
        self.assertFalse(hasattr(WindowsMouse,"click_current"))

    def test_image_delay_and_emergency_stop_perform_no_input(self):
        mouse=FakeMouse();engine=AutomationEngine(backend=mouse);self.addCleanup(engine.stop)
        engine.start(ClickPlan(template="fixture",delay_s=3));engine.stop()
        self.assertEqual(mouse.clicks,[])
        mouse.cancel=True;engine.start(ClickPlan(template="fixture",delay_s=0));engine.thread.join(2)
        self.assertEqual(mouse.clicks,[]);self.assertFalse(engine.status()["running"])





    def test_invalid_plan_never_starts_and_duplicate_start_rejected(self):
        engine=AutomationEngine(backend=FakeMouse());self.addCleanup(engine.stop)
        for plan in (ClickPlan(interval_ms=0),ClickPlan(count=0),ClickPlan(mode="image"),ClickPlan(delay_s=float("nan"))):
            with self.assertRaises(ValueError):engine.start(plan)
            self.assertIsNone(engine.thread)
        engine.start(ClickPlan(template="fixture",delay_s=3))
        with self.assertRaises(ValueError):engine.start(ClickPlan(template="fixture"))

    def test_template_unique_ambiguous_and_absent_match(self):
        matcher=TemplateMatcher();rng=np.random.default_rng(3)
        template=rng.integers(0,256,(10,12,3),dtype=np.uint8)
        source=np.zeros((60,80,3),dtype=np.uint8);source[15:25,20:32]=template
        point,note=matcher.locate(Image.fromarray(source),template,.99)
        self.assertEqual(point,(26,20))
        source[40:50,50:62]=template
        self.assertIsNone(matcher.locate(Image.fromarray(source),template,.99)[0])
        self.assertIn("多个",matcher.locate(Image.fromarray(source),template,.99)[1])
        self.assertIsNone(matcher.locate(Image.new("RGB",(60,80)),template,.99)[0])

    def test_image_engine_uses_region_offset_and_no_match_never_clicks(self):
        class Matcher:
            def load(self,path):return object()
            def locate(self,image,template,threshold):return ((4,6),"match")
        mouse=FakeMouse(Image.new("RGB",(30,30)));engine=AutomationEngine(backend=mouse,matcher=Matcher());self.addCleanup(engine.stop)
        engine.start(ClickPlan(mode="image",template="test-only",region=(100,200,30,30),count=1,delay_s=0))
        engine.thread.join(2)
        self.assertEqual(mouse.clicks,[(104,206,"left")])
        mouse.clicks.clear()
        engine.matcher.locate=lambda *args:(None,"absent")
        engine.start(ClickPlan(mode="image",template="test-only",region=(0,0,30,30),duration_s=1,delay_s=0,interval_ms=100))
        engine.thread.join(2)
        self.assertEqual(mouse.clicks,[])
        self.assertFalse(engine.status()["running"])

    def test_stop_during_recognition_cannot_click_after_return(self):
        mouse=FakeMouse(Image.new("RGB",(30,30)))
        class Matcher:
            def load(self,path):return object()
            def locate(self,*args):engine.stop_event.set();return ((10,10),"match")
        engine=AutomationEngine(backend=mouse,matcher=Matcher());self.addCleanup(engine.stop)
        engine.start(ClickPlan(mode="image",template="test",region=(0,0,30,30),delay_s=0))
        engine.thread.join(2);self.assertEqual(mouse.clicks,[])

    def test_template_rejects_solid_or_transparent_images(self):
        matcher=TemplateMatcher()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"solid.png";Image.new("RGB",(20,20),(255,0,0)).save(path)
            with self.assertRaisesRegex(ValueError,"细节"):matcher.load(path)
            path=Path(directory)/"transparent.png";Image.new("RGBA",(20,20),(255,0,0,0)).save(path)
            with self.assertRaisesRegex(ValueError,"透明"):matcher.load(path)

    def test_win32_input_structure_coordinates_and_partial_release_with_fake_api(self):
        class Api:
            calls=[];result=3
            def GetSystemMetrics(self,index):return {76:-1000,77:0,78:2000,79:1000}[index]
            def SendInput(self,count,events,size):
                self.calls.append((count,[(events[i].mi.dx,events[i].mi.dy,events[i].mi.dwFlags) for i in range(count)] if count==3 else [],size))
                return self.result if count==3 else 1
        api=Api();mouse=WindowsMouse(api=api)
        mouse.click(-500,100,"left")
        self.assertEqual(api.calls[0][1][0],(round(500*65535/1999),round(100*65535/999),0xC001))
        self.assertEqual(api.calls[0][1][1][2],2);self.assertEqual(api.calls[0][1][2][2],4)
        self.assertEqual(ctypes.sizeof(mouse.Input),40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
        with self.assertRaises(ValueError):mouse.click(-1001,0,"left")
        api.result=2
        with self.assertRaises(OSError):mouse.click(0,0,"left")
        self.assertEqual(api.calls[-1][0],1)

    def test_hotkey_registration_does_not_run_until_press_and_closes(self):
        pumps=[];events=[]
        def factory(key):pumps.append(FakeHotkey(key));return pumps[-1]
        listener=HotkeyToggle(factory);self.addCleanup(listener.stop)
        listener.bind("F6",lambda:events.append("toggle"))
        self.assertEqual(events,[])
        pumps[0].press()
        end=time.monotonic()+1
        while not events and time.monotonic()<end:time.sleep(.01)
        self.assertEqual(events,["toggle"])
        listener.stop();self.assertTrue(pumps[0].closed)

    def test_hotkey_conflict_never_enables_or_calls_toggle(self):
        calls=[]
        class Conflict:
            def __init__(self,key):pass
            def open(self):raise ValueError("快捷键已被占用")
            def close(self):pass
        listener=HotkeyToggle(Conflict);self.addCleanup(listener.stop)
        with self.assertRaisesRegex(ValueError,"占用"):listener.bind("F6",lambda:calls.append(1))
        self.assertEqual(calls,[])

    def test_dialog_centers_relative_to_parent_and_clamps_own_monitor(self):
        self.assertEqual(centered_position((100,100,1000,800),(600,400),(0,0,1920,1080)),(300,300))
        self.assertEqual(centered_position((-1800,50,1000,800),(600,400),(-1920,0,0,1080)),(-1600,250))
        self.assertEqual(centered_position((1700,900,200,100),(600,400),(0,0,1920,1040)),(1320,640))
