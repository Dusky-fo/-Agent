"""Explicit host permissions and deterministic workflows, with no model integration."""
import io
import secrets
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from .net import public_url,download
from .plugins import Registry


@dataclass(frozen=True)
class Caller:
    owner:str
    origin:str
    permissions:frozenset


class WebTasks:
    def __init__(self,downloader=download):self.download=downloader;self.grants={}

    def grant(self,url):
        _,host=public_url(url)
        token=secrets.token_hex(16)
        self.grants={k:v for k,v in self.grants.items() if v[1]>time.monotonic()}
        if len(self.grants)>=20:raise ValueError("网页授权过多，请稍后重试")
        self.grants[token]=(url,time.monotonic()+120,host)
        return token

    def read(self,url,consent,context):
        grant=self.grants.pop(consent,None)
        if not grant or grant[0]!=url or grant[1]<=time.monotonic():raise PermissionError("网页任务须在本机确认该完整网址，授权只使用一次、2分钟有效")
        context.progress(10,"读取已授权的公开网页；不执行脚本或提交表单")
        buffer=io.BytesIO();result=self.download(url,buffer,1024**2,check=context.check,allowed_hosts=(grant[2],))
        if "text/html" not in result["content_type"].lower() and "text/plain" not in result["content_type"].lower():raise ValueError("网页任务只读取 HTML 或纯文本")
        class Text(HTMLParser):
            def __init__(self):super().__init__();self.hidden=0;self.parts=[]
            def handle_starttag(self,tag,attrs):
                if tag in ("script","style","noscript"):self.hidden+=1
            def handle_endtag(self,tag):
                if tag in ("script","style","noscript"):self.hidden=max(0,self.hidden-1)
            def handle_data(self,data):
                if not self.hidden and data.strip():self.parts.append(data.strip())
        parser=Text();parser.feed(buffer.getvalue().decode("utf-8",errors="replace"))
        text="\n".join(parser.parts)
        context.check()
        return {"host":grant[2],"characters":len(text),"text":text[:10000],"truncated":len(text)>10000,"encoding":"UTF-8；非 UTF-8 页面可能显示替换字符"}


class ToolExecutor:
    def __init__(self,tasks):self.tasks=tasks;self.registry=Registry();self.descriptors={}

    def register(self,name,description,handler,schema,permissions,origins=("local",)):
        if name in self.descriptors:raise ValueError("工具名称重复")
        self.descriptors[name]={"name":name,"version":"1.0","description":description,"parameters":schema,"permissions":sorted(permissions),"origins":list(origins)}
        self.registry.register(name,description,lambda **params:lambda context:handler(context,**params),schema)

    def call(self,name,params,caller,context):
        spec=self.descriptors.get(name)
        if not isinstance(caller,Caller) or not spec or caller.origin not in spec["origins"] or not set(spec["permissions"]).issubset(caller.permissions):raise PermissionError("工具未注册，或当前入口未获得所需权限；参数不能授予权限")
        context.check()
        # The context is supplied by this host, never taken from tool parameters.
        checked=self.registry.call(name,params)
        return checked(context) if callable(checked) else checked

    def execute(self,name,params,caller,timeout=120):
        return self.tasks.run(name,caller.owner,lambda context:self.call(name,params,caller,context),timeout)

    def workflow(self,steps,caller,timeout=300):
        if not isinstance(steps,list) or not 1<=len(steps)<=10:raise ValueError("固定编排支持 1–10 步")
        # Validate every permission/schema before any side effect happens.
        for step in steps:
            if not isinstance(step,dict) or set(step)!={"tool","parameters"}:raise ValueError("编排步骤格式无效")
            spec=self.descriptors.get(step["tool"])
            if not spec or caller.origin not in spec["origins"] or not set(spec["permissions"]).issubset(caller.permissions):raise PermissionError("编排含未授权工具")
            self.registry.validate(step["tool"],step["parameters"])
        def run(context):
            results=[]
            for i,step in enumerate(steps):
                context.progress(int(i*95/len(steps)),"固定任务步骤 %s/%s"%(i+1,len(steps)))
                results.append(self.call(step["tool"],step["parameters"],caller,context))
            return {"steps":results}
        return self.tasks.run("rules.workflow",caller.owner,run,timeout)
