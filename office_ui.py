"""A-style device workspace. Presence and actions come from the live client."""
import tkinter as tk
import sys
from functools import lru_cache
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
import customtkinter as ctk
from PIL import Image

BLUE, INK, MUTED, LINE, RAIL = "#0759ed", "#141923", "#526173", "#dce2eb", "#f5f7fa"
FONT = {"win32": "Microsoft YaHei UI", "darwin": "PingFang SC"}.get(sys.platform, "Noto Sans CJK SC")


def face(widget, size, bold=False):
    root = widget.winfo_toplevel()
    if not hasattr(root, "_office_fonts"):
        root._office_fonts = {}
    key = (size, bold)
    if key not in root._office_fonts:
        root._office_fonts[key] = tkfont.Font(root=root, family=FONT,
            size=size, weight="bold" if bold else "normal")
    return root._office_fonts[key]


def label(parent, value="", size=10, bold=False, color=INK, **options):
    return tk.Label(parent,text=value,font=face(parent,size,bold),
                    bg=parent["bg"],fg=color,anchor="w",**options)


@lru_cache(maxsize=128)
def glyph(name, size, color):
    with Image.open(Path(__file__).with_name("ui_assets") / "fluent" / (name+".png")) as mask:
        artwork=Image.new("RGBA",mask.size,color)
        artwork.putalpha(mask)
    return ctk.CTkImage(light_image=artwork,dark_image=artwork,size=(size,size))


def button(parent, title, cmd, blue=False, icon=None):
    return ctk.CTkButton(parent,text=title,command=cmd,width=100,height=38,
                         font=(FONT,15,"bold"),corner_radius=8,
                         image=glyph(icon,20,"white" if blue else MUTED) if icon else None,
                         fg_color=BLUE if blue else "white",text_color="white" if blue else INK,
                         hover_color="#0648c2" if blue else "#eef4ff",
                         border_width=0 if blue else 1,border_color=LINE)


def monitor(parent, size=46, muted=False):
    fill="#eff1f5" if muted else "#e4eeff"
    ink="#818b99" if muted else BLUE
    return ctk.CTkLabel(parent,text="",width=size,height=size,corner_radius=10,
                        fg_color=fill,image=glyph("desktop",round(size*.63),ink))


