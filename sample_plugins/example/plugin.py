"""No file, UI, process, or network operations."""


def register(registry):
    registry.register("example.ping", "检查示例插件", lambda: {"ok": True, "message": "示例插件运行正常"})
