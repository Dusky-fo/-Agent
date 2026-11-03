"""Durable subscriptions, bounded history, deduplication, and retry outbox."""
import json
import sqlite3
import threading
import time


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS subscribers(user TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, user TEXT, text TEXT, key TEXT,
          attempts INTEGER DEFAULT 0, due REAL, state TEXT DEFAULT 'pending', error TEXT DEFAULT '', created REAL);
        CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY, ts REAL);
        CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY, user TEXT, appid TEXT, outbox_id INTEGER,
          created REAL, accepted REAL, confirmed REAL);
        CREATE TABLE IF NOT EXISTS reply_context(user TEXT PRIMARY KEY,scope TEXT,message_id TEXT,ts REAL,seq INTEGER);
        CREATE TABLE IF NOT EXISTS reply_messages(user TEXT,scope TEXT,message_id TEXT,ts REAL,seq INTEGER,PRIMARY KEY(user,scope,message_id));
        """)
        self.db.execute("INSERT OR IGNORE INTO reply_messages SELECT user,scope,message_id,ts,seq FROM reply_context")
        self.db.commit()
        self.db.execute("UPDATE outbox SET state='uncertain',error='上次退出时请求未结束；未自动重发' WHERE state='sending'")
        self.db.commit()
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(outbox)")}
        if "created" not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN created REAL")
            self.db.execute("UPDATE outbox SET created=due WHERE created IS NULL")
            self.db.commit()
        if "merges" not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN merges INTEGER DEFAULT 1")
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def event(self, kind, text):
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(ts,kind,text) VALUES(?,?,?)", (time.time(), kind, str(text)[:2000]))
            self.db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)")

    def history(self, limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (max(1, min(100, int(limit))),))]

    def subscribe(self, user, enabled):
        with self.lock, self.db:
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO subscribers VALUES(?)", (user,))
            else:
                self.db.execute("DELETE FROM subscribers WHERE user=?", (user,))
                self.db.execute("UPDATE outbox SET state='cancelled' WHERE user=? AND state='pending'", (user,))

    def subscribers(self):
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT user FROM subscribers")]

    def claim(self, message_id):
        if not message_id:
            return False
        with self.lock, self.db:
            self.db.execute("DELETE FROM inbox WHERE ts<?", (time.time() - 86400,))
            return self.db.execute("INSERT OR IGNORE INTO inbox VALUES(?,?)", (message_id, time.time())).rowcount == 1

    def enqueue(self, user, text, key, cooldown):
        with self.lock, self.db:
            recent=self.db.execute("SELECT id,state,merges FROM outbox WHERE user=? AND key=? AND created>? ORDER BY id DESC LIMIT 1", (user, key, time.time() - cooldown)).fetchone()
            if recent:
                if recent["state"]=="pending":self.db.execute("UPDATE outbox SET text=?,merges=? WHERE id=?",((str(text)[:1800]+"\n（已合并 %s 条同类事件，显示最近一条）"%(recent["merges"]+1)),recent["merges"]+1,recent["id"]))
                return False
            if self.db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0] >= 3000:
                self.event("notify.full", "待发队列已满，新事件仅保留在本地历史")
                return False
            now = time.time()
            self.db.execute("INSERT INTO outbox(user,text,key,due,created) VALUES(?,?,?,?,?)", (user, text, key, now, now))
            self.db.execute("DELETE FROM outbox WHERE state!='pending' AND id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 1000)")
            return True

    def due(self):
        self.expire_pending()
        with self.lock,self.db:
            return [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE state='pending' AND due<=? ORDER BY id LIMIT 10", (time.time(),))]

    def expire_pending(self):
        with self.lock,self.db:self.db.execute("UPDATE outbox SET state='timed_out',error='等待超过24小时，未继续发送旧通知' WHERE state='pending' AND created<?",(time.time()-86400,))

    def claim_outbox(self,ident):
        with self.lock,self.db:return self.db.execute("UPDATE outbox SET state='sending' WHERE id=? AND state='pending'",(ident,)).rowcount==1

    def finish(self, item, error, max_attempts, uncertain=False):
        attempts = item["attempts"] + 1
        state = "uncertain" if uncertain else "sent" if not error else ("failed" if attempts >= max_attempts else "pending")
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET attempts=?,state=?,due=?,error=? WHERE id=?", (attempts, state, time.time() + min(300, 5 * 2**attempts), str(error)[:200], item["id"]))
            if not error:
                self.db.execute("UPDATE tests SET accepted=COALESCE(accepted,?) WHERE outbox_id=?", (time.time(), item["id"]))

    def create_test(self, user, appid):
        import secrets
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM tests WHERE user=? AND created>?", (user, time.time() - 5)).fetchone():
                raise ValueError("请等待5秒再发送下一条测试通知")
            ident = secrets.token_hex(5).upper()
            now = time.time()
            cursor = self.db.execute("INSERT INTO outbox(user,text,key,due,created) VALUES(?,?,?,?,?)",
                                     (user, "灵梭测试通知（编号 %s）。请在本机找到相同编号，再点击“我已收到”。" % ident, "test:" + ident, now, now))
            self.db.execute("INSERT INTO tests(id,user,appid,outbox_id,created) VALUES(?,?,?,?,?)", (ident, user, appid, cursor.lastrowid, now))
            self.db.execute("DELETE FROM tests WHERE created<?", (now - 7 * 86400,))
            return {"id": ident, "state": "pending"}

    def tests(self, appid, allowed):
        with self.lock:
            rows = self.db.execute("SELECT t.*,o.state,o.error FROM tests t LEFT JOIN outbox o ON o.id=t.outbox_id WHERE t.appid=? ORDER BY t.created DESC LIMIT 20", (appid,))
            return [dict(r) for r in rows if r["user"] in allowed]

    def confirm_test(self, ident, appid, allowed):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM tests WHERE id=? AND appid=?", (ident, appid)).fetchone()
            if not row or row["user"] not in allowed or not row["accepted"]:
                raise ValueError("该测试尚未被平台接受或接收人已撤销授权，不能标记收到")
            self.db.execute("UPDATE tests SET confirmed=COALESCE(confirmed,?) WHERE id=?", (time.time(), ident))

    def cancel_pending(self):
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET state='cancelled' WHERE state='pending'")
            self.db.execute("DELETE FROM reply_context")
            self.db.execute("DELETE FROM reply_messages")

    def remember_reply(self,user,scope,message_id):
        with self.lock,self.db:
            self.db.execute("DELETE FROM reply_messages WHERE ts<?",(time.time()-3600,))
            self.db.execute("INSERT OR IGNORE INTO reply_messages VALUES(?,?,?,?,0)",(user,scope,message_id,time.time()))
            self.db.execute("DELETE FROM reply_messages WHERE rowid NOT IN (SELECT rowid FROM reply_messages ORDER BY ts DESC LIMIT 10000)")

    def reserve_reply(self,user,scope,message_id=None):
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM reply_messages WHERE user=? AND scope=? AND ts>?"+(" AND message_id=?" if message_id else "")+" ORDER BY ts DESC LIMIT 1",(user,scope,time.time()-3600,message_id) if message_id else (user,scope,time.time()-3600)).fetchone()
            if not row or row["seq"]>=5:return None
            seq=row["seq"]+1
            self.db.execute("UPDATE reply_messages SET seq=? WHERE user=? AND scope=? AND message_id=?",(seq,user,scope,row["message_id"]))
            return row["message_id"],seq

    def defer(self,item,reason):
        with self.lock,self.db:
            self.db.execute("UPDATE outbox SET state='pending',due=?,error=? WHERE id=? AND state='sending'",(time.time()+30,reason[:200],item["id"]))

    def queue_summary(self):
        with self.lock:
            return {r[0]: r[1] for r in self.db.execute("SELECT state,count(*) FROM outbox GROUP BY state")}
