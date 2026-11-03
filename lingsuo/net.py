"""Bounded public HTTPS reads with pinned DNS and no redirects or credentials."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit


def public_url(url, allowed_hosts=None):
    if not isinstance(url,str) or len(url)>8192 or any(ord(c)<33 for c in url):raise ValueError("网址长度无效或含空白控制字符")
    parsed=urlsplit(url)
    host=(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme!="https" or not host or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443):
        raise ValueError("只支持无账号信息的 HTTPS 443 网址")
    if parsed.fragment:raise ValueError("网址不能包含片段")
    if allowed_hosts is not None and not any(host==h or host.endswith("."+h) for h in allowed_hosts):raise PermissionError("网址不属于明确授权的主机")
    return parsed,host


def download(url, output, limit, check=lambda:None, allowed_hosts=None):
    parsed,host=public_url(url,allowed_hosts)
    addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise PermissionError("拒绝访问本地、内网或特殊网络地址")
    # Connect to a validated address, keep hostname for TLS SNI/certificate validation.
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            check()
            raw=socket.create_connection((addresses[0][4][0],443),timeout=15)
            try:self.sock=self._context.wrap_socket(raw,server_hostname=host)
            except BaseException:raw.close();raise
    conn=PinnedHTTPS(host,timeout=15,context=ssl.create_default_context())
    try:
        check();conn.request("GET",parsed.path+("?"+parsed.query if parsed.query else "") or "/",headers={"User-Agent":"LingSuo/1.0","Accept-Encoding":"identity"})
        response=conn.getresponse()
        if response.status!=200:raise ValueError("远端拒绝下载（HTTP %s）；不跟随重定向"%response.status)
        length=response.getheader("Content-Length")
        if length is not None:
            try:size=int(length)
            except ValueError:raise ValueError("远端返回了无效文件大小") from None
            if size<0 or size>limit:raise ValueError("远端文件超过大小限制")
        total=0
        while True:
            check();block=response.read(min(65536,limit-total+1))
            if not block:break
            total+=len(block)
            if total>limit:raise ValueError("下载超过大小限制")
            output.write(block)
        if length is not None and total!=int(length):raise ValueError("下载不完整，未保存文件")
        return {"size":total,"content_type":response.getheader("Content-Type","")}
    finally:conn.close()
