# -*- coding: utf-8 -*-
"""
樱桃后台控制动作层（LataleGuard 用）

流程封板：v1.2（2026-10-31 05:50）
  已走通四条路径（均实测）：
    1) 自动 -> 加载脚本 -> 换脚本表      pick <表名>
    2) 自动 -> 脚本设置 -> 世界募集组队   team <房间名> <密码> <队长|队员>
    3) 角色 -> 读取配置 -> 保存设置        role <清单名>
    4) 开始 / 停止挂机                    start / stop
  封板说明与全部坐标见 docs/05_樱桃后台控制_封板_v1.2_20261031.md
  其它模块（副本/挂机/保护/技能…）均未覆盖。

原理（2026-10-31 实测）：
  1. 找窗口：类名 iiiiiiiiiiiii / GGGGGGGGG + 尺寸 740x600 才是主设置界面
     另有 280x450 文件对话框、300x200 清空确认框，均是独立窗口
  2. 操作：PostMessage 投递鼠标/键盘消息，不抢前台、不动光标、不截图
  3. 判据：看界面状态（窗口是否消失、列表是否变化），不看 PostMessage 返回值

前置条件：**必须以管理员身份运行**，否则消息会被 UIPI 拒收（返回 0）。

命令行：
    python cherry_ctl.py status
    python cherry_ctl.py start            # F8 开始挂机
    python cherry_ctl.py stop             # F9 停止挂机
    python cherry_ctl.py readtable        # 点开「读取脚本表」
    python cherry_ctl.py pick 智海         # 打开脚本表并选中某个表，最后点确定
    python cherry_ctl.py shot [文件名]     # 抓主界面截图
"""
from __future__ import annotations

FLOW_VERSION = "1.2"          # 封板版本，改坐标/流程时同步更新 docs/05


import ctypes
import json
import logging
import os
import re
import shutil
import sys
import time
import threading
import functools

from .compat import hotkey_codes
from .compat import app_dir
from ctypes import wintypes

import numpy as np

# 本模块原来没有 logger，新增的兜底分支和失败截图要用
log = logging.getLogger("cherry-ctl")

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE, "templates")
CHERRY_ROOT = os.path.join(app_dir(os.path.dirname(BASE)), ".unconfigured-data-source-a")
SYSTEM_SET = os.path.join(CHERRY_ROOT, r"Data\Config\SystemSet.save")
AUTOLOG = os.path.join(CHERRY_ROOT, r"log\AutoLog")

CONFIG_ROOT = os.path.join(CHERRY_ROOT, r"Data\Config")

START_KEY = None
_ACTION_LOCK = threading.RLock()


class ActionError(RuntimeError):
    pass


