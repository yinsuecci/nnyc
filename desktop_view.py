"""Tk widget for a consented desktop session."""
import asyncio
import time
import tkinter as tk
from collections import deque

from PIL import ImageTk

from desktop_stream import KEYS


class DesktopView:
    def __init__(self, root, target, on_change=None):
        self.target = target
        self.verified = False
        self.on_change = on_change
        self.window = tk.Toplevel(root)
        self.window.title(f"nn远程 · {target}")
        self.window.geometry("920x620")
        self.status = tk.StringVar(value="等待被控端明确授权")
        tk.Label(self.window, textvariable=self.status, anchor="w").pack(fill="x")
        self.canvas = tk.Canvas(self.window, background="#141b29", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.item = self.canvas.create_image(0, 0)
        self.events = asyncio.Queue()
        self.geometry = None
        self.frames = deque()
        self.closed = asyncio.Event()
        self.pointer_last = 0
        self.allow_input = False
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas.bind("<Motion>", lambda e: self.pointer(e))
        self.canvas.bind("<FocusOut>", lambda _: self.emit(["release"]))
        self.canvas.bind("<KeyPress>", lambda e: self.key(e, True))
        self.canvas.bind("<KeyRelease>", lambda e: self.key(e, False))
        self.canvas.bind("<MouseWheel>", lambda e: self.wheel(1 if e.delta > 0 else -1))
        self.canvas.bind("<Button-4>", lambda _: self.wheel(1))
        self.canvas.bind("<Button-5>", lambda _: self.wheel(-1))
        for number, name in ((1, "left"), (2, "middle"), (3, "right")):
            self.canvas.bind(f"<ButtonPress-{number}>",
                             lambda e, n=name: self.button(e, n, True))
            self.canvas.bind(f"<ButtonRelease-{number}>",
                             lambda e, n=name: self.button(e, n, False))

    def connected(self, fps, allow_input):
        if type(fps) is not int or fps not in (30, 60) or type(allow_input) is not bool:
            raise ValueError("无效桌面设置")
        self.fps = fps
        self.allow_input = allow_input
        self.verified = True
        self.status.set("已授权，正在等待画面")
        if self.on_change:
            self.on_change()

    def show(self, image):
        if self.closed.is_set():
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        image.thumbnail((max(1, w), max(1, h)))
        self.geometry = ((w - image.width) // 2, (h - image.height) // 2,
                         image.width, image.height)
        self.photo = ImageTk.PhotoImage(image, master=self.window)
        self.canvas.itemconfigure(self.item, image=self.photo)
        self.canvas.coords(self.item, w // 2, h // 2)
        now = time.monotonic()
        self.frames.append(now)
        while self.frames[0] < now - 1:
            self.frames.popleft()
        mode = "可控制" if self.allow_input else "仅查看"
        self.status.set(f"{mode} · 目标 {self.fps} FPS · 最近一秒显示 {len(self.frames)} 帧")

    def emit(self, event):
        if not self.allow_input or self.closed.is_set():
            return
        if self.events.qsize() >= 96:
            self.close()
        else:
            self.events.put_nowait(event)

    def pointer(self, event, force=False):
        if self.geometry is None:
            return False
        now = time.monotonic()
        if not force and now - self.pointer_last < 1 / 90:
            return False
        x, y, w, h = self.geometry
        if not x <= event.x < x + w or not y <= event.y < y + h:
            return False
        self.pointer_last = now
        self.emit(["move", (event.x - x) * 65535 // max(w - 1, 1),
                   (event.y - y) * 65535 // max(h - 1, 1)])
        return True

    def button(self, event, name, pressed):
        inside = self.pointer(event, force=True)
        if pressed and inside:
            self.canvas.focus_set()
        if inside or not pressed:
            self.emit(["button", name, pressed])
        return "break"

    def key(self, event, pressed):
        name = event.keysym
        if len(name) == 1:
            name = name.lower()
        if name in KEYS:
            self.emit(["key", name, pressed])
        return "break"

    def wheel(self, direction):
        self.emit(["wheel", direction])
        return "break"

    def close(self):
        if not self.closed.is_set():
            self.closed.set()
            self.window.destroy()
