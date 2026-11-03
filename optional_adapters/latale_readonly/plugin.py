"""Optional read-only adapter. No game launch, injection, keys, or window actions."""
def status():
    from lingsuo.processes import list_processes
    names = {"lataleclient.exe", "lataleclient_x64.exe", "lataleclientx64.exe"}
    rows = [row for row in list_processes() if row["name"].casefold() in names]
    return {"running": bool(rows), "instances":len(rows), "processes":[{"name":r["name"], "pid":r["pid"]} for r in rows],
            "capability":"只读进程状态，不启动游戏，不控制窗口，不读取游戏内存，不执行挂机"}


def register(registry):
    registry.register("latale_readonly.status", "只读查看彩虹岛客户端进程和实例数", status)
