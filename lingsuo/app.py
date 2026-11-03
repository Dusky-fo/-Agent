import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path
from .paths import default_data_dir
from .runtime import Runtime
from .instance import RestoreSignal,request_restore


def tray_image():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (24, 42, 36, 255))
    draw = ImageDraw.Draw(image)
    draw.polygon([(15, 36), (37, 9), (30, 29), (50, 27), (25, 56), (31, 35)], fill=(184, 230, 207, 255))
    return image


def acquire_lock(directory):
    handle = (directory / "instance.lock").open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            raise RuntimeError("灵梭已在此数据目录运行，请从托盘打开主窗口。") from None
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description="灵梭基础版")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path, help="隔离自检报告路径（仅配合 --self-test）")
    parser.add_argument("--headless", action="store_true", help="只启动同进程后台，不显示桌面窗口")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--trust-plugin", help="明确审查并信任本地插件，保存后退出；不加载其代码")
    parser.add_argument("--disable-plugin", help="禁用本地插件，保存后退出")
    parser.add_argument("--wait-for-process",type=int,help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        from .selftest import run
        return run(args.report)
    if args.wait_for_process is not None:
        from .restart import wait_for_previous_process
        wait_for_previous_process(args.wait_for_process)
    directory = args.data_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:lock = acquire_lock(directory)
    except RuntimeError:
        if request_restore(directory):return 0
        raise
    app = Runtime(directory)
    if args.trust_plugin or args.disable_plugin:
        try:
            app.plugins.configure(args.trust_plugin or args.disable_plugin, bool(args.trust_plugin), bool(args.trust_plugin))
            print("插件设置已保存，启用需重新启动。")
            return 0
        finally:
            app.stop()
            lock.close()
    try:
        app.restore_signal=RestoreSignal(directory) if not args.headless else None
        app.start()
        if args.headless:
            print("灵梭后台正在本进程运行。", flush=True)
            while not app.stop_event.wait(1):
                pass
        else:
            from .desktop import run_desktop
            run_desktop(app)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        app.stop()
        if getattr(app,"restore_signal",None):app.restore_signal.close()
        lock.close()
