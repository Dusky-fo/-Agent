"""Durable cooperative tasks. Cancellation never kills a thread or retries a side effect."""
import json
import secrets
import time
from dataclasses import dataclass


class TaskCancelled(RuntimeError): user_safe=True
class TaskTimedOut(RuntimeError): user_safe=True


@dataclass
class Outcome:
    result: dict
    state: str = "completed"


class TaskContext:
    def __init__(self, manager, ident, deadline):
        self.manager, self.id, self.deadline = manager, ident, deadline
        self.guard=None

    @property
    def owner(self):return self.manager.get(self.id)["owner"]

    def check(self):
        row = self.manager.get(self.id)
        if row["cancel_requested"]: raise TaskCancelled("用户已请求取消")
        if time.monotonic() >= self.deadline: raise TaskTimedOut("任务超过执行时限")
        if self.guard:self.guard()

    def progress(self, percent, step):
        self.check()
        self.manager.update(self.id, progress=max(0,min(99,int(percent))),step=str(step)[:120])


class Tasks:
    VIEW="""SELECT id,kind,owner,state,progress,step,result,error,cancel_requested,created,updated,received FROM tasks
      UNION ALL SELECT 'N'||o.id,'qq.notification',o.user,
      CASE o.state WHEN 'pending' THEN 'queued' WHEN 'sending' THEN 'running' WHEN 'sent' THEN 'platform_accepted' ELSE o.state END,
      CASE WHEN o.state='sent' THEN 100 ELSE 0 END,
      CASE WHEN o.state='sent' THEN '平台已接受，等待用户核对' WHEN o.state='pending' THEN '通知队列等待发送' ELSE '通知已结束' END,
      '{}',o.error,0,o.created,o.created,t.confirmed FROM outbox o LEFT JOIN tests t ON t.outbox_id=o.id"""
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT,owner TEXT,state TEXT,progress INTEGER,step TEXT,result TEXT,error TEXT,cancel_requested INTEGER,created REAL,updated REAL,received REAL)")
            store.db.execute("UPDATE tasks SET state='interrupted',error='上次退出时任务未结束；未自动重试',updated=? WHERE state IN ('running','queued')",(time.time(),))

    def create(self, kind, owner=""):
        ident = secrets.token_hex(6).upper(); now = time.time()
        with self.store.lock,self.store.db:
            if self.store.db.execute("SELECT count(*) FROM tasks WHERE state IN ('running','queued')").fetchone()[0] >= 32:
                raise ValueError("正在执行的任务过多")
            self.store.db.execute("INSERT INTO tasks VALUES(?,?,?,'queued',0,'等待执行','{}','',0,?,?,NULL)",(ident,kind,owner,now,now))
            self.store.db.execute("DELETE FROM tasks WHERE state NOT IN ('running','queued') AND id NOT IN (SELECT id FROM tasks ORDER BY created DESC LIMIT 1000)")
        return ident

    def update(self, ident, **values):
        if set(values)-{"state","progress","step","result","error","cancel_requested","received"}:raise ValueError("未知任务字段")
        values["updated"] = time.time()
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE tasks SET "+",".join(k+"=?" for k in values)+" WHERE id=?",(*values.values(),ident))

    def get(self, ident, owner=None):
        ident=str(ident).upper()
        if ident.startswith("N") and ident[1:].isdecimal():
            with self.store.lock:
                row=self.store.db.execute("SELECT * FROM ("+self.VIEW+") WHERE id=?"+(" AND owner=?" if owner is not None else ""),(ident,owner) if owner is not None else (ident,)).fetchone()
            if not row:raise PermissionError("任务不存在或不属于当前用户")
            return dict(row)
        with self.store.lock:
            row=self.store.db.execute("SELECT * FROM tasks WHERE id=?",(ident,)).fetchone()
        if not row or (owner is not None and row["owner"] != owner):raise PermissionError("任务不存在或不属于当前用户")
        return dict(row)

    def recent(self, owner=None, limit=30):
        return self.query(owner=owner,limit=max(1,min(100,limit)))["rows"]

    def query(self,*,owner=None,states=None,kind="",text="",since=None,until=None,attention=False,limit=30,offset=0):
        """Apply identity, filters and priority BEFORE pagination over both task stores."""
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=20000:raise ValueError("任务分页参数无效")
        if not isinstance(text,str) or len(text)>200 or not isinstance(kind,str) or len(kind)>100:raise ValueError("任务搜索条件无效")
        where=[];args=[]
        if owner is not None:where.append("owner=?");args.append(owner)
        if states:
            if not isinstance(states,(list,tuple,set)) or any(s not in STATE_NAMES for s in states):raise ValueError("任务状态筛选无效")
            where.append("state IN ("+",".join("?" for _ in states)+")");args.extend(states)
        if kind:where.append("kind=?");args.append(kind)
        if text:
            pattern="%"+text.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
            where.append("(id LIKE ? ESCAPE '\\' OR kind LIKE ? ESCAPE '\\' OR result LIKE ? ESCAPE '\\')");args.extend([pattern]*3)
        for value,operator in ((since,">="),(until,"<=")):
            if value is not None:
                import math
                if type(value) not in (int,float) or not math.isfinite(value):raise ValueError("任务日期筛选无效")
                where.append("created"+operator+"?");args.append(value)
        sql=" FROM ("+self.VIEW+")"+(" WHERE "+" AND ".join(where) if where else "")
        order="CASE WHEN state IN ('queued','running','uncertain','failed','timed_out','interrupted') THEN 0 ELSE 1 END," if attention else ""
        with self.store.lock:
            total=self.store.db.execute("SELECT count(*)"+sql,args).fetchone()[0]
            rows=[dict(r) for r in self.store.db.execute("SELECT *"+sql+" ORDER BY "+order+"created DESC,id DESC LIMIT ? OFFSET ?",(*args,limit,offset))]
        return {"rows":rows,"total":total,"offset":offset,"limit":limit}

    def notification_rows(self,owner=None,limit=30):
        with self.store.lock:
            query="SELECT o.*,t.confirmed FROM outbox o LEFT JOIN tests t ON t.outbox_id=o.id"+(" WHERE o.user=?" if owner is not None else "")+" ORDER BY o.id DESC LIMIT ?"
            rows=self.store.db.execute(query,(owner,limit) if owner is not None else (limit,)).fetchall()
        return [{"id":"N"+str(r["id"]),"kind":"qq.notification","owner":r["user"],"state":{"pending":"queued","sending":"running","sent":"platform_accepted"}.get(r["state"],r["state"]),"progress":100 if r["state"]=="sent" else 0,"step":"平台已接受，等待用户核对" if r["state"]=="sent" else "通知队列等待发送" if r["state"]=="pending" else "通知已结束","result":"{}","error":r["error"],"received":r["confirmed"],"created":r["created"],"updated":r["created"],"cancel_requested":0} for r in rows]

    def cancel(self, ident, owner=None):
        row=self.get(ident,owner)
        if row["state"] not in {"queued","running"}:raise ValueError("任务已结束，不能撤回已执行的操作")
        if row["id"].startswith("N"):
            if row["state"]=="running":raise ValueError("通知请求已开始，不能撤回平台提交；请等待结果")
            with self.store.lock,self.store.db:self.store.db.execute("UPDATE outbox SET state='cancelled' WHERE id=? AND state='pending'",(int(row["id"][1:]),))
            return
        self.update(row["id"],cancel_requested=1,step="已请求取消，等待当前步骤安全结束")

    def confirm_received(self, ident, owner=None):
        row=self.get(ident,owner)
        if row["state"] != "platform_accepted":raise ValueError("任务尚未被平台接受，不能确认收到")
        if row["id"].startswith("N"):
            with self.store.lock,self.store.db:
                changed=self.store.db.execute("UPDATE tests SET confirmed=COALESCE(confirmed,?) WHERE outbox_id=?",(time.time(),int(row["id"][1:]))).rowcount
            if not changed:raise ValueError("普通通知请在 QQ 核对；只有编号测试通知和文件任务支持收到确认")
            return
        self.update(row["id"],received=row["received"] or time.time())

    def run(self, kind, owner, fn, timeout=120):
        if not 1 <= timeout <= 3600:raise ValueError("任务时限须为 1–3600 秒")
        ident=self.create(kind,owner);self.update(ident,state="running",step="执行中")
        context=TaskContext(self,ident,time.monotonic()+timeout)
        try:
            context.check();out=fn(context)
            out=out if isinstance(out,Outcome) else Outcome(out or {})
            if out.state not in {"completed","platform_accepted"}:raise ValueError("任务返回状态无效")
            encoded=json.dumps(out.result,ensure_ascii=False)
            if len(encoded)>1024*1024:encoded=json.dumps({"summary":"结果较大，仅保留状态，请查看对应功能结果"},ensure_ascii=False)
            self.update(ident,state=out.state,progress=100,step="平台已接受，等待用户核对" if out.state=="platform_accepted" else "执行完成",result=encoded)
            return self.get(ident)
        except (TaskCancelled,TaskTimedOut) as exc:
            exc.task_id=ident
            self.update(ident,state="cancelled" if isinstance(exc,TaskCancelled) else "timed_out",error=str(exc),step="已停止")
            raise
        except Exception as exc:
            exc.task_id=ident
            # Raw transport bodies, credentials and document contents are not persisted.
            self.update(ident,state="uncertain" if getattr(exc,"outcome_uncertain",False) else "failed",error=str(exc)[:300] if isinstance(exc,(ValueError,PermissionError)) or getattr(exc,"user_safe",False) else "执行失败，请检查任务相关设置或平台错误记录",step="结果待核对" if getattr(exc,"outcome_uncertain",False) else "执行失败")
            raise

    def shutdown(self):
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE tasks SET cancel_requested=1 WHERE state IN ('running','queued')")


STATE_NAMES={"queued":"等待","running":"执行中","completed":"执行完成","platform_accepted":"平台已接受","cancelled":"已取消","timed_out":"超时","failed":"失败","interrupted":"上次中断","uncertain":"结果待核对"}
