"""Local-only scoped file plans, exclusive writes, durable backups and recovery."""
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from .inbound import filename
from .observers import safe_path
from .file_locks import stable_original


class FileManager:
    LIMIT=100*1024**2
    BACKUP_LIMIT=512*1024**2

    def __init__(self,config,directory,tasks):
        self.config,self.directory,self.tasks=config,Path(directory).resolve(),tasks
        self.plans={};self.lock=threading.RLock()
        self.journals=self.directory/"file-recovery";self.journals.mkdir(exist_ok=True)

    def authorize(self,path,exists=True):
        p=safe_path(path,self.directory,exists)
        roots=[safe_path(r,self.directory) for r in self.config.get()["file_manager"]["roots"]]
        if not any(p.is_relative_to(r) for r in roots):raise PermissionError("路径不在本机文件管理授权范围内；此授权不会开放给 QQ")
        if exists and not p.is_file():raise ValueError("请选择普通文件，不支持移动整个目录")
        return p

    def digest(self,path,check=lambda:None):
        p=self.authorize(path);before=p.stat()
        if before.st_size>self.LIMIT:raise ValueError("文件管理单文件上限 100 MB")
        digest=hashlib.sha256();total=0
        with p.open("rb") as handle:
            while block:=handle.read(65536):
                check();total+=len(block)
                if total>self.LIMIT:raise ValueError("文件超过限制")
                digest.update(block)
        after=p.stat()
        if (after.st_size,after.st_mtime_ns,after.st_ino)!=(before.st_size,before.st_mtime_ns,before.st_ino):raise ValueError("读取期间文件已变化，请重新预览")
        return {"size":total,"sha256":digest.hexdigest(),"mtime":before.st_mtime_ns}

    def search(self,query=""):
        if not isinstance(query,str) or len(query)>200:raise ValueError("搜索词过长")
        result=[];visited=0
        for value in self.config.get()["file_manager"]["roots"]:
            root=safe_path(value,self.directory)
            for folder,dirs,files in os.walk(root,followlinks=False):
                visited+=1
                if visited>1500:return result
                dirs[:]=[d for d in sorted(dirs) if not (Path(folder)/d).is_symlink() and not getattr(Path(folder)/d,"is_junction",lambda:False)() and not (Path(folder)/d).resolve().is_relative_to(self.directory)]
                if len(Path(folder).relative_to(root).parts)>=6:dirs[:]=[]
                for name in sorted(files):
                    if query.casefold() not in name.casefold():continue
                    try:
                        p=self.authorize(Path(folder)/name);result.append({"path":str(p),"name":name,"size":p.stat().st_size})
                    except (OSError,PermissionError,ValueError):continue
                    if len(result)>=100:return result
        return result

    def preview(self,changes):
        if not isinstance(changes,list) or not 1<=len(changes)<=100:raise ValueError("一次预览 1–100 个文件")
        rows=[];sources=set();targets=set();total=0
        for source,target in changes:
            src=self.authorize(source);dst=self.authorize(target,False);filename(dst.name)
            if src==dst or src in sources or dst in targets or dst.exists():raise ValueError("文件重复、目标相同或目标已存在；不会覆盖")
            if dst.parent.exists() and not dst.parent.is_dir():raise ValueError("目标父路径不是目录")
            ancestor=dst.parent
            while not ancestor.exists():ancestor=ancestor.parent
            if src.stat().st_dev!=ancestor.stat().st_dev:raise ValueError("当前只支持同一磁盘卷内移动；跨盘移动尚未开放")
            info=self.digest(src);total+=info["size"]
            if total>self.LIMIT:raise ValueError("单次整理总量不能超过 100 MB")
            sources.add(src);targets.add(dst);rows.append({"source":str(src),"target":str(dst),**info})
        if sources & targets:raise ValueError("目标不能同时是本次其他源文件")
        ident=secrets.token_hex(6).upper()
        with self.lock:
            self.plans={k:v for k,v in self.plans.items() if v["expires"]>time.monotonic()}
            if len(self.plans)>=20:raise ValueError("待确认预览过多，请稍后重试")
            self.plans[ident]={"id":ident,"rows":rows,"expires":time.monotonic()+300}
        return {"id":ident,"rows":rows,"backup_bytes":total}

    def organize(self,paths,directory):
        dest=self.authorize(Path(directory)/"_permission_probe_",False).parent
        return self.preview([(p,dest/((Path(p).suffix[1:].lower() or "无扩展名")[:20])/Path(p).name) for p in paths])

    def write_journal(self,journal):
        target=self.journals/(journal["id"]+".json");temporary=target.with_suffix(".tmp")
        with temporary.open("w",encoding="utf-8") as handle:
            json.dump(journal,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,target)

    @staticmethod
    def copy_exclusive(source,target,check=lambda:None):
        created=False
        try:
            with Path(source).open("rb") as inp,Path(target).open("xb") as out:
                created=True
                while block:=inp.read(65536):check();out.write(block)
                out.flush();os.fsync(out.fileno())
        except BaseException:
            if created:Path(target).unlink(missing_ok=True)
            raise

    @staticmethod
    def move_exclusive(source,target):
        if os.name=="nt":os.rename(source,target)
        else:
            # No overwrite, even in fixture runs on POSIX. Keep the same inode/content.
            os.link(source,target);Path(source).unlink()

    def execute(self,ident,context):
        with self.lock:plan=self.plans.pop(ident,None)
        if not plan or plan["expires"]<=time.monotonic():raise ValueError("预览已过期或已提交，请重新预览")
        if self.backup_usage()+sum(row["size"] for row in plan["rows"])>self.BACKUP_LIMIT:raise ValueError("恢复备份将超过 512 MB，请先到恢复记录核对备份占用；没有移动任何文件")
        journal={"id":ident,"created":time.time(),"state":"preparing","rows":[]}
        folder=self.journals/ident;folder.mkdir()
        try:
            for i,row in enumerate(plan["rows"]):
                context.progress(int(i*30/len(plan["rows"])),"校验文件并保留恢复备份")
                if self.digest(row["source"],context.check)!={k:row[k] for k in ("size","sha256","mtime")}:raise ValueError("文件内容或时间已变化，请重新预览")
                dst=self.authorize(row["target"],False)
                if dst.exists():raise ValueError("目标已有同名文件，请重新预览")
                backup=folder/(str(i)+".bak");self.copy_exclusive(row["source"],backup,context.check)
                if hashlib.sha256(backup.read_bytes()).hexdigest()!=row["sha256"]:raise ValueError("备份校验失败，未移动此文件")
                journal["rows"].append({**row,"backup":str(backup),"phase":"backed_up"})
                self.write_journal(journal)
            journal["state"]="executing";self.write_journal(journal)
            for i,row in enumerate(journal["rows"]):
                context.progress(30+int(i*65/len(journal["rows"])),"执行已确认的文件变更")
                src=self.authorize(row["source"]);dst=self.authorize(row["target"],False)
                if self.digest(src)["sha256"]!=row["sha256"]:raise ValueError("执行前源文件已变化，停止后续操作")
                dst.parent.mkdir(parents=True,exist_ok=True);self.authorize(dst,False)
                # Same-volume atomic rename preserves concurrent writes instead of copy/delete.
                row["phase"]="moving";self.write_journal(journal);context.check()
                self.move_exclusive(src,dst)
                row["phase"]="moved";self.write_journal(journal)
            journal["state"]="completed";self.write_journal(journal)
            return {"recovery_id":ident,"files":len(journal["rows"]),"state":"completed","completed":len(journal["rows"]),"total":len(plan["rows"])}
        except BaseException:
            journal["state"]="partial";self.write_journal(journal)
            raise

    def history(self):
        rows=[]
        for p in self.journals.glob("*.json"):
            try:
                if p.stat().st_size>1024**2:continue
                item=json.loads(p.read_text(encoding="utf-8"));rows.append({"id":item["id"],"state":item["state"],"created":item["created"],"files":len(item["rows"]),"names":"、".join(Path(r["source"]).name for r in item["rows"][:4])})
            except (OSError,ValueError,KeyError):continue
        return sorted(rows,key=lambda r:r["created"],reverse=True)[:100]

    def backup_usage(self):return sum(p.stat().st_size for p in self.journals.rglob("*.bak") if p.is_file() and not p.is_symlink())

    def details(self,ident):
        if not isinstance(ident,str) or len(ident)!=12 or any(c not in "0123456789ABCDEF" for c in ident):raise ValueError("恢复编号无效")
        path=self.journals/(ident+".json")
        if path.stat().st_size>1024**2:raise ValueError("恢复记录过大")
        return json.loads(path.read_text(encoding="utf-8"))

    def recover(self,ident,context):
        if not isinstance(ident,str) or len(ident)!=12 or any(c not in "0123456789ABCDEF" for c in ident):raise ValueError("恢复编号无效")
        with self.lock:
            journal=json.loads((self.journals/(ident+".json")).read_text(encoding="utf-8"))
            if journal["state"] in {"cleaned","cleanup_pending"}:raise ValueError("此记录的备份已清理或正在清理，不能恢复；原位置文件已核对保留")
            for i,row in enumerate(journal["rows"]):
                context.progress(int(i*95/max(1,len(journal["rows"]))),"校验并恢复文件")
                if row["phase"] not in ("moved","moving","copied"):continue
                src=self.authorize(row["source"],False);dst=self.authorize(row["target"],False)
                backup=Path(row["backup"])
                if not backup.resolve().is_relative_to((self.journals/ident).resolve()) or hashlib.sha256(backup.read_bytes()).hexdigest()!=row["sha256"]:raise ValueError("恢复备份无效")
                if dst.exists() and self.digest(dst)["sha256"]!=row["sha256"]:raise ValueError("目标文件已修改，不能自动恢复，请手动核对备份")
                if src.exists():
                    if self.digest(src)["sha256"]!=row["sha256"]:raise ValueError("源位置已有不同文件，不会覆盖")
                    if dst.exists():raise ValueError("源位置和目标均存在文件，请手动核对；不会自动删除任何一份")
                else:
                    src.parent.mkdir(parents=True,exist_ok=True);self.authorize(src,False)
                    if dst.exists():self.move_exclusive(dst,src)
                    else:self.copy_exclusive(backup,src,context.check)
                row["phase"]="restored";self.write_journal(journal)
            journal["state"]="restored";self.write_journal(journal)
            return {"recovery_id":ident,"state":"restored"}

    def cleanup_preview(self,ident):
        """Only restored, unchanged originals may release their redundant private copies."""
        with self.lock:
            item=self.details(ident)
            if item["state"] not in {"restored","cleanup_pending"} or not item["rows"]:raise ValueError("仅已恢复并可核对原文件的记录允许清理；未恢复的备份必须保留")
            total=0;files=[]
            for row in item["rows"]:
                if row["phase"]!="restored" or self.digest(row["source"])["sha256"]!=row["sha256"]:raise ValueError("原位置文件已变化或尚未恢复，保留备份")
                backup=Path(row["backup"]);folder=(self.journals/ident).resolve()
                if any(p.is_symlink() or getattr(p,"is_junction",lambda:False)() for p in (backup,*backup.parents)) or backup.parent.resolve()!=folder or backup.suffix!=".bak":raise PermissionError("备份路径无效，不执行清理")
                if not backup.exists():
                    if item["state"]=="cleanup_pending":continue
                    raise ValueError("备份缺失，请先核对，不执行清理")
                if not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest()!=row["sha256"]:raise ValueError("备份内容变化，不执行清理")
                files.append(str(backup));total+=backup.stat().st_size
            digest=hashlib.sha256((self.journals/(ident+".json")).read_bytes()).hexdigest()
            return {"id":ident,"journal_sha256":digest,"files":files,"bytes":total,"originals":[r["source"] for r in item["rows"]]}

    def cleanup(self,preview,context):
        with self.lock,ExitStack() as stack:
            for source in preview["originals"]:stack.enter_context(stable_original(self.authorize(source)))
            current=self.cleanup_preview(preview["id"])
            if current!=preview:raise ValueError("清理预览已变化，请重新核对")
            item=self.details(preview["id"]);item["state"]="cleanup_pending";self.write_journal(item)
            for index,path in enumerate(current["files"]):
                context.progress(int(index*95/max(1,len(current["files"]))),"清理已恢复记录的冗余备份")
                # Recheck originals and private paths at each cancellation boundary.
                verified=self.cleanup_preview(preview["id"])
                if path not in verified["files"]:raise ValueError("清理文件已变化，请重新核对")
                Path(path).unlink()
            item["state"]="cleaned";self.write_journal(item)
            return {"recovery_id":item["id"],"files":len(current["files"]),"bytes_reclaimed":current["bytes"],"source_kept":True}