def _serialized(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _ACTION_LOCK:
            try:
                return fn(*args, **kwargs)
            except ActionError as exc:
                return False, str(exc)
    return wrapped


def configure(root, start_key=None):
    global CHERRY_ROOT, SYSTEM_SET, AUTOLOG, CONFIG_ROOT, START_KEY
    CHERRY_ROOT = os.path.abspath(root) if root else os.path.join(app_dir(os.path.dirname(BASE)), ".unconfigured-data-source-a")
    SYSTEM_SET = os.path.join(CHERRY_ROOT, "Data", "Config", "SystemSet.save")
    AUTOLOG = os.path.join(CHERRY_ROOT, "log", "AutoLog")
    CONFIG_ROOT = os.path.join(CHERRY_ROOT, "Data", "Config")
    if start_key:
        hotkey_codes(start_key)
    START_KEY = start_key




MAIN_CLASSES = ("iiiiiiiiiiiii", "GGGGGGGGG", "kkkkkkkkkkkkk",
                "444444", "333333333333333")   # 后两个是当前版本实际类名
MAIN_SIZE = (740, 600)      # 主设置界面
SIZE_TOL = 12
GAME_WINDOW_CLASS = "LATALE_CLIENT"    # 游戏主窗口类名（用于反查角色名）
GAME_EXE_KEY = "lataleclient"          # 主界面归属进程的特征串


def role_account_dir(role):
    """按角色名找到其所属的账号目录。

    结构：Data\\Config\\<账号ID>\\<角色hex>\\ 下有一个 0 字节文件，文件名就是角色名。
    找不到返回 None。
    """
    if not role or not os.path.isdir(CONFIG_ROOT):
        return None
    try:
        for acc in os.listdir(CONFIG_ROOT):
            acc_dir = os.path.join(CONFIG_ROOT, acc)
            if not os.path.isdir(acc_dir):
                continue
            for sub in os.listdir(acc_dir):
                sub_dir = os.path.join(acc_dir, sub)
                if not os.path.isdir(sub_dir):
                    continue
                try:
                    for f in os.listdir(sub_dir):
                        fp = os.path.join(sub_dir, f)
                        if os.path.isfile(fp) and os.path.getsize(fp) == 0 and f == role:
                            return acc_dir
                except OSError:
                    continue
    except OSError:
        pass
    return None


def local_names(role=None, ext=".sc"):
    """直接读目录拿名称清单，不依赖 OCR。

    ext='.sc' → 脚本表名；ext='.lg' → 角色配置名。
    role 为空时返回所有账号的并集。
    """
    def _scan(d):
        try:
            return sorted({os.path.splitext(f)[0] for f in os.listdir(d)
                           if f.lower().endswith(ext)})
        except OSError:
            return []

    if role:
        acc = role_account_dir(role)
        return _scan(acc) if acc else []
    names = set()
    if os.path.isdir(CONFIG_ROOT):
        for acc in os.listdir(CONFIG_ROOT):
            acc_dir = os.path.join(CONFIG_ROOT, acc)
            if os.path.isdir(acc_dir):
                names.update(_scan(acc_dir))
    return sorted(names)


# ---------------------------------------------------------------- 运行状态（读日志，不碰界面）

def autolog_dir(role):
    """角色名 → AutoLog 子目录（目录名是角色名的 GBK 十六进制）"""
    try:
        return os.path.join(AUTOLOG, role.encode("gbk").hex().upper())
    except Exception:
        return None


def script_status(role=None, limit=3):
    """判断脚本跑到哪了、完成没。

    数据源：log\\AutoLog\\<角色GBK hex>\\<日期>\\<时-分-秒>.ini
    每个 ini 是一次脚本运行的记录：
        name / begintime / endtime / endmsg / msg[]
    endmsg 非空 = 本次已结束（内容可能是「执行完毕!」或结束原因）；
    空 = 还在跑。

    返回 [{role, script, begin, end, endmsg, state, age_sec}]，
    state ∈ running / done / stalled
    """
    import glob

    out = []
    if role:
        roles = [role]
    else:
        roles = []
        if os.path.isdir(AUTOLOG):
            for name in os.listdir(AUTOLOG):
                if not os.path.isdir(os.path.join(AUTOLOG, name)):
                    continue
                try:
                    roles.append(bytes.fromhex(name).decode("gbk"))
                except Exception:
                    continue

    for r in roles:
        d = autolog_dir(r)
        if not d or not os.path.isdir(d):
            continue
        files = glob.glob(os.path.join(d, "*", "*.ini"))
        if not files:
            continue
        files.sort(key=os.path.getmtime, reverse=True)
        for f in files[:limit]:
            obj = None
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    with open(f, "r", encoding=enc) as fp:
                        obj = json.load(fp)
                    break
                except Exception:
                    continue
            if not isinstance(obj, dict):
                continue
            endmsg = str(obj.get("endmsg") or "").strip()
            # 骯字符（乱码）不当成有效结束信息
            if endmsg and all(c in "\ufffd\x00" for c in endmsg):
                endmsg = ""
            age = time.time() - os.path.getmtime(f)
            if not endmsg:
                state = "running" if age < 180 else "stalled"
            elif ("执行完毕" in endmsg) or ("完成" in endmsg):
                state = "done"
            else:
                state = "ended"      # 结束了但不是正常完成，endmsg 就是原因
            out.append({
                "role": r,
                "script": str(obj.get("name") or "?"),
                "endmsg": endmsg,
                "state": state,
                "age_sec": int(age),
                "file": os.path.basename(f),
                "day": os.path.basename(os.path.dirname(f)),
            })
    return out


# ---------------------------------------------------------------- 脚本配置（文本层，不碰界面）
#
# 文件位置：Data\\Config\\<账号>\\<角色GBKhex>\\<脚本hash>.lua
# 脚本 hash = MD5(GBK(脚本名)).hex[8:24]
#   例：智海 -> 80a29fdeb82317d6
# 内容是一行一个「键=值」的配置，不是 Lua 代码。
# 此法已在 PairTool（用户自写工具）中验证生效，不需要重载樱桃。

def _role_dir(role):
    """按角色名找到它的配置目录（Data\\Config\\<账号>\\<角色hex>）"""
    if not role or not os.path.isdir(CONFIG_ROOT):
        return None
    for acc in os.listdir(CONFIG_ROOT):
        ad = os.path.join(CONFIG_ROOT, acc)
        if not os.path.isdir(ad):
            continue
        for ch in os.listdir(ad):
            cd = os.path.join(ad, ch)
            if not os.path.isdir(cd):
                continue
            try:
                for f in os.listdir(cd):
                    fp = os.path.join(cd, f)
                    if os.path.isfile(fp) and os.path.getsize(fp) == 0 and f == role:
                        return cd
            except OSError:
                continue
    return None


def script_hash(script):
    """脚本名 -> 配置文件名（不含 .lua）"""
    import hashlib
    return hashlib.md5(script.encode("gbk")).hexdigest()[8:24]


def lua_path(role, script):
    """角色 + 脚本名 → 配置文件路径（不存在返回 None）"""
    d = _role_dir(role)
    if not d:
        return None
    p = os.path.join(d, script_hash(script) + ".lua")
    return p if os.path.isfile(p) else None


def read_script_cfg(role, script):
    """读脚本配置。返回 (dict, 原文, 路径) 或 (None, None, None)"""
    p = lua_path(role, script)
    if not p:
        return None, None, None
    try:
        text = open(p, "r", encoding="gbk", errors="replace").read()
    except Exception:
        return None, None, p
    cfg = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg, text, p


def write_script_cfg(role, script, changes, backup=True):
    """改脚本配置（只改给定键，其余原样保留）。

    changes: {键: 新值}。先备份原文件，再用正则逐行替换。
    返回 (ok, msg, 变更列表)。
    """
    import re as _re
    cfg, text, p = read_script_cfg(role, script)
    if p is None:
        return False, "找不到配置：%s / %s" % (role, script), []
    if text is None:
        return False, "读不了配置文件", []

    if backup:
        try:
            import shutil
            shutil.copy2(p, p + ".bak")
        except Exception:
            pass

    done = []
    new_text = text
    for k, v in (changes or {}).items():
        old = cfg.get(k)
        pat = _re.compile(r"^%s=[^\r\n]*" % _re.escape(k), _re.M)
        if pat.search(new_text):
            new_text = pat.sub("%s=%s" % (k, v), new_text)
            done.append("%s：%s → %s" % (k, old if old is not None else "（无）", v))
        else:
            # 键不存在：追加到末尾
            if not new_text.endswith("\n"):
                new_text += "\r\n"
            new_text += "%s=%s\r\n" % (k, v)
            done.append("%s：新增 → %s" % (k, v))

    if new_text == text:
        return True, "没有变化", []
    try:
        open(p, "w", encoding="gbk", errors="replace", newline="").write(new_text)
    except Exception as e:
        return False, "写入失败：%s" % e, done
    return True, "已修改 %d 项（原文件已备份为 .bak）" % len(done), done


def script_name_by_hash(role, hashname):
    """hash -> 中文脚本名（在该角色的 .sc 清单里反查）。对不上返回 None。"""
    h = (hashname or "").lower().replace(".lua", "")
    if not h:
        return None
    for n in local_names(role, ".sc"):
        if script_hash(n) == h:
            return n
    return None


def list_script_cfgs(role):
    """列出该角色可编辑的脚本配置。

    只返回能对上中文名的（对不上的 hash 不展示、不发送）。
    返回 [{script, path, size, mtime}]
    """
    d = _role_dir(role)
    if not d:
        return []
    out = []
    for n in local_names(role, ".sc"):
        p = os.path.join(d, script_hash(n) + ".lua")
        if os.path.isfile(p):
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"script": n, "path": p, "size": st.st_size,
                        "mtime": st.st_mtime})
    return out


# ---------------------------------------------------------------- 别名（模糊搜索用）
#
# 用户习惯叫法不等于文件名：说「武器本」「边界」「边」都应该能找到「残破边界」。
# 格式：{"真名": ["别名1", "别名2"]}，可以手改，也可以让程序学。
ALIAS_PATH = os.path.join(BASE, "script_aliases.json")


