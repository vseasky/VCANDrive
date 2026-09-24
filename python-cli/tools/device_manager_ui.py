"""Small native Tk UI for discovery, identity display and USB mode switching."""
import argparse
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

if __package__:
    from . import device_manager as backend
else:
    import device_manager as backend

MODES={'VCAN':'vcan_usb','PCAN':'pcan','GS_CAN':'vkgs_usb'}
LABELS={v:k for k,v in MODES.items()}

class DeviceManagerUI:
    def __init__(self, root, api=backend):
        self.root,self.api=root,api
        self.events=queue.Queue()
        self.busy=False
        self.devices={}
        self.notices=[]
        self.status=tk.StringVar(value='正在识别设备…')
        self.target=tk.StringVar(value='PCAN')
        self.selection=tk.StringVar(value='请从上方列表选择设备')
        self.device_count=tk.StringVar(value='0 台设备')
        root.title('USB CAN 设备管理')
        root.geometry('940x460')
        root.minsize(760,440)
        root.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Treeview',rowheight=34,font=('Microsoft YaHei UI',10))
        style.configure('Treeview.Heading',font=('Microsoft YaHei UI',9),padding=(4,7))

        body=ttk.Frame(root,padding=18)
        body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1)
        body.rowconfigure(1,weight=1)

        top=ttk.Frame(body)
        top.grid(row=0,column=0,sticky='ew',pady=(0,14))
        title=ttk.Frame(top)
        title.pack(side='left')
        ttk.Label(title,text='设备管理',
                  font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(title,text='查看设备信息并切换工作模式',
                  font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(3,0))
        self.refresh_button=ttk.Button(top,text='刷新设备',command=self.refresh,width=12)
        self.refresh_button.pack(side='right',anchor='center')

        list_card=ttk.LabelFrame(body,text='已连接设备',padding=(10,8))
        list_card.grid(row=1,column=0,sticky='nsew')
        list_card.columnconfigure(0,weight=1)
        list_card.rowconfigure(1,weight=1)
        ttk.Label(list_card,textvariable=self.device_count,
                  font=('Microsoft YaHei UI',9)).grid(row=0,column=0,sticky='e',pady=(0,6))
        table=ttk.Frame(list_card)
        table.grid(row=1,column=0,sticky='nsew')
        self.tree=ttk.Treeview(table,columns=('mode','firmware','hardware','uid'),
                               show='headings',selectmode='browse',height=5)
        for key,label,width in [('mode','当前模式',120),('firmware','固件版本',120),
                                ('hardware','硬件版本',135),('uid','设备 UID',390)]:
            self.tree.heading(key,text=label,anchor='w')
            self.tree.column(key,width=width,minwidth=90,anchor='w',stretch=key=='uid')
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self.selected)

        actions=ttk.LabelFrame(body,text='模式切换',padding=(12,10))
        actions.grid(row=2,column=0,sticky='ew',pady=(14,0))
        actions.columnconfigure(0,weight=1)
        selected=ttk.Frame(actions)
        selected.grid(row=0,column=0,sticky='w',padx=(0,12))
        ttk.Label(selected,text='所选设备 UID',
                  font=('Microsoft YaHei UI',9)).pack(anchor='w')
        ttk.Label(selected,textvariable=self.selection,
                  font=('Microsoft YaHei UI',10)).pack(anchor='w',pady=(5,0))
        mode_controls=ttk.Frame(actions)
        mode_controls.grid(row=0,column=1,sticky='e')
        ttk.Label(mode_controls,text='目标模式',
                  font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(0,5))
        mode_row=ttk.Frame(mode_controls)
        mode_row.pack(anchor='e')
        self.mode_box=ttk.Combobox(mode_row,textvariable=self.target,values=list(MODES),
                                    state='readonly',width=12)
        self.mode_box.pack(side='left')
        self.mode_box.bind('<<ComboboxSelected>>',lambda _:self.controls())
        self.switch_button=ttk.Button(mode_row,text='切换模式',command=self.change_mode,width=12)
        self.switch_button.pack(side='left',padx=(10,0))

        self.status_label=ttk.Label(body,textvariable=self.status,
                                    anchor='w',justify='left',font=('Microsoft YaHei UI',9))
        self.status_label.grid(row=3,column=0,sticky='ew',pady=(11,0))
        root.bind('<Configure>',lambda e:self.status_label.configure(
            wraplength=max(300,root.winfo_width()-50)) if e.widget==root else None)
        root.after(80,self.poll)
        self.refresh()

    def current(self):
        rows=self.tree.selection()
        return self.devices.get(rows[0]) if rows else None

    def selected(self,_=None):
        d=self.current()
        self.selection.set(d['uid_hex'] if d else '请从上方列表选择设备')
        if d:self.target.set(LABELS[d['mode']])
        self.controls()

    def controls(self):
        d=self.current()
        self.refresh_button.configure(state='disabled' if self.busy else 'normal')
        self.mode_box.configure(state='disabled' if self.busy or not d else 'readonly')
        can_switch=not self.busy and d and MODES.get(self.target.get())!=d['mode']
        self.switch_button.configure(state='normal' if can_switch else 'disabled')

    def job(self,kind,fn):
        if self.busy:return
        self.busy=True;self.controls()
        def worker():
            try:self.events.put((kind,fn(),None))
            except Exception as e:self.events.put((kind,None,str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def refresh(self):
        self.status.set('正在识别设备…')
        self.job('scan',self.api.discover)

    def render(self,devices,preferred=None):
        current=self.current()
        preferred=preferred or (current['uid_hex'] if current else None)
        self.tree.delete(*self.tree.get_children())
        self.devices={}
        self.device_count.set(f'{len(devices)} 台设备')
        chosen=None
        for n,d in enumerate(devices):
            key=str(n);self.devices[key]=d
            self.tree.insert('', 'end',iid=key,
                             values=(LABELS[d['mode']],d['sw_version_full'],
                                     d['hw_version_hex'],d['uid_hex']))
            if d['uid_hex']==preferred:chosen=key
        if chosen is None and len(devices)==1:chosen='0'
        if chosen is not None:self.tree.selection_set(chosen);self.tree.focus(chosen)
        self.selected()

    def change_mode(self):
        d=self.current();target=MODES.get(self.target.get())
        if self.busy or not d or not target or target==d['mode']:return
        # Capture the exact selected device, never the first row after refresh.
        selected=dict(d)
        self.status.set(f"正在切换到 {LABELS[target]}，等待设备重新连接并核对身份…")
        def work():
            result=self.api.switch(selected,target)
            devices,notices=self.api.discover()
            return result,devices,notices
        self.job('switch',work)

    def poll(self):
        try:
            kind,value,error=self.events.get_nowait()
        except queue.Empty:pass
        else:
            self.busy=False
            if error:
                self.status.set('操作未完成：'+error+'。请刷新确认当前状态。')
                # Disable stale rows after an uncertain write result.
                if kind=='switch':self.render([])
            elif kind=='scan':
                devices,self.notices=value;self.render(devices)
                summary=f'已识别 {len(devices)} 台设备'
                if self.notices:summary+='；部分接口未识别或被占用：'+self.notices[0]
                self.status.set(summary)
            else:
                result,devices,self.notices=value
                self.render(devices,result['device']['uid_hex'])
                self.status.set('切换完成，设备模式、版本及 UID 已核验。')
            self.controls()
        self.root.after(80,self.poll)

    def close(self):
        if self.busy:
            self.status.set('正在执行设备操作，请等待结果后关闭窗口。')
            return
        self.root.destroy()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dll',type=Path,help='optional PCANBasic.dll path')
    args=p.parse_args()
    if args.dll:
        if not args.dll.is_file():p.error('DLL file not found')
        os.environ['PATH']=str(args.dll.resolve().parent)+os.pathsep+os.environ['PATH']
    root=tk.Tk();DeviceManagerUI(root);root.mainloop()

if __name__=='__main__':main()