class OfficeUI:
    def build_office(self):
        r=self.root
        ctk.set_appearance_mode("light")
        r.title("nn远程")
        r._office_app_icon=tk.PhotoImage(master=r,file=str(Path(__file__).with_name("ui_assets") / "nn-remote.png"))
        r.iconphoto(True,r._office_app_icon)
        r.geometry(f"{min(1320,int(r.winfo_screenwidth()*.9))}x{min(825,int(r.winfo_screenheight()*.82))}")
        r.minsize(550,470)
        r.protocol("WM_DELETE_WINDOW",self.close)
        r.configure(bg=LINE)
        style=ttk.Style(r)
        style.theme_use("clam")
        for key,bg,fg in (("Paper","white",INK),("Blue",BLUE,"white")):
            s=key+".TButton"
            style.configure(s,font=face(r,10,True),padding=(12,7),background=bg,foreground=fg,
                            bordercolor=LINE if key=="Paper" else BLUE,lightcolor=bg,darkcolor=bg)
            style.map(s,background=[("disabled","#e9ecf0"),("active","#e3efff" if key=="Paper" else "#0048c6")],
                      foreground=[("disabled","#737e8e")])
        self.peer_id=None
        self._signature=None
        self.history=[]
        self.search=tk.StringVar(master=r)
        self.transfer_name=tk.StringVar(master=r,value="暂无文件传输")
        self.transfer=tk.StringVar(master=r,value="选择在线设备后可发送文件")
        self.transfer_history=[]
        self.sending=False
        self.count=tk.StringVar(master=r)
        self.detail=tk.StringVar(master=r)
        self.id_text=tk.StringVar(master=r)
        self.badge=tk.StringVar(master=r)
        foot=tk.Frame(r,bg=RAIL,padx=14,pady=3)
        foot.pack(side="bottom",fill="x")
        label(foot,"nn远程",10,True,BLUE).pack(side="left")
        if self.config.get("mobile_desktop"):
            ttk.Button(foot, text="手机连接码", command=lambda: self.spawn(self.show_pair_code())).pack(side="left", padx=8)
        label(foot,size=9,color=MUTED,textvariable=self.status).pack(side="right")
        unit=r.winfo_fpixels("1i")/96
        px=lambda value: round(value*unit)
        rail=self.rail=tk.Frame(r,bg=RAIL,width=px(60),padx=px(6),pady=12)
        rail.pack(side="left",fill="y",padx=(0,1))
        rail.pack_propagate(False)
        self.nav_hint=tk.StringVar(master=r)
        label(foot,textvariable=self.nav_hint,size=9,color=MUTED).pack(side="left",padx=12)
        def nav(icon,title,cmd,first=False,bottom=False):
            w=ctk.CTkButton(rail,text="",width=44,height=44,corner_radius=9,
                            image=glyph(icon,24,BLUE if first else MUTED),
                            fg_color="#ddeaff" if first else RAIL,hover_color="#deebff",command=cmd)
            w.pack(fill="x",side="bottom" if bottom else "top",pady=3)
            for event in ("<Enter>","<FocusIn>"):
                w.bind(event,lambda _:self.nav_hint.set(title))
            for event in ("<Leave>","<FocusOut>"):
                w.bind(event,lambda _:self.nav_hint.set(""))
            return w
        nav("desktop","设备通讯录",lambda:self.open_pane(False),True)
        nav("people","远程协助",lambda:self.open_pane(True))
        nav("folder_arrow_right","文件传输",self.choose_file)
        nav("history","连接记录",self.show_history)
        nav("settings","设置",self.show_settings)
        self.term_button=nav("code","手机终端开关",lambda:self.spawn(self.toggle_terminal()),bottom=True)
        center=self.center=tk.Frame(r,bg="white",width=px(280),padx=14,pady=20)
        center.pack_propagate(False)
        right=self.right=tk.Frame(r,bg="white")
        pc=tk.Canvas(right,bg="white",highlightthickness=0)
        ps=ctk.CTkScrollbar(right,command=pc.yview,width=10,fg_color="white",
                            button_color="#d7e0ec",button_hover_color="#bac9de")
        pc.pack(side="left",fill="both",expand=True)
        def scroll_range(lo,hi):
            if float(lo)>0 or float(hi)<.9999:
                ps.pack(side="right",fill="y")
            else:
                ps.pack_forget()
            ps.set(lo,hi)
        pc.configure(yscrollcommand=scroll_range)
        p=tk.Frame(pc,bg="white",padx=px(22),pady=px(24))
        pw=pc.create_window(0,0,anchor="nw",window=p)
        def fit_detail(_=None):
            height=p.winfo_reqheight()
            pc.itemconfigure(pw,width=min(pc.winfo_width(),px(940)),height=height)
            pc.configure(scrollregion=(0,0,pc.winfo_width(),height))
        pc.bind("<Configure>",fit_detail)
        p.bind("<Configure>",fit_detail)
        identity=tk.Frame(p,bg="white")
        identity.pack(fill="x",pady=(0,20))
        self.view_button=button(identity,"连接远程桌面",lambda:self.spawn(self.start_view()),True,"desktop")
        identity.columnconfigure(1,weight=1)
        monitor(identity,68).grid(row=0,column=0,rowspan=2,padx=(0,px(14)))
        names=tk.Frame(identity,bg="white")
        names.grid(row=0,column=1,sticky="sw")
        self.live=label(names,textvariable=self.badge,size=9)
        names.columnconfigure(0,weight=1)
        label(names,size=17,bold=True,textvariable=self.detail).grid(row=0,column=0,sticky="w")
        self.live.grid(row=0,column=1,padx=6)
        idbox=tk.Frame(identity,bg="white")
        idbox.grid(row=1,column=1,sticky="nw")
        label(idbox,"设备 ID",color=MUTED).pack(side="left")
        label(idbox,textvariable=self.id_text).pack(side="left",padx=8)
        self.copy_button=ctk.CTkButton(idbox,text="",width=30,height=30,image=glyph("copy",18,MUTED),
                                      fg_color="white",hover_color="#eef4ff",command=self.copy_id)
        self.copy_button.pack(side="left")
        def header_fit(event):
            if event.width < px(480):
                self.view_button.grid(row=2,column=0,columnspan=2,rowspan=1,sticky="w",pady=(8,0))
            else:
                self.view_button.grid(row=0,column=2,columnspan=1,rowspan=2,sticky="e",pady=0)
        identity.bind("<Configure>",header_fit)
        tk.Frame(p,bg=LINE,height=1).pack(fill="x")
        trust=tk.Frame(p,bg="#f7f9fd",padx=10,pady=9)
        trust.pack(fill="x")
        ctk.CTkLabel(trust,text="",image=glyph("lock_closed",20,MUTED)).pack(side="left")
        self.security_state=tk.StringVar(master=r)
        label(trust,textvariable=self.security_state,color=MUTED).pack(side="left",padx=8)
        tk.Frame(trust,bg=LINE,width=1,height=16).pack(side="left",padx=4)
        label(trust,"连接需经对端授权",color=MUTED).pack(side="left",padx=8)
        def section(title,icon,link=None):
            tk.Frame(p,bg=LINE,height=1).pack(fill="x")
            row=tk.Frame(p,bg="white")
            row.pack(fill="x",pady=(px(18),px(8)))
            ctk.CTkLabel(row,text="",image=glyph(icon,24,MUTED)).pack(side="left")
            label(row,title,14,True).pack(side="left",padx=12)
            if link:
                ctk.CTkButton(row,text=link[0],command=link[1],width=88,height=28,
                              text_color=BLUE,fg_color="white",hover_color="#eef4ff").pack(side="right")
            body=tk.Frame(p,bg="white")
            body.pack(fill="x",padx=(px(36),0),pady=(0,px(18)))
            return body
        assist=section("远程协助","people")
        label(assist,"邀请对方协助本机，或发起对对方电脑的连接请求。",color=MUTED).pack(fill="x")
        actions=tk.Frame(assist,bg="white")
        actions.pack(fill="x",pady=8)
        self.help_button=button(actions,"请求对方协助",lambda:self.spawn(self.invite_helper()),icon="people")
        self.help_button.configure(border_color=BLUE,text_color=BLUE)
        self.help_button.pack(side="left")
        self.offer_button=button(actions,"协助对方电脑",lambda:self.spawn(self.start_view()),icon="desktop")
        self.offer_button.pack(side="left",padx=(8,0))
        metadata=section("连接信息","shield_checkmark")
        self.session_state=tk.StringVar(master=r)
        for i,(key,value) in enumerate((("连接方式","远程桌面"),("加密保护",self.security_state),("连接状态",self.session_state))):
            label(metadata,key,color=MUTED).grid(row=i,column=0,sticky="w",padx=(0,px(38)),pady=3)
            field=label(metadata,value) if isinstance(value,str) else label(metadata,textvariable=value)
            field.grid(row=i,column=1,sticky="w")
        files=section("文件传输","arrow_swap",("查看传输记录 ›",self.show_transfer_history))
        surface=ctk.CTkFrame(files,corner_radius=12,fg_color="#f8faff",border_color=LINE,border_width=1)
        surface.pack(fill="x")
        strip=tk.Frame(surface,bg="#f8faff")
        strip.pack(fill="x",padx=12,pady=10)
        ctk.CTkLabel(strip,text="",width=36,height=40,image=glyph("folder_arrow_right",24,BLUE),
                     fg_color="#e4eeff",corner_radius=7).pack(side="left",padx=(0,10))
        self.file_button=button(strip,"传输文件",self.choose_file)
        self.file_button.pack(side="right",padx=(10,0))
        label(strip,size=11,bold=True,textvariable=self.transfer_name).pack(fill="x")
        label(strip,size=9,color=MUTED,textvariable=self.transfer).pack(fill="x")
        self.progress=ctk.CTkProgressBar(strip,height=6,corner_radius=3,fg_color="#dee7f5",
                                        progress_color=BLUE,border_width=0)
        self.progress.set(0)
        self.progress.pack(fill="x",pady=5)
        label(center,"设备通讯录",16,True).pack(anchor="w")
        search_box=ctk.CTkFrame(center,height=38,fg_color="white",corner_radius=8,
                                border_width=1,border_color=LINE)
        search_box.pack(fill="x",pady=(14,0))
        glass=ctk.CTkLabel(search_box,text="",width=22,image=glyph("search",18,MUTED))
        glass.pack(side="left",padx=(8,2))
        self.search_entry=ctk.CTkEntry(search_box,textvariable=self.search,height=32,border_width=0,
                                      fg_color="white",font=(FONT,14))
        self.search_entry.pack(side="left",fill="x",expand=True,padx=(0,4),pady=3)
        hint=tk.Label(self.search_entry,text="搜索设备（名称或设备 ID）",font=face(r,9),
                      fg=MUTED,bg="white",anchor="w",bd=0)
        def search_hint(*_):
            if self.search.get() or r.focus_get() in self.search_entry.winfo_children():
                hint.place_forget()
            else:
                hint.place(x=5,rely=.5,anchor="w")
        for w in (hint,glass):
            w.bind("<Button-1>",lambda _:self.search_entry.focus_set())
        self.search_entry.bind("<FocusIn>",lambda _:hint.place_forget())
        self.search_entry.bind("<FocusOut>",lambda _:r.after_idle(search_hint))
        self.search.trace_add("write",search_hint)
        search_hint()
        label(center,textvariable=self.count,bold=True).pack(fill="x",pady=(14,8))
        self.canvas=tk.Canvas(center,bg="white",highlightthickness=0)
        self.canvas.pack(side="left",fill="both",expand=True)
        scroll=ttk.Scrollbar(center,command=self.canvas.yview)
        def scroll_contacts(lo,hi):
            if float(lo)>0 or float(hi)<.9999:
                scroll.pack(side="right",fill="y")
            else:
                scroll.pack_forget()
            scroll.set(lo,hi)
        self.canvas.configure(yscrollcommand=scroll_contacts)
        self.cards=tk.Frame(self.canvas,bg="white")
        win=self.canvas.create_window(0,0,anchor="nw",window=self.cards)
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(win,width=e.width))
        self.cards.bind("<Configure>",lambda _:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.search.trace_add("write",lambda *_:self.render())
        self.compact=None
        self.detail_open=False
        def resize(event):
            if event.widget != r:
                return
            # Hysteresis keeps the panes stable near the single-column breakpoint.
            compact=event.width < px(800 if self.compact else 770)
            if compact!=self.compact:
                self.compact=compact
                self.arrange_panes()
        r.bind("<F11>",lambda _:r.attributes("-fullscreen",not r.attributes("-fullscreen")))
        r.bind("<Escape>",lambda _:r.attributes("-fullscreen",False))
        r.bind("<Configure>",resize,add="+")
        self.render()

    def arrange_panes(self):
        self.center.pack_forget()
        self.right.pack_forget()
        if not self.compact:
            self.center.pack(side="left",fill="y",padx=(0,1))
            self.right.pack(fill="both",expand=True)
        else:
            (self.right if self.detail_open else self.center).pack(fill="both",expand=True)

    def open_pane(self, detail):
        self.detail_open=detail
        self.arrange_panes()
        (self.help_button if detail else self.search_entry).focus_set()

    def selected(self):
        return self.peer_id

    def select_peer(self,key):
        if key in self.card_ids:
            self.peer_id=key
            self.render()

    def copy_id(self):
        if self.peer_id:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.peer_id)

    async def invite_helper(self):
        key=self.selected()
        if self.devices.get(key) is True and sys.platform=="win32":
            await self.client.call("request",target=key,purpose="help")
            self.status.set(f"已邀请 {key} 协助本机，桌面尚未共享")

    def show_history(self):
        self.show_log("本次连接记录",self.history,"暂无连接请求")

    def show_transfer_history(self):
        self.show_log("本次传输记录",self.transfer_history,"暂无文件传输记录")

    def record_transfer(self,message):
        from time import strftime
        self.transfer_history.append(f"{strftime('%H:%M:%S')}   {message}")
        del self.transfer_history[:-100]

    def show_log(self,title,entries,empty):
        dialog=tk.Toplevel(self.root,bg="white")
        dialog.title("nn远程 · "+title)
        log=tk.Text(dialog,width=44,height=12,font=(FONT,10),relief="flat")
        log.pack(padx=18,pady=14)
        log.insert("1.0","\n".join(reversed(entries)) or empty)
        log.configure(state="disabled")

    def selection(self):
        self.render()

    def render(self):
        needle=self.search.get().strip().casefold()
        names=self.config.get("device_names",{})
        rows=[(k,self.devices.get(k),str(names.get(k,k))) for k in self.config["peers"]
              if needle in k.casefold() or needle in str(names.get(k,k)).casefold()]
        self.card_ids={row[0] for row in rows}
        if self.peer_id not in self.card_ids:
            self.peer_id=rows[0][0] if rows else None
        selected_view=self.view if self.view is not None and self.view.target==self.peer_id else None
        verified=selected_view is not None and selected_view.verified and not selected_view.closed.is_set()
        session=("已连接 · 可操控" if selected_view.allow_input else "已连接 · 仅查看") if verified else (
            "等待对端授权或连接" if selected_view and not selected_view.closed.is_set() else "未连接")
        sig=(tuple(rows),self.peer_id,self.view is None,self.sending,session)
        if sig==self._signature:
            return
        self._signature=sig
        self.count.set(f"匹配设备  ({len(rows)})" if needle else f"我的设备  ({len(rows)})")
        self.session_state.set(session)
        self.security_state.set("端到端加密（E2E）" if verified else "未建立加密连接")
        def state(value):
            return "在线" if value is True else "离线" if value is False else "待确认"
        k=self.peer_id
        online=self.devices.get(k)
        self.detail.set(names.get(k,k) if k else "未选择设备")
        self.id_text.set(k or "未选择")
        self.badge.set("●  "+state(online) if k else "")
        self.live.configure(fg="#177346" if online else MUTED)
        self.view_button.configure(state="normal" if online and self.view is None else "disabled")
        self.offer_button.configure(state=self.view_button.cget("state"))
        self.help_button.configure(state="normal" if online and sys.platform=="win32" else "disabled")
        self.file_button.configure(state="normal" if online and not self.sending else "disabled")
        self.copy_button.configure(state="normal" if k else "disabled")
        for child in self.cards.winfo_children():
            child.destroy()
        if not rows:
            label(self.cards,"没有匹配的设备" if needle else "暂无配对设备",13,color=MUTED).pack(pady=40)
        for device,online,name in rows:
            bg="#e8f1ff" if k==device else "white"
            shell=ctk.CTkFrame(self.cards,fg_color=bg,corner_radius=9,border_width=0)
            shell.pack(fill="x",pady=(0,4))
            card=tk.Frame(shell,bg=bg)
            card.pack(fill="x",padx=10,pady=11)
            icon=monitor(card,muted=online is False)
            icon.pack(side="left",padx=(0,10))
            info=tk.Frame(card,bg=bg)
            info.pack(fill="both",expand=True)
            title=tk.Frame(info,bg=bg)
            title.pack(fill="x")
            heading=label(title,name,13,True)
            heading.pack(fill="x")
            pill=label(info,"●  "+state(online),9,color="#157744" if online else MUTED)
            pill.pack(fill="x")
            for widget in (shell,card,icon,info,title,heading,pill):
                def select(_,key=device):
                    self.select_peer(key)
                    if self.compact:
                        self.open_pane(True)
                widget.bind("<Button-1>",select)
                widget.bind("<MouseWheel>",lambda e:self.canvas.yview_scroll(-int(e.delta/120),"units"))
