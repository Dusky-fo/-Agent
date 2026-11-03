"""Windows user-scoped DPAPI at rest; no machine-wide key or plaintext fallback."""
import base64
import ctypes
import json
import os

PREFIX="dpapi:v1:"


class CredentialError(ValueError):pass


def crypt(data,decode=False):
    if os.name!="nt":raise CredentialError("需要 Windows 用户级凭据保护；密钥未保存")
    if not isinstance(data,bytes) or len(data)>32768:raise CredentialError("密钥保护数据格式无效")
    from ctypes import wintypes as w
    class Blob(ctypes.Structure):_fields_=[("size",w.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));output=Blob()
    api=ctypes.WinDLL("crypt32",use_last_error=True);kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decode:
        fn=api.CryptUnprotectData;fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.POINTER(Blob)]
        description=None
    else:
        fn=api.CryptProtectData;fn.argtypes=[ctypes.POINTER(Blob),w.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.POINTER(Blob)]
        description="LingSuo QQ credential"
    fn.restype=w.BOOL
    if not fn(ctypes.byref(source),description,None,None,None,1,ctypes.byref(output)):raise CredentialError("无法读取或保护密钥，请使用原 Windows 账户，或重新填写 AppSecret；本地监控仍可使用")
    try:
        if output.size>32768:raise CredentialError("凭据数据超出限制")
        return ctypes.string_at(output.data,output.size)
    finally:
        if output.data:
            if decode:ctypes.memset(output.data,0,output.size)
            kernel.LocalFree(output.data)


def protect(secret,appid):
    raw=json.dumps({"application":"LingSuo","appid":appid,"secret":secret},ensure_ascii=False).encode("utf-8")
    return PREFIX+base64.b64encode(crypt(raw)).decode("ascii")


def unprotect(value,appid):
    try:
        if not isinstance(value,str) or not value.startswith(PREFIX) or len(value)>45000:raise ValueError()
        payload=json.loads(crypt(base64.b64decode(value[len(PREFIX):],validate=True),True))
        if set(payload)!={"application","appid","secret"} or payload["application"]!="LingSuo" or payload["appid"]!=appid or not isinstance(payload["secret"],str) or not 1<=len(payload["secret"])<=1024:raise ValueError()
        return payload["secret"]
    except (ValueError,TypeError,KeyError,UnicodeError):raise CredentialError("已保存密钥无法解密，请用原 Windows 账户或重新填写；本地监控仍可使用") from None
