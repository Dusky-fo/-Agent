"""Source and frozen entry point. Nothing starts merely by importing this file."""
import sys

if __name__ == "__main__":
    from lingsuo.app import main
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Startup errors only, without serializing configuration or HTTP requests.
        message = "灵梭启动失败：%s" % type(exc).__name__
        if sys.stderr:
            print(message, file=sys.stderr)
        if "--self-test" not in sys.argv and "--headless" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message + "\n请检查托盘是否已有灵梭，以及本机配置。", "灵梭", 0x10)
        raise SystemExit(1)
