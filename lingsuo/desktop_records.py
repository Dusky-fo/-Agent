"""Paged native records. Filtering and priority are performed by Tasks.query."""
import json
import time
import tkinter as tk
from tkinter import ttk
from .desktop_skin import C,FONT
from .desktop_ux import TASK_NAMES,task_sentence
from .tasks import STATE_NAMES


class RecordScreens:
    def _task_page(self):
        page=self.pages["tasks"]
        self.label(page,"查看真实结果；平台接受不等于手机收到。取消不能撤回已提交消息或已完成的文件变更。")
        self.record_offset=0;self.record_total=0;self.record_query={"attention":True}
        row=self.row(page)
        self.task_state=tk.StringVar(value="全部状态");self.task_order=tk.StringVar(value="优先需处理")
        self.task_kind=tk.StringVar(value="全部操作");self.task_period=tk.StringVar(value="全部日期");self.task_search=tk.StringVar()
        for variable,values in ((self.task_state,("全部状态","进行中","等待","失败","待核对","完成","平台接受")),(self.task_order,("优先需处理","全部按时间")),(self.task_kind,("全部操作",*TASK_NAMES.values())),(self.task_period,("全部日期","最近1天","最近7天","最近30天"))):
            combo=ttk.Combobox(row,textvariable=variable,values=values,state="readonly",width=13);combo.pack(side="left",padx=(0,6))
            combo.bind("<<ComboboxSelected>>",lambda _:self.guard_action("record_filter",self.filter_records)())
        row=self.row(page);ttk.Label(row,text="任务编号 / 文件名").pack(side="left",padx=(0,8))
        entry=ttk.Entry(row,textvariable=self.task_search);entry.pack(side="left",fill="x",expand=True);entry.bind("<Return>",lambda _:self.buttons["task_search"].invoke())
        self.button(row,"task_search","查找记录",self.filter_records).pack(side="left",padx=6)
        self.button(row,"setting_history","告警事件",self.history_dialog).pack(side="left")
        footer=self.row(page);footer.pack_configure(side="bottom")
        self.button(footer,"task_previous","上一页",lambda:self.page_records(-1)).pack(side="left")
        self.button(footer,"task_next","下一页",lambda:self.page_records(1)).pack(side="left",padx=6)
        self.task_page_note=tk.StringVar();ttk.Label(footer,textvariable=self.task_page_note).pack(side="left",padx=8)
        actions=self.row(page);actions.pack_configure(side="bottom")
        self.button(actions,"task_detail","查看结果",self.record_detail).pack(side="left",padx=(0,8))
        self.button(actions,"task_cancel","取消任务",lambda:self.app.tasks.cancel(self.selected(self.task_tree))).pack(side="left")
        self.button(actions,"task_received","我已实际收到",lambda:self.app.tasks.confirm_received(self.selected(self.task_tree))).pack(side="left",padx=8)
        self.task_tree=self.table(page,[("id","编号"),("kind","操作"),("result","结果 / 下一步")])
        self.task_tree.column("id",width=120,minwidth=100,stretch=False);self.task_tree.column("kind",width=130,minwidth=100,stretch=False);self.task_tree.column("result",width=500,minwidth=220)
        self.task_tree.empty_note.configure(text="没有符合条件的记录 · 可清空搜索或选择全部状态")

    def filter_records(self):
        if len(self.task_search.get().strip())>200:raise ValueError("任务搜索最长200个字符，请缩短关键词或填写任务编号。")
        states={"进行中":["running"],"等待":["queued"],"失败":["failed","timed_out","interrupted"],"待核对":["uncertain"],"完成":["completed"],"平台接受":["platform_accepted"]}
        days={"最近1天":1,"最近7天":7,"最近30天":30}.get(self.task_period.get())
        self.record_offset=0
        self.record_query={"states":states.get(self.task_state.get()),"kind":next((k for k,v in TASK_NAMES.items() if v==self.task_kind.get()),""),"text":self.task_search.get().strip(),"since":time.time()-days*86400 if days else None,"attention":self.task_order.get()=="优先需处理"}
        self.state_epoch+=1;self.next_snapshot=0

    def page_records(self,direction):
        offset=max(0,self.record_offset+direction*30)
        if direction>0 and offset>=self.record_total:return
        self.record_offset=offset;self.state_epoch+=1;self.next_snapshot=0

    def refresh_records(self,result):
        self.record_total=result["total"]
        if self.record_offset and self.record_offset>=self.record_total:
            self.record_offset=max(0,((self.record_total-1)//30)*30);self.next_snapshot=0;return
        self.fill(self.task_tree,[(r["id"],(r["id"],TASK_NAMES.get(r["kind"],"已移除功能的历史任务" if r["kind"]=="mouse.repeat" else r["kind"]),task_sentence(r))) for r in result["rows"]])
        self.task_page_note.set("第 %s 页 · 共 %s 条 · 每页30条"%(self.record_offset//30+1,self.record_total))

    def record_detail(self):
        task=self.app.tasks.get(self.selected(self.task_tree));_,body=self.dialog("任务结果 "+task["id"],width=800,height=540)
        self.label(body,"状态："+("你已确认收到" if task["received"] else STATE_NAMES.get(task["state"],task["state"]))+"\n"+task_sentence(task))
        result=json.loads(task["result"])
        labels={"file":"文件","size":"字节数","files":"文件数 / 搜索结果","recovery_id":"恢复编号","source_kept":"源文件保留","format_note":"格式说明","host":"网站","characters":"文字数","text":"网页文字","summary":"说明","clicks":"点击次数","stop_reason":"停止原因"}
        field=tk.Text(body,wrap="char",bg=C["card"],fg=C["txt"],font=(FONT,10));field.pack(fill="both",expand=True)
        field.insert("end","\n".join(labels[k]+"："+str(v) for k,v in result.items() if k in labels) or "没有额外结果。手机实收需本人核对。");field.configure(state="disabled")
