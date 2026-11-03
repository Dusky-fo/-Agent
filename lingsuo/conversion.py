"""Small, explicit document conversions. No Office launch, macros, PDF or OCR."""
import html
import io
import zipfile
from pathlib import Path


class Documents:
    PAIRS={( ".txt",".docx"),(".md",".docx"),(".docx",".txt"),(".txt",".html"),(".md",".html")}

    def __init__(self,manager):self.manager=manager

    def preview(self,source,target):
        src=self.manager.authorize(source);dst=self.manager.authorize(target,False)
        if (src.suffix.lower(),dst.suffix.lower()) not in self.PAIRS:raise ValueError("支持 TXT/MD→DOCX、DOCX→TXT、TXT/MD→HTML；不支持 PDF、旧 DOC 或 OCR")
        if dst.exists():raise ValueError("输出文件已存在，不会覆盖")
        info=self.manager.digest(src)
        if info["size"]>5*1024**2:raise ValueError("文档输入上限 5 MB")
        return {"source":str(src),"target":str(dst),**info}

    def execute(self,expected,context):
        current=self.preview(expected["source"],expected["target"])
        if current!=expected:raise ValueError("文档已变化，请重新预览")
        src=Path(current["source"]);dst=Path(current["target"])
        context.progress(10,"读取授权文档并转换")
        with src.open("rb") as handle:raw=handle.read(5*1024**2+1)
        if len(raw)>5*1024**2:raise ValueError("文档在读取时超过限制")
        if src.suffix.lower()==".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info=archive.infolist()
                if len(info)>1000 or sum(i.file_size for i in info)>20*1024**2 or any(i.flag_bits&1 for i in info):raise ValueError("DOCX 解压规模过大或加密，不支持转换")
            from docx import Document
            document=Document(io.BytesIO(raw));parts=[]
            if len(document.paragraphs)>10000:raise ValueError("文档段落过多，请拆分后转换")
            for para in document.paragraphs:context.check();parts.append(para.text)
            for table in document.tables:
                for row in table.rows:context.check();parts.append("\t".join(c.text for c in row.cells))
            text="\n".join(parts)
            if not text.strip():raise ValueError("文档没有可提取文字，不会生成空白成功结果")
            output=text.encode("utf-8")
        else:
            try:text=raw.decode("utf-8-sig")
            except UnicodeDecodeError:raise ValueError("输入必须为 UTF-8 文本") from None
            if b"\x00" in raw:raise ValueError("不支持二进制或 UTF-16 文本")
            if dst.suffix.lower()==".docx":
                from docx import Document
                document=Document()
                lines=text.splitlines()
                if len(lines)>10000:raise ValueError("文本超过一万行，请拆分后转换")
                for line in lines:context.check();document.add_paragraph(line)
                stream=io.BytesIO();document.save(stream);output=stream.getvalue()
            else:output=('<!doctype html><meta charset="utf-8"><title>'+html.escape(src.stem)+"</title><pre>"+html.escape(text)+"</pre>").encode("utf-8")
        if len(output)>10*1024**2:raise ValueError("转换输出超过 10 MB")
        context.progress(90,"再次核对源文件与输出路径")
        if self.preview(src,dst)!=current:raise ValueError("转换期间文件或授权变化，未保存")
        if not dst.parent.is_dir():raise ValueError("先选择已存在的输出文件夹")
        created=False
        try:
            with dst.open("xb") as handle:created=True;handle.write(output)
        except BaseException:
            if created:dst.unlink(missing_ok=True)
            raise
        return {"file":dst.name,"size":len(output),"source_kept":True,"format_note":"只转换文字，格式、图片、页眉脚注不保证保留；Markdown 不渲染"}