def load_aliases():
    try:
        import json as _json
        with open(ALIAS_PATH, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_alias(real, alias):
    """记住一条别名（同一个真名下自动去重）"""
    import json as _json
    d = load_aliases()
    lst = d.setdefault(real, [])
    if alias and alias not in lst:
        lst.append(alias)
        try:
            with open(ALIAS_PATH, "w", encoding="utf-8") as f:
                _json.dump(d, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False
    return False


def map_catalog():
    """读分类目录（从「自动副本脚本加载设置」截图 OCR 后清洗得到）

    返回 {分类: [{name, level}]}，失败返回 {}。
    """
    import json as _json
    p = os.path.join(BASE, "map_catalog.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = _json.load(f)
        return d.get("categories") or {}
    except Exception:
        return {}


def match_name(name, candidates):
    """模糊匹配名称。返回 (命中或 None, 候选列表)。

    顺序：完全相等 > 别名 > 子串（双向）> 别名子串 > 宇符重叠。
    多个同分就返回候选，让调用方去问，不自己做主。
    """
    name = (name or "").strip()
    if not name:
        return None, list(candidates)
    cand_set = set(candidates)
    if name in cand_set:
        return name, [name]

    aliases = load_aliases()

    # 别名：用户说的词命中某真名的别名
    for real, alist in aliases.items():
        if real in cand_set and (name == real or name in (alist or [])):
            return real, [real]

    low = name.lower()
    near = [c for c in candidates if low in c.lower() or c.lower() in low]
    if len(near) == 1:
        return near[0], near
    if near:
        return None, near

    # 别名子串：例如说「打武器」而别名里有「武器本」
    for real, alist in aliases.items():
        if real not in cand_set:
            continue
        for a in (alist or []):
            if a and (a in name or name in a):
                return real, [real]

    scored = []
    for c in candidates:
        hit = sum(1 for ch in set(name) if ch in c)
        if hit:
            scored.append((hit, c))
    if scored:
        scored.sort(reverse=True)
        top = scored[0][0]
        best = [c for h, c in scored if h == top]
        if len(best) == 1:
            return best[0], best
        return None, best
    return None, []


WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------- 窗口

def _pid_of(hwnd):
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_of_pid(pid):
    """取进程完整路径（失败返回空串）"""
    k = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        k.CloseHandle(h)
    return ""


def _role_of_pid(pid):
    """从该进程的游戏主窗口标题里取角色名（标题形如「角色名 - 频道 - LaTale Client(64)」）"""
    found = [""]
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, lp):
        if _pid_of(h) != pid:
            return True
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if c.value == GAME_WINDOW_CLASS:
            t = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, t, 512)
            for part in t.value.split("-"):
                name = part.strip()
                if name and "latale" not in name.lower():
                    found[0] = name
                    break
            return False
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return found[0]


def list_targets():
    """列出所有樱桃主界面。

    判定：尺寸 740x600（容差）且 所属进程是游戏客户端。
    不依赖窗口类名，因为不同客户端版本的类名会变（实测有 GGGGGGGGG / kkkkkkkkkkkkk）。
    返回 [{hwnd, pid, role, exe, visible}]，按窗口位置从上到下排序。
    """
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    hits = []

    def cb(h, lp):
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        w, hh = r.right - r.left, r.bottom - r.top
        if abs(w - MAIN_SIZE[0]) <= SIZE_TOL and abs(hh - MAIN_SIZE[1]) <= SIZE_TOL:
            pid = _pid_of(h)
            exe = _exe_of_pid(pid)
            if GAME_EXE_KEY in exe.lower().replace("\\", "/"):
                hits.append({"hwnd": int(h), "pid": pid, "exe": exe,
                             "role": "", "visible": bool(u.IsWindowVisible(h)),
                             "top": r.top})
        return True

    u.EnumWindows(EnumProc(cb), 0)
    for t in hits:
        t["role"] = _role_of_pid(t["pid"])
    hits.sort(key=lambda x: (x["top"], x["hwnd"]))
    return hits


def find_main_window(target=None):
    """定位樱桃主设置界面。

    target 可以是：
        None      → 第一个（最上面的那个）
        int       → 游戏进程 PID
        str       → 角色名（包含匹配）
    返回 hwnd 或 None。
    """
    ts = list_targets()
    if not ts:
        return None
    if target is None:
        return ts[0]["hwnd"] if len(ts) == 1 else None
    if isinstance(target, int):
        for t in ts:
            if t["pid"] == target:
                return t["hwnd"]
        return None
    key = str(target).strip()
    exact = [t for t in ts if key and key == t["role"]]
    hits = exact or [t for t in ts if key and key in t["role"]]
    return hits[0]["hwnd"] if len(hits) == 1 else None


def window_rect(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


# ---------------------------------------------------------------- 抓图

def grab(hwnd):
    """PrintWindow 抓窗口内容，返回 PIL.Image（不依赖前台/可见）"""
    from PIL import Image

    left, top, right, bottom = window_rect(hwnd)
    w, hh = right - left, bottom - top
    if w <= 0 or hh <= 0:
        raise RuntimeError("窗口矩形异常: %sx%s" % (w, hh))

    hdc = u.GetWindowDC(hwnd)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, hh)
    gdi.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -hh
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * hh * 4)
    gdi.GetDIBits(mdc, bmp, 0, hh, buf, ctypes.byref(bi), 0)

    arr = np.frombuffer(buf, dtype=np.uint8).reshape((hh, w, 4))
    img = Image.fromarray(arr[:, :, :3][:, :, ::-1].copy())

    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    return img


# ---------------------------------------------------------------- 定位

