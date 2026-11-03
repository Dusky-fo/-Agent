"""Explicitly authorized QQ attachments; never fetch on receipt of an event."""
import os
import re
import secrets
import threading
import time
from pathlib import Path
from .net import public_url,download
from .observers import safe_path


CDN_HOSTS=("multimedia.nt.qq.com.cn","qpic.cn","gchat.qpic.cn")


def filename(value):
    if not isinstance(value,str) or not value or len(value.encode("utf-16-le"))//2>180 or value!=Path(value).name or any(c in value for c in '\\/:*?"<>|') or any(ord(c)<32 for c in value) or value.endswith((" ",".")):
        raise ValueError("附件文件名无效；不允许路径、特殊字符或过长名称")
    if value.split(".")[0].upper() in {"CON","PRN","AUX","NUL",*("COM%s"%n for n in range(1,10)),*("LPT%s"%n for n in range(1,10))}:raise ValueError("附件使用 Windows 保留名称")
    return value


class Inbox:
    def __init__(self,config,directory,tasks,downloader=download):
        self.config,self.directory,self.tasks,self.download=config,Path(directory),tasks,downloader
        self.pending={};self.lock=threading.RLock()

    def authorize(self,user):
        cfg=self.config.get()
        if not cfg["qq"]["enabled"] or user not in cfg["qq"]["allowed_users"]:raise PermissionError("QQ 已禁用或用户未授权")
        inbox=cfg["inbound"]
        if not inbox["enabled"] or not inbox["directory"]:raise PermissionError("请先在本机启用 QQ 附件接收并指定落盘目录")
        root=safe_path(inbox["directory"],self.directory)
        if not root.is_dir():raise PermissionError("授权接收目录不存在")
        return root,inbox["max_mb"]*1024**2

    def offer(self,user,attachments):
        self.authorize(user)
        if not isinstance(attachments,list) or not 1<=len(attachments)<=10:raise ValueError("一次最多接收 10 个附件")
        rows=[]
        for item in attachments:
            getter=item.get if isinstance(item,dict) else lambda k,default=None:getattr(item,k,default)
            url=getter("url","")
            if isinstance(url,str) and url.startswith("//"):url="https:"+url
            public_url(url,CDN_HOSTS)
            name=getter("filename") or ("QQ图片-"+secrets.token_hex(4)+".jpg" if str(getter("content_type","")).startswith("image/") else "QQ附件-"+secrets.token_hex(4)+".bin")
            rows.append({"name":filename(name),"url":url})
        with self.lock:self.pending[user]=(time.monotonic()+120,rows)
        return "发现附件，尚未下载：\n"+"\n".join("%s. %s"%(i,r["name"]) for i,r in enumerate(rows,1))+"\n回复“接收”保存唯一附件，多个回复“接收 1”。2分钟内有效；仅保存，不打开或运行。"

    def receive(self,user,index=1):
        with self.lock:
            entry=self.pending.get(user)
            if not entry or entry[0]<=time.monotonic():self.pending.pop(user,None);raise ValueError("附件选择已过期，请重新发送附件")
            if index is None:
                if len(entry[1])!=1:raise ValueError("有多个附件，请回复“接收 编号”")
                index=1
            if not 1<=index<=len(entry[1]):raise ValueError("附件编号无效")
            row=entry[1][index-1];self.pending.pop(user,None)
        def execute(context):
            root,limit=self.authorize(user);target=root/row["name"]
            if target.exists():raise ValueError("接收目录已有同名文件，请先在本机处理；不会覆盖")
            import tempfile
            fd,temporary=tempfile.mkstemp(prefix=".lingsuo-receive-",suffix=".part",dir=root)
            try:
                with os.fdopen(fd,"wb") as output:
                    context.progress(10,"下载授权对话的附件")
                    result=self.download(row["url"],output,limit,check=context.check,allowed_hosts=CDN_HOSTS)
                context.progress(90,"重新核对授权并保存")
                current_root,current_limit=self.authorize(user)
                if current_root!=root:raise PermissionError("接收目录授权已变化")
                actual=Path(temporary).stat().st_size
                if actual!=result["size"] or not 0<actual<=min(limit,current_limit):raise ValueError("附件为空、大小不符或超过当前限制")
                # Windows rename is exclusive: concurrent same-name arrivals cannot overwrite.
                if os.name=="nt":os.rename(temporary,target)
                else:
                    os.link(temporary,target);Path(temporary).unlink()
                return {"file":target.name,"size":result["size"],"saved":True}
            finally:Path(temporary).unlink(missing_ok=True)
        return self.tasks.run("qq.attachment.receive",user,execute,timeout=180)
