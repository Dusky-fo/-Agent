"""Small, explicit Chinese intent rules. No network, model, or tool execution."""
import re
from dataclasses import dataclass


def clean_phrase(text):
    return text.strip().rstrip("。！？!?，,～~").strip()


ALIASES = {
    "帮我看看状态":"状态", "看看状态":"状态", "查看状态":"状态", "监控状态":"状态",
    "监控怎么样了":"状态", "监控还在吗":"状态", "现在怎么样":"状态", "看看监控":"状态",
    "打开通知":"开启通知", "开启提醒":"开启通知", "打开提醒":"开启通知", "有变化告诉我":"开启通知",
    "有变化通知我":"开启通知", "提醒我进程变化":"开启通知",
    "关闭通知":"停止通知", "关掉通知":"停止通知", "关闭提醒":"停止通知", "关掉提醒":"停止通知",
    "停止提醒":"停止通知", "不要通知我":"停止通知", "别提醒我了":"停止通知",
    "怎么用":"帮助", "你能做什么":"帮助", "使用说明":"帮助", "查看历史":"历史", "看看记录":"历史",
}
CONFIRMATIONS = {"发送", "确认发送", "发吧", "发给我", "发送给我", "就发这个", "确认发这个", "可以发了"}
CANCELLATIONS = {"取消", "取消发送", "取消文件", "不发了", "先别发", "不要发", "别发", "不用发了", "算了"}


def canonical(text):
    phrase = clean_phrase(text)
    if phrase in CANCELLATIONS or re.fullmatch(r"(?:我)?(?:不想|不要|不用|先别|别|暂时不).{0,50}(?:发|发送|传).{0,20}", phrase):
        return "取消"
    return ALIASES.get(phrase, phrase)


def selection_number(text):
    match = re.fullmatch(r"(?:选择|选|我要|发送|发|就|要)?第?([0-9]{1,2}|[一二三四五六七八九十两]{1,3})(?:个|号|项|份)?(?:文件)?吧?", clean_phrase(text))
    if not match: return None
    value = match[1]
    if value.isdecimal(): return int(value)
    digits = dict(zip("一二三四五六七八九", range(1, 10))); digits["两"] = 2
    if value in digits: return digits[value]
    if value == "十": return 10
    if value.startswith("十") and value[1:] in digits: return 10 + digits[value[1:]]
    if value == "二十": return 20
    return None


@dataclass(frozen=True)
class FileRequest:
    query: str
    desktop_only: bool = False


def file_request(text):
    phrase = clean_phrase(text)
    if re.match(r"^(?:不要|别|不用|禁止|不许|取消)", phrase): return None
    phrase = re.sub(r"^(?:(?:请你?|麻烦你?|帮我|帮忙|能不能|能否|你能|你可以|可以|能)\s*)+", "", phrase).strip()
    compact = re.sub(r"\s+", "", phrase)
    if compact in {"桌面文件", "看看桌面", "看一下桌面", "看看桌面文件", "桌面有什么文件", "桌面上有什么文件", "桌面有哪些文件", "列出桌面文件", "列一下桌面文件"}:
        return FileRequest("", True)
    if compact in {"文件列表", "有哪些文件", "看看有哪些文件", "共享目录有哪些文件", "列出共享文件"}:
        return FileRequest("")
    query = None
    for pattern in (
        r"^把\s*(.+?)(?:发送给我|发给我|传给我|发我|给我)(?:一下|吧|好吗|可以吗)?$",
        r"^(?:发送|发|传)(?:一下|一份|一个|个)?\s*(.+?)(?:给我|给我看看)(?:一下|吧|好吗|可以吗)?$",
        r"^(?:给我(?:发送|发|传)(?:一下|一份|一个)?|发送给我|发给我|发我|给我|我想下载|我想要|我要|下载|发送文件|发文件|发送)\s*(.+?)(?:可以吗|好吗|吧)?$",
        r"^(?:搜索文件|查找文件|找文件|搜文件|找一下|搜索|查找|搜一下|找找|搜|找)\s*(.+?)(?:在哪里|在哪)?$",
        r"^(.+?)(?:发送给我|发给我|传给我|发我)(?:一下|吧|好吗|可以吗)?$",
    ):
        match = re.fullmatch(pattern, phrase)
        if match:
            query = match[1].strip(); break
    if query is None: return None
    scope = re.match(r"^(?:我(?:的)?|电脑(?:的)?)?桌面(?:上)?(?:的)?\s*", query)
    desktop_only = bool(scope)
    if scope: query = query[scope.end():]
    else: query = re.sub(r"^(?:共享|授权)(?:目录|文件夹)(?:里|中)?的?\s*", "", query)
    query = re.sub(r"^(?:那个|这个|一份|一个|名叫|叫)\s*", "", query)
    query = re.sub(r"(?:这个|那个)(?:文件|文档)$", "", query).strip()
    if len(query) >= 2 and (query[0], query[-1]) in {('"','"'), ("'","'"), ('“','”'), ('‘','’'), ('《','》')}:
        query = query[1:-1].strip()
    if not query or query in {"文件", "文档", "一下"}: return FileRequest("", desktop_only)
    if len(query) > 200 or any(char in query for char in "\\/:"):
        raise ValueError("请说文件名或部分名称，只能查找已授权目录；不能指定任意路径。")
    return FileRequest(query, desktop_only)