def find_template(img, tpl_path, scales=(0.8, 0.9, 1.0, 1.1, 1.2), threshold=0.85):
    """模板匹配，返回窗口内中心坐标 (x, y) 或 None"""
    import cv2

    if not os.path.exists(tpl_path):
        return None
    shot = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    tpl = cv2.imdecode(np.fromfile(tpl_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if tpl is None:
        return None

    best = (0.0, None, None)
    for s in scales:
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if t.shape[0] > shot.shape[0] or t.shape[1] > shot.shape[1]:
            continue
        res = cv2.matchTemplate(shot, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best[0]:
            best = (mx, loc, t.shape)
    if best[0] < threshold:
        return None
    x, y = best[1]
    th, tw = best[2][0], best[2][1]
    return x + tw // 2, y + th // 2, best[0]


_OCR = None

def list_tables():
    """列出可用的脚本表名（.sc 文件，位于 Data\\Config\\<账号>\\ 下）

    2026-10-31 少爷指出：脚本表名称就是这些文件的文件名，不必去对话框里 OCR 读。
    返回 {name: [所属账号目录, ...]}
    """
    root = os.path.join(CHERRY_ROOT, "Data", "Config")
    out = {}
    if not os.path.isdir(root):
        return out
    for acc in os.listdir(root):
        acc_dir = os.path.join(root, acc)
        if not os.path.isdir(acc_dir) or not acc.isdigit():
            continue
        for fn in os.listdir(acc_dir):
            if fn.lower().endswith(".sc"):
                name = fn[:-3]
                out.setdefault(name, []).append(acc)
    return out


def has_table(name):
    return name in list_tables()


def ocr_items(img):
    """返回 [(text, cx, cy, score)]，坐标是图片（窗口）内坐标"""
    global _OCR
    from rapidocr_onnxruntime import RapidOCR

    if _OCR is None:
        _OCR = RapidOCR()
    tmp = os.path.join(BASE, "tmp_ocr.png")
    img.save(tmp)
    result, _ = _OCR(tmp)
    out = []
    for box, text, score in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((str(text), int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), float(score)))
    try:
        os.remove(tmp)
    except Exception:
        pass
    return out


def find_text(img, wanted, fuzzy=False):
    """在窗口画面里找一段文字，返回 (x, y, 命中的文本) 或 None"""
    items = ocr_items(img)
    if not items:
        return None
    if fuzzy:
        cands = [it for it in items if wanted in it[0]]
    else:
        cands = [it for it in items if it[0].strip() == wanted]
    if not cands:
        return None
    it = max(cands, key=lambda x: x[3])
    return it[1], it[2], it[0]


# ---------------------------------------------------------------- 操作

def _client_point(hwnd, wx, wy):
    """窗口内坐标 -> 客户区坐标"""
    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    left, top, _, _ = window_rect(hwnd)
    pt = POINT(left + int(wx), top + int(wy))
    u.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def click(hwnd, wx, wy, tag=""):
    """消息点击（窗口内坐标），不抢前台、不动光标"""
    cx, cy = _client_point(hwnd, wx, wy)
    lp = (cy << 16) | (cx & 0xFFFF)
    u.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.12)
    ok1 = u.PostMessageW(hwnd, WM_LBUTTONDOWN, 0x0001, lp)
    time.sleep(0.10)
    ok2 = u.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    if not (ok1 and ok2):
        raise ActionError("点击投递失败，请检查权限和目标窗口")
    return True


def _require_click(hwnd, x, y, tag=""):
    if not click(hwnd, x, y, tag):
        raise ActionError("点击投递失败，已停止后续操作")
    return True


def press(hwnd, vk):
    """消息按键，不抢前台"""
    sc = u.MapVirtualKeyW(vk, 0)
    down = 1 | (sc << 16)
    up = 1 | (sc << 16) | 0xC0000000
    ok1 = u.PostMessageW(hwnd, WM_KEYDOWN, vk, down)
    time.sleep(0.05)
    ok2 = u.PostMessageW(hwnd, WM_KEYUP, vk, up)
    if not (ok1 and ok2):
        raise ActionError("按键投递失败，请检查权限和目标窗口")
    return True


def press_combo(hwnd, text):
    codes = hotkey_codes(text)
    held = []
    try:
        for code in codes[:-1]:
            held.append(code)
            if not u.PostMessageW(hwnd, WM_KEYDOWN, code, 1):
                raise ActionError("修饰键投递失败")
        return press(hwnd, codes[-1])
    finally:
        for code in reversed(held):
            u.PostMessageW(hwnd, WM_KEYUP, code, 0xC0000001)


def _wait_closed(hwnd, timeout=4.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not u.IsWindowVisible(hwnd):
            return True
        time.sleep(0.15)
    return False


# ---------------------------------------------------------------- 配置

def read_hotkeys():
    """从 SystemSet.save 读热键（GBK JSON），失败则用默认 F8/F9"""
    out = {"start": "F8", "end": "F9", "hide_ui": "F12"}
    try:
        raw = None
        for enc in ("gbk", "utf-8-sig", "utf-8"):
            try:
                with open(SYSTEM_SET, "r", encoding=enc) as f:
                    raw = f.read()
                break
            except UnicodeDecodeError:
                continue
        if not raw:
            return out
        data = json.loads(raw)
        if data.get("hotkey_start"):
            out["start"] = str(data["hotkey_start"]).strip()
        if data.get("hotkey_end"):
            out["end"] = str(data["hotkey_end"]).strip()
        if data.get("hotkey_hide_ui"):
            out["hide_ui"] = str(data["hotkey_hide_ui"]).strip()
    except Exception:
        pass
    return out


_KEY_MAP = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "INS": 0x2D, "INSERT": 0x2D, "DEL": 0x2E, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDN": 0x22, "PAUSE": 0x13, "SCROLL": 0x91,
}


def vk_of(name):
    name = (name or "").strip().upper()
    if name in _KEY_MAP:
        return _KEY_MAP[name]
    if re.fullmatch(r"[A-Z]", name):
        return ord(name)
    if re.fullmatch(r"[0-9]", name):
        return ord(name)
    return None


# ---------------------------------------------------------------- 状态

def autolog_latest():
    """最近一场副本结算文件的 mtime（epoch），没有则 0"""
    latest = 0.0
    if not os.path.isdir(AUTOLOG):
        return 0.0
    for root, _dirs, files in os.walk(AUTOLOG):
        for fn in files:
            if fn.endswith(".ini"):
                try:
                    m = os.path.getmtime(os.path.join(root, fn))
                    if m > latest:
                        latest = m
                except OSError:
                    pass
    return latest


def status(verbose=True):
    hwnd = find_main_window()
    info = {
        "admin": is_admin(),
        "window": hwnd,
        "hotkeys": read_hotkeys(),
        "last_run": autolog_latest(),
        "last_run_age_sec": None,
    }
    if info["last_run"]:
        info["last_run_age_sec"] = int(time.time() - info["last_run"])
    if verbose:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return info


# ---------------------------------------------------------------- 动作

@_serialized
def do_start(hwnd=None):
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面（740x600）"
    key = START_KEY or read_hotkeys()["start"]
    ok = press_combo(hwnd, key)
    return ok, "已投递开始键（%s），请通过运行进度确认是否开始" % key


@_serialized
def do_stop(hwnd=None):
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面（740x600）"
    ok = press_combo(hwnd, read_hotkeys()["end"])
    return ok, "已投递停止键（%s）" % read_hotkeys()["end"]


TAB_AUTO = (21, 336)          # 左侧「自动」标签（必须先点它，右侧才会有加载脚本）
TAB_LOAD_SCRIPT = (111, 43)   # 顶部「加载脚本」标签（740x600 窗口内坐标）
BTN_READ_TABLE = (687, 459)   # 「读取脚本表」按钮（固定坐标，不用识图）

# 左侧栏标签（740x600 窗口内坐标，实测）
LEFT_TABS = {
    "便捷": (21, 80), "搭配": (21, 112), "指令": (20, 144), "触发": (20, 176),
    "LUA": (20, 209), "保护": (21, 240), "技能": (21, 272), "挂机": (21, 304),
    "自动": (21, 336), "角色": (20, 369), "物品": (20, 400), "日志": (21, 432),
    "其他": (21, 464),
}

# 顶部页签（实测，仅「自动」模块下的五个）
TOP_TABS = {
    "加载脚本": (111, 43), "补给脚本": (250, 43), "超能任务": (389, 43),
    "脚本设置": (527, 43), "脚本日志": (668, 43),
}


@_serialized
def do_tab(name, hwnd=None):
    """后台切到顶部某个页签（仅需主界面）"""
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面"
    pt = TOP_TABS.get(name)
    if not pt:
        return False, "未标定这个页签：%s（可用：%s）" % (name, "、".join(TOP_TABS))
    ok = click(hwnd, pt[0], pt[1])
    time.sleep(0.4)
    return ok, "已切到「%s」页签" % name


@_serialized
def do_left(name, hwnd=None):
    """后台切到左侧某个模块标签"""
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面"
    pt = LEFT_TABS.get(name)
    if not pt:
        return False, "未标定这个标签：%s（可用：%s）" % (name, "、".join(LEFT_TABS))
    ok = click(hwnd, pt[0], pt[1])
    time.sleep(0.4)
    return ok, "已切到左侧「%s」" % name


@_serialized
def do_read_table(hwnd=None):
    """
    点开「读取脚本表」对话框。**纯坐标 + 纯消息，不截图、不 OCR。**

    前提：左侧先选「自动」，顶部再切「加载脚本」，右侧才会出现“读取脚本表”按钮。
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面"

    _require_click(hwnd, TAB_AUTO[0], TAB_AUTO[1])
    time.sleep(0.30)
    _require_click(hwnd, TAB_LOAD_SCRIPT[0], TAB_LOAD_SCRIPT[1])
    time.sleep(0.40)
    _require_click(hwnd, BTN_READ_TABLE[0], BTN_READ_TABLE[1])
    time.sleep(1.2)

    if not find_window_by_size(*DIALOG_SIZE, owner=hwnd):
        return False, "点了读取脚本表，但没看到对话框弹出来"
    return True, "已点开读取脚本表（纯坐标）"


DIALOG_SIZE = (280, 450)      # 文件选择对话框
CONFIRM_SIZE = (300, 200)     # “是否清空原有脚本?”确认框

# 2026-10-31 实测标定的对话框内坐标（列表项 + 底部按钮）
# 只作为快路径：新增/删除脚本表后顺序会变，找不到就回退到 OCR
DIALOG_ITEMS = {
    "宝石": (43, 80), "师徒": (42, 101), "新徽章": (48, 124), "普雷": (42, 145),
    "智海": (42, 167), "沙泪": (43, 189), "活动经验本": (60, 211), "混伊卡": (48, 233),
    "经验本": (48, 255), "翡翠4": (45, 278), "蘑菇门": (49, 300), "觉醒石箱子": (60, 321),
    "边": (36, 343), "铁赫": (43, 365),
}
DIALOG_OK = (72, 425)
CONFIRM_OK = (151, 170)   # 2026-10-31 22:xx OCR 标定修正（原 135,157 偏了，点不中）

# 列表项按索引算坐标：实测第 0 项 y=80，行距约 21.9，可视区约 14 行。
# 不再依赖硬编码「表名→坐标」，因为不同账号的表不同、顺序也不同（踩过：
# 旧坐标表里「活动经验本」在 y=211，而新账号 y=211 是「普雷」）。
DIALOG_ITEM_Y0 = 80.0
DIALOG_ITEM_DY = 21.88
DIALOG_ITEM_X = 55
DIALOG_VISIBLE_ROWS = 15      # 实测：未滚动时一次能显示 15 项
SCROLL_STEP_ROWS = 1          # 实测：滚一格 = 1 行（不是 Windows 默认的 3 行）
WM_MOUSEWHEEL = 0x020A


def _dialog_item_point(index):
    """按列表索引返回对话框内坐标（第 0 项在最上方）"""
    return (DIALOG_ITEM_X, int(round(DIALOG_ITEM_Y0 + index * DIALOG_ITEM_DY)))


def scroll_list(dlg, times, step_rows=None):
    """向下滚动 times 格。

    实测结论（2026-10-31）
    ----------------------
    - 这个列表 **滚一格 = 1 行**（不是 Windows 默认的 3 行）
    - **PostMessage 发 WM_MOUSEWHEEL 完全无效**（画面差异 0.00%）
    - 必须先激活窗口，再用真实滚轮（mouse_event）才生效
    - PageDown 无效；方向键只会移动选中项，不滚动列表

    所以这里用：SetForegroundWindow → 光标移到列表 → mouse_event 滚轮。
    结束后恢复光标与原前台窗口。
    """
    step_rows = SCROLL_STEP_ROWS if step_rows is None else step_rows
    if not times:
        return 0
    k = ctypes.windll.user32
    MOUSEEVENTF_WHEEL = 0x0800
    delta = -120 if times > 0 else 120        # 负值向下，正值向上
    n = abs(times)
    fg = 0
    cur = wintypes.POINT()
    try:
        fg = k.GetForegroundWindow()
        k.GetCursorPos(ctypes.byref(cur))
        x1, y1, x2, y2 = window_rect(dlg)
        sx, sy = x1 + 140, y1 + 240
        k.SetForegroundWindow(wintypes.HWND(dlg))
        time.sleep(0.25)
        k.SetCursorPos(sx, sy)
        time.sleep(0.10)
        for _ in range(n):
            k.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            time.sleep(0.12)
        time.sleep(0.15)
    except Exception:
        pass
    finally:
        try:
            k.SetCursorPos(cur.x, cur.y)
            if fg:
                k.SetForegroundWindow(wintypes.HWND(fg))
        except Exception:
            pass
    return n * step_rows


def find_window_by_size(width, height, tol=20, visible_only=True, owner=None):
    """按尺寸找一个窗口（对话框/确认框都靠尺寸认）

    注：内部回调的参数不能叫 h，否则会把“高度”遮蔽成窗口句柄（踩过）。
    类名白名单只作参考：樱桃版本不同类名会变（iiiiii / GGGGGG / 444444 都见过），
    认得太死就会出现“框明明开着却找不到”。这里改成优先白名单，没有就按尺寸兜底。
    """
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    hits = []

    target_pid = _pid_of(owner) if owner else None

    def cb(hwnd, lp):
        if target_pid is not None and _pid_of(hwnd) != target_pid:
            return True
        if visible_only and not u.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        if abs((r.right - r.left) - width) > tol:
            return True
        if abs((r.bottom - r.top) - height) > tol:
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        # 白名单命中排前面，其余尺寸相符的排后面
        hits.append((0 if cls.value in MAIN_CLASSES else 1, hwnd))
        return True

    u.EnumWindows(EnumProc(cb), 0)
    if not hits:
        return None
    if len(hits) != 1:
        log.warning("找到多个同尺寸窗口，拒绝猜测目标")
        return None
    return hits[0][1]


@_serialized
def do_pick_table(name, hwnd=None, clear_existing=True):
    """
    完整流程（实测）：
      1) 主界面点「读取脚本表」
      2) 等 280x450 对话框出现，找到它（对话框是独立窗口，消息必须发给它）
      3) 选中目标表：优先用标定坐标，找不到再 OCR
      4) 点对话框的「确定」
      5) 若弹出 300x200 的「是否清空原有脚本?」，点它的「确定」
    """
    main = hwnd or find_main_window()
    if not main:
        return False, "找不到樱桃主界面"

    ok, msg = do_read_table(main)
    if not ok:
        return False, msg

    dlg = find_window_by_size(*DIALOG_SIZE, owner=main)
    if not dlg:
        return False, "读取脚本表后没出现 280x450 对话框"

    # 3) 选中目标：按文件系统顺序算坐标（与界面列表顺序一致，已实测）
    role = _role_of_pid(_pid_of(main))
    names = local_names(role) if role else []
    idx = names.index(name) if name in names else -1

    if 0 <= idx < DIALOG_VISIBLE_ROWS:
        pt = _dialog_item_point(idx)
    elif idx >= DIALOG_VISIBLE_ROWS:
        # 固定规则：滚一格 = 1 行，滚够次数后按实际行数算目标现在的位置
        need = idx - DIALOG_VISIBLE_ROWS + 1
        times = need
        moved = scroll_list(dlg, times)
        time.sleep(0.35)
        pos = idx - moved
        if 0 <= pos < DIALOG_VISIBLE_ROWS:
            pt = _dialog_item_point(pos)
        else:
            return False, ("「%s」排在第 %d 项，需滚 %d 行；"
                           "已滚 %d 行后位置仍算不准，为避免点错已停止。"
                           "请在樱桃里手动上移该表再试"
                           % (name, idx + 1, need, moved))
    else:
        return False, "当前账号没有「%s」这张表（现有：%s）" % (
            name, "、".join(names[:20]) or "没读到")

    if not click(dlg, pt[0], pt[1]):
        return False, "选择脚本表失败"
    picked = name
    time.sleep(0.55)

    # 4) 点确定
    if not click(dlg, DIALOG_OK[0], DIALOG_OK[1]):
        return False, "确认脚本表失败"
    if not _wait_closed(dlg):
        return False, "读取窗口没有关闭，未确认加载成功"
    time.sleep(0.5)

    # 5) 清空确认框
    if clear_existing:
        cf = find_window_by_size(*CONFIRM_SIZE, owner=main)
        if cf:
            if not click(cf, CONFIRM_OK[0], CONFIRM_OK[1]) or not _wait_closed(cf):
                return False, "清空原有脚本未确认成功"
            return True, "已提交脚本表读取「%s」，并确认清空原有脚本" % picked
    return True, "已提交脚本表读取「%s」" % picked


# ---------------------------------------------------------------- 组队设置（v1.1）
#
# 预设存到用户数据目录：%APPDATA%\LataleGuard\cherry_profiles.json
# 理由：预设里有房间名/密码，属于私人数据，不应跟着代码目录一起提交或分发。
_PROFILE_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"),
    "LataleGuard")
PROFILE_PATH = os.path.join(_PROFILE_DIR, "cherry_profiles.json")
_LEGACY_PROFILE = os.path.join(BASE, "cherry_profiles.json")   # 旧位置，仅用于自动搬迁
TEAM_CB_CAPTAIN = (117, 370)        # 「我是队长（由我创建房间）」勾选框
TEAM_IN_ROOM = (262, 394)           # 房间名输入框
TEAM_IN_PASS = (216, 416)           # 密码输入框
TEAM_BTN_SAVE = (527, 541)          # 「保存设置」
TEAM_CB_BOX = (102, 362, 124, 384)  # 勾选框像素区域（自绘控件读不到状态）


def _find_edit_wnd(hwnd):
    """找当前的 EditWnd 子控件（自绘输入框）"""
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    rows = []

    def cb(h, lp):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(h, cls, 64)
        if cls.value == "EditWnd":
            rows.append(h)
        return True

    u.EnumChildWindows(hwnd, EnumProc(cb), 0)
    return rows[0] if rows else None


def checkbox_checked(img):
    """勾选框里有没有蓝色（自绘控件读不到状态，只能看像素）"""
    box = img.crop(TEAM_CB_BOX).convert("RGB")
    arr = np.array(box).astype(int)
    blue = (arr[:, :, 2] > 120) & ((arr[:, :, 2] - arr[:, :, 0]) > 40)
    return float(blue.mean()) > 0.05


def _post_checked(hwnd, message, key, flags):
    if not u.PostMessageW(hwnd, message, key, flags):
        raise ActionError("输入消息被目标拒绝，已停止")


def _fill_edit(hwnd, pt, text, clears=30):
    """点输入框 -> End -> 退格清空 -> WM_CHAR 逐字写入"""
    _require_click(hwnd, pt[0], pt[1])
    time.sleep(0.4)
    e = _find_edit_wnd(hwnd)
    if not e:
        return False, "没找到输入框（EditWnd）"
    _post_checked(e, 0x0100, 0x23, 1 | (0x4F << 16))
    _post_checked(e, 0x0101, 0x23, 1 | (0x4F << 16) | 0xC0000000)
    time.sleep(0.1)
    clears = max(clears, min(4096, int(u.GetWindowTextLengthW(e)) + 1))
    for _ in range(clears):
        _post_checked(e, 0x0100, 0x08, 1 | (0x0E << 16))
        _post_checked(e, 0x0102, 0x08, 1)
        _post_checked(e, 0x0101, 0x08, 1 | (0x0E << 16) | 0xC0000000)
        time.sleep(0.012)
    time.sleep(0.25)
    for ch in str(text):
        _post_checked(e, 0x0102, ord(ch), 1)
        time.sleep(0.06)
    return True, "已写入「%s」" % text


@_serialized
def do_team(room, password=None, captain=None, save=True, hwnd=None):
    """
    设置世界募集组队。
      room     房间名
      password 密码（None=不动，空字符串=清空）
      captain  True=确保勾选 / False=确保不勾（原本勾着则取消）/ None=不动
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面"

    ok, msg = do_left("自动", hwnd)
    if not ok:
        return False, msg
    time.sleep(0.3)
    ok, msg = do_tab("脚本设置", hwnd)
    if not ok:
        return False, msg
    time.sleep(0.55)

    ok, msg = _fill_edit(hwnd, TEAM_IN_ROOM, room)
    if not ok:
        return False, "房间名：" + msg
    time.sleep(0.4)

    if password is not None:
        ok, msg = _fill_edit(hwnd, TEAM_IN_PASS, password)
        if not ok:
            return False, "密码：" + msg
        time.sleep(0.4)

    note = ""
    if captain is not None:
        image = grab(hwnd)
        if image is None:
            return False, "无法读取队长勾选状态，已停止以免误改"
        cur = checkbox_checked(image)
        if bool(cur) != bool(captain):
            _require_click(hwnd, TEAM_CB_CAPTAIN[0], TEAM_CB_CAPTAIN[1])
            time.sleep(0.4)
            after = grab(hwnd)
            if after is None or bool(checkbox_checked(after)) != bool(captain):
                return False, "队长勾选状态未达到要求，请检查目标窗口"
            note = "，队长：%s -> %s" % ("勾" if cur else "不勾", "勾" if captain else "不勾")
        else:
            note = "，队长已是「%s」未动" % ("勾" if captain else "不勾")

    if save:
        _require_click(hwnd, TEAM_BTN_SAVE[0], TEAM_BTN_SAVE[1])
        time.sleep(1.0)
        saved_note = "，已点「保存设置」"
    else:
        saved_note = "，未保存"

    return True, "招募已设：房间名「%s」密码「%s」%s%s" % (
        room, "未改" if password is None else ("已清空" if password == "" else "已设置"), note, saved_note)


