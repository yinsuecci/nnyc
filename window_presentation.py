"""Coordinate DWM frame changes and Tk child repainting on Windows."""
import ctypes as c
from ctypes import wintypes as w
import tkinter as tk


class Presentation:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.key_down = False
        self.window_state = ""
        self.job: str | None = None
        self.user = c.WinDLL("user32")
        self.user.GetParent.argtypes, self.user.GetParent.restype = [w.HWND], w.HWND
        self.user.RedrawWindow.argtypes = [w.HWND, c.c_void_p, w.HANDLE, w.UINT]
        self.job = root.after_idle(self.repaint)
        root.bind("<Map>", self.on_map, add="+")
        root.bind("<Configure>", self.on_configure, add="+")
        root.bind("<KeyRelease-F11>", self.release, add="+")
        root.bind("<FocusOut>", self.release_focus, add="+")

    def release_focus(self, event: tk.Event) -> None:
        if event.widget == self.root:
            self.release()

    def release(self, event: tk.Event | None = None) -> None:
        self.key_down = False

    def on_map(self, event: tk.Event) -> None:
        if event.widget == self.root:
            self.request_repaint()

    def on_configure(self, event: tk.Event) -> None:
        if event.widget == self.root:
            state = self.root.state()
            if state != self.window_state:
                self.window_state = state
                self.request_repaint()

    def request_repaint(self) -> None:
        if self.job is None:
            self.job = self.root.after_idle(self.repaint)

    def repaint(self) -> None:
        self.job = None
        hwnd = self.user.GetParent(self.root.winfo_id())
        # Resolve the current wrapper, not the pre-fullscreen HWND. Keep DWM
        # animations enabled so the compositor can retain its previous frame.
        self.user.RedrawWindow(hwnd, None, None, 0x0581)

    def toggle(self) -> None:
        if self.key_down:
            return
        self.key_down = True
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))
        self.request_repaint()

    def restore(self) -> None:
        self.release()
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
            self.request_repaint()
