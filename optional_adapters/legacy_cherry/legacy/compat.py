import os
import sys
import re

def hotkey_codes(value):
    names = str(value).upper().replace(" ", "").split("+")
    modifiers = {"CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10}
    if not names or len(set(names)) != len(names):
        raise ValueError("按键格式无效")
    codes = []
    for name in names[:-1]:
        if name not in modifiers:
            raise ValueError("修饰键仅支持 Ctrl / Alt / Shift")
        codes.append(modifiers[name])
    key = names[-1]
    if re.fullmatch(r"F([1-9]|1[0-2])", key):
        codes.append(0x70 + int(key[1:]) - 1)
    elif re.fullmatch(r"[A-Z0-9]", key):
        codes.append(ord(key))
    else:
        raise ValueError("按键支持 F1~F12、字母/数字，以及 Ctrl/Alt/Shift 组合")
    return codes

def app_dir(source_dir=None):
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(source_dir or os.path.dirname(__file__))