# ---- 角色 → 读取配置（v1.2）----
ROLE_BTN_READ_CFG = (484, 70)      # 「读取配置」
ROLE_BTN_SAVE_CFG = (404, 70)      # 「保存配置」
ROLE_BTN_SAVE_ALL = (526, 541)     # 「[保存设置]」
ROLE_BTN_CLEAR = (323, 70)         # 「清空列表」（清的是运行记录，红勾）
ROLE_BTN_ADD = (243, 70)           # 「添加角色」
ROLE_ITEM_Y0 = 80.0                # 读取配置对话框：首行 y
ROLE_ITEM_DY = 22.0                # 行高（实测 21~23）


@_serialized
def do_clear_records(hwnd=None, ensure_page=True):
    """清空运行记录（角色页的「清空列表」）。

    红勾 = 该角色已经跑过脚本，后续切换会跳过它。
    重新读取配置后必须清一次，否则新脚本不会被执行。
    点完会弹「是否清空记录?」，需要再确认。

    ensure_page：do_role_flow 调进来时已经在角色页了，
    再切一次反而会点到刚关闭的对话框上，所以传 False。
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到主界面"
    if ensure_page:
        ok, msg = do_left("角色", hwnd)
    if not ok:
        return False, msg
        time.sleep(0.35)
    _require_click(hwnd, ROLE_BTN_CLEAR[0], ROLE_BTN_CLEAR[1])
    time.sleep(0.8)

    cf = find_window_by_size(*CONFIRM_SIZE, owner=hwnd)
    if cf:
        _require_click(cf, CONFIRM_OK[0], CONFIRM_OK[1])
        time.sleep(0.6)
        if find_window_by_size(*CONFIRM_SIZE, owner=hwnd):
            return False, "确认框没点中，请手动看一眼"
        return True, "已清空运行记录（红勾重置）"
    return False, "未出现清空确认框，无法确认结果，已停止后续步骤"


@_serialized
def do_save_role_cfg(hwnd=None):
    """角色页保存：点底栏的「[保存设置]」。

    注：顶部还有个「保存配置」（存的是配置清单本身），
    但少爷明确「最后一步和招募一样，点下方的保存设置」。
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到主界面"
    ok, msg = do_left("角色", hwnd)
    if not ok:
        return False, msg
    time.sleep(0.3)
    _require_click(hwnd, ROLE_BTN_SAVE_ALL[0], ROLE_BTN_SAVE_ALL[1])
    time.sleep(0.6)
    return True, "已提交保存设置，请在目标程序中核对"


@_serialized
def do_role_flow(config_name, hwnd=None):
    """角色切换页标准流程：读配置 → 清空运行记录 → 保存配置。

    顺序依据用户实际操作（2026-10-31 确认）：重新读取配置后，
    旧的运行记录（红勾）必须清掉，否则樱桃会跳过这些角色，
    新脚本不会执行。
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到主界面"

    ok, msg = do_left("角色", hwnd)
    if not ok:
        return False, msg
    time.sleep(0.35)

    ok, msg = do_role_config(config_name, hwnd=hwnd, save=False)
    if not ok:
        return False, "读配置失败：" + msg
    time.sleep(0.4)

    ok2, msg2 = do_clear_records(hwnd=hwnd, ensure_page=False)
    if not ok2:
        return False, "读配置成功，但清空记录失败：" + msg2
    time.sleep(0.4)

    ok3, msg3 = do_save_role_cfg(hwnd=hwnd)
    if not ok3:
        return False, "清空成功，但保存失败：" + msg3

    return True, "已读取「%s」，并清空运行记录、保存配置" % config_name


# 「读取配置」对话框里的清单（280x450，与加载脚本页的列表不同）
ROLE_DIALOG_ITEMS = {
    "宝石": (43, 80), "新徽章": (48, 101), "日常": (43, 124), "智海": (43, 146),
    "沙泪": (42, 168), "混伊卡": (48, 189), "箱子": (42, 211), "翡翠": (45, 233),
    "蘑菇": (43, 256), "边": (36, 277), "铁赫剑": (49, 300),
}


def _dump_fail_shot(hwnd, tag):
    """失败现场：截一张主界面存到 logs，下次出错不用猜停在哪个页。"""
    try:
        from PIL import ImageGrab
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
        d = os.path.join(app_dir(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%s_%s.png" % (tag, time.strftime("%m%d_%H%M%S")))
        img.save(p)
        log.info("失败现场已存：%s", p)
        return p
    except Exception as e:
        log.warning("截图失败：%s", e)
        return None


@_serialized
def do_role_config(name, hwnd=None, save=True):
    """
    角色页：读取配置 -> 选清单 -> 确定 -> 点底栏「[保存设置]」。

    注：少爷明确“最后一步和招募一样，点下方的保存设置”，所以不走上方的「保存配置」。
    """
    hwnd = hwnd or find_main_window()
    if not hwnd:
        return False, "找不到樱桃主界面"

    ok, msg = do_left("角色", hwnd)
    if not ok:
        return False, msg
    time.sleep(0.6)

    _require_click(hwnd, ROLE_BTN_READ_CFG[0], ROLE_BTN_READ_CFG[1])
    # 轮询等对话框：封板时固定等 1.2 秒，机器空闲时够用；
    # 现在游戏在挂机，框弹得慢，固定等待就会误判成“没出现”。
    dlg = None
    for _ in range(15):
        time.sleep(0.3)
        dlg = find_window_by_size(*DIALOG_SIZE, owner=hwnd)
        if dlg:
            break
    if not dlg:
        _dump_fail_shot(hwnd, "role_read_cfg")
        return False, "点了读取配置，等了 4.5 秒也没出现 280x450 对话框（已截图到 logs）"

    role = _role_of_pid(_pid_of(hwnd))
    names = local_names(role, ".lg")
    if name not in names:
        return False, "当前账号没有角色配置「%s」" % name
    index = names.index(name)
    if index >= DIALOG_VISIBLE_ROWS:
        moved = scroll_list(dlg, index - DIALOG_VISIBLE_ROWS + 1)
        index -= moved
    if not 0 <= index < DIALOG_VISIBLE_ROWS:
        return False, "配置不在可见列表内，已停止以免点错"
    pt = (DIALOG_ITEM_X, int(round(ROLE_ITEM_Y0 + index * ROLE_ITEM_DY)))
    _require_click(dlg, pt[0], pt[1])
    time.sleep(0.5)
    if not click(dlg, DIALOG_OK[0], DIALOG_OK[1]) or not _wait_closed(dlg):
        return False, "角色配置读取窗口未确认关闭"

    # 注意：读取配置点「确定」后不会弹确认框。
    # 「是否清空记录?」是清空列表那一步的框，单独在 do_clear_records 里处理。
    # 两边共用一套尺寸/坐标去猜，就会点错，所以这里不接确认框。

    if save:
        _require_click(hwnd, ROLE_BTN_SAVE_ALL[0], ROLE_BTN_SAVE_ALL[1])
        time.sleep(1.0)
        return True, "已读取配置「%s」，并点「[保存设置]」" % name
    return True, "已读取配置「%s」（未保存）" % name


def load_profiles():
    # 首次运行：把旧位置（项目目录）的预设自动搬到 AppData
    try:
        if not os.path.exists(PROFILE_PATH) and os.path.exists(_LEGACY_PROFILE):
            os.makedirs(_PROFILE_DIR, exist_ok=True)
            shutil.copy2(_LEGACY_PROFILE, PROFILE_PATH)
    except Exception:
        pass
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_profile(name, room, password, captain):
    data = load_profiles()
    data[name] = {"room": room, "password": password, "captain": bool(captain)}
    try:
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise RuntimeError("写预设失败：%s" % e)
    return data


def main(argv):
    # 控制台默认 GBK，统一改成 UTF-8，避免中文/符号打印直接抛异常
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 解析全局 --target（角色名 或 进程 PID），不在则操作第一个
    _target = None
    _rest = [argv[0]]
    i = 1
    while i < len(argv):
        if argv[i] in ("--target", "-t") and i + 1 < len(argv):
            _target = argv[i + 1]
            i += 2
            continue
        _rest.append(argv[i])
        i += 1
    argv = _rest

    if len(argv) < 2:
        print(__doc__)
        return 0
    cmd = argv[1].lower()

    if cmd == "targets":
        ts = list_targets()
        if not ts:
            print("没找到樱桃主界面（尺寸 740x600 且属于游戏进程）")
            return 1
        for n, t in enumerate(ts, 1):
            print("%d. hwnd=%-10d pid=%-6d 角色=%-10s 可见=%s" % (
                n, t["hwnd"], t["pid"], t["role"] or "(未识别)", t["visible"]))
        print("共 %d 个" % len(ts))
        return 0

    if cmd == "status":
        status()
        return 0

    # 解析目标窗口
    TARGET = None
    if _target is not None:
        key = int(_target) if str(_target).isdigit() else _target
        TARGET = find_main_window(key)
        if not TARGET:
            print("[FAIL] 没找到目标：%s（先跑 targets 看看有哪些）" % _target)
            return 1
    else:
        ts = list_targets()
        if len(ts) > 1:
            print("[注意] 检测到 %d 个主界面，未指定 --target，默认操作：%s（pid=%d）" % (
                len(ts), ts[0]["role"] or "?", ts[0]["pid"]))
        TARGET = ts[0]["hwnd"] if ts else None


    if cmd in ("tables", "list"):
        tabs = list_tables()
        if not tabs:
            print("没读到脚本表")
            return 1
        for name, accs in sorted(tabs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print("%-14s %s" % (name, ",".join(accs)))
        print("共 %d 张" % len(tabs))
        return 0

    if not is_admin():
        print("[WARN] 非管理员：消息会被 UIPI 拒收，请用管理员身份运行")
        # 继续尝试，便于观察具体失败点

    if cmd == "role":
        if len(argv) < 3:
            print("用法: python cherry_ctl.py role <清单名>   可用：%s" % "、".join(ROLE_DIALOG_ITEMS))
            return 2
        ok, msg = do_role_config(argv[2], hwnd=TARGET)
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1

    if cmd == "team":
        # team <房间名> <密码> [队长|队员]        直接设
        # team save <预设名> <房间名> <密码> [队长|队员]
        # team use <预设名>
        # team list
        args = argv[2:]
        if not args or args[0] == "list":
            prof = load_profiles()
            if not prof:
                print("还没有预设。用法：team save <名字> <房间名> <密码> <队长|队员>")
                return 0
            for k, v in prof.items():
                print("  %-10s 房间名=%-10s 密码=%-8s 队长=%s" % (
                    k, v.get("room", ""), v.get("password", ""),
                    "是" if v.get("captain") else "否"))
            return 0

        if args[0] == "save":
            if len(args) < 5:
                print("用法: team save <预设名> <房间名> <密码> <队长|队员>")
                return 2
            who = args[4]
            if who not in ("队长", "队员"):
                print("[FAIL] 最后一个参数必须是 队长 或 队员")
                return 2
            save_profile(args[1], args[2], args[3], who == "队长")
            print("[OK] 已保存预设「%s」" % args[1])
            return 0

        if args[0] == "use":
            if len(args) < 2:
                print("用法: team use <预设名>")
                return 2
            prof = load_profiles().get(args[1])
            if not prof:
                print("[FAIL] 没有这个预设：%s" % args[1])
                return 1
            ok, msg = do_team(prof.get("room", ""), prof.get("password", ""),
                              bool(prof.get("captain")), hwnd=TARGET)
            print(("[OK] " if ok else "[FAIL] ") + msg)
            return 0 if ok else 1

        if len(args) < 3:
            print("用法: team <房间名> <密码> <队长|队员>")
            return 2
        who = args[2]
        if who not in ("队长", "队员"):
            print("[FAIL] 第三个参数必须是 队长 或 队员（我不能替你定）")
            return 2
        ok, msg = do_team(args[0], args[1], who == "队长", hwnd=TARGET)
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1

    if cmd in ("tab", "left"):
        if len(argv) < 3:
            print("用法: python cherry_ctl.py tab <页签名> / left <左侧名>")
            return 2
        if cmd == "tab":
            ok, msg = do_tab(argv[2], hwnd=TARGET)
        else:
            ok, msg = do_left(argv[2], hwnd=TARGET)
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1

    if cmd == "start":
        ok, msg = do_start(hwnd=TARGET)
    elif cmd == "stop":
        ok, msg = do_stop(hwnd=TARGET)
    elif cmd in ("readtable", "read_table"):
        ok, msg = do_read_table(hwnd=TARGET)
    elif cmd == "pick":
        if len(argv) < 3:
            print("用法: python cherry_ctl.py pick <脚本表名>")
            return 2
        name = argv[2]
        known = list_tables()
        if known and name not in known:
            near = [k for k in known if name in k or k in name]
            print("[FAIL] 没有这张脚本表：%s" % name)
            print("       相近：%s" % ("、".join(near[:6]) if near else "、".join(sorted(known)[:10])))
            return 1
        ok, msg = do_pick_table(name, hwnd=TARGET)
    elif cmd == "shot":
        hwnd = find_main_window()
        if not hwnd:
            print("找不到樱桃主界面")
            return 1
        name = argv[2] if len(argv) > 2 else "shot_%s.png" % time.strftime("%H%M%S")
        path = name if os.path.isabs(name) else os.path.join(BASE, "logs", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        grab(hwnd).save(path)
        print("已保存:", path)
        return 0
    else:
        print(__doc__)
        return 2

    print(("[OK] " if ok else "[FAIL] ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
