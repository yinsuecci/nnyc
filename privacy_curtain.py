"""Attended, single-monitor Windows privacy curtain. Never starts at login."""
import asyncio
import ctypes
from ctypes import wintypes as wt
import multiprocessing as mp
import secrets


class Unsupported(RuntimeError):
    pass


def _checked(value):
    if not value:
        raise Unsupported("Windows 未确认遮屏条件")
    return value


def _native():
    user = ctypes.WinDLL("user32", use_last_error=True)
    def bind(name, result, *args):
        func = getattr(user,name)
        func.restype, func.argtypes = result, args
        return func
    bind("SetThreadDpiAwarenessContext", wt.HANDLE, wt.HANDLE)
    bind("GetAncestor", wt.HWND, wt.HWND, wt.UINT)
    bind("GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(wt.RECT))
    bind("IsWindowVisible", wt.BOOL, wt.HWND)
    bind("GetWindowLongW", wt.LONG, wt.HWND, ctypes.c_int)
    bind("SetWindowLongW", wt.LONG, wt.HWND, ctypes.c_int, wt.LONG)
    bind("SetLayeredWindowAttributes", wt.BOOL, wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD)
    bind("SetWindowPos", wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, ctypes.c_int, wt.UINT)
    bind("SetWindowDisplayAffinity", wt.BOOL, wt.HWND, wt.DWORD)
    bind("GetWindowDisplayAffinity", wt.BOOL, wt.HWND, ctypes.POINTER(wt.DWORD))
    bind("GetTopWindow", wt.HWND, wt.HWND)
    bind("GetWindow", wt.HWND, wt.HWND, wt.UINT)
    bind("CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
         wt.HINSTANCE, wt.LPVOID)
    bind("DestroyWindow", wt.BOOL, wt.HWND)
    bind("RegisterHotKey", wt.BOOL, wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
    bind("UnregisterHotKey", wt.BOOL, wt.HWND, ctypes.c_int)
    bind("PeekMessageW", wt.BOOL, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
    bind("TranslateMessage", wt.BOOL, ctypes.POINTER(wt.MSG))
    bind("DispatchMessageW", ctypes.c_ssize_t, ctypes.POINTER(wt.MSG))
    bind("OpenInputDesktop", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
    bind("CloseDesktop", wt.BOOL, wt.HANDLE)
    bind("GetUserObjectInformationW", wt.BOOL, wt.HANDLE, ctypes.c_int, wt.LPVOID,
         wt.DWORD, ctypes.POINTER(wt.DWORD))
    return user


def _geometry(user):
    if user.GetSystemMetrics(80) != 1:
        raise Unsupported("目前仅支持单显示器")
    size = user.GetSystemMetrics(0), user.GetSystemMetrics(1)
    if min(size) < 100 or size[0] * size[1] > 17000000:
        raise Unsupported("无法验证当前显示范围")
    return size


def _normal_desktop(user):
    desk = _checked(user.OpenInputDesktop(0, False, 1))
    try:
        name, needed = ctypes.create_unicode_buffer(128), wt.DWORD()
        _checked(user.GetUserObjectInformationW(desk, 2, name, ctypes.sizeof(name), ctypes.byref(needed)))
        if name.value.lower() != "default":
            raise Unsupported("当前不是普通交互桌面")
    finally:
        user.CloseDesktop(desk)


def _rect(user, hwnd):
    r = wt.RECT()
    _checked(user.GetWindowRect(hwnd,ctypes.byref(r)))
    return r.left,r.top,r.right,r.bottom


def _cloaked(hwnd):
    flag = wt.DWORD()
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wt.HWND(hwnd),14,ctypes.byref(flag),ctypes.sizeof(flag))
    return hr == 0 and bool(flag.value)


def _coverage(user, hwnd, size, bounds, stop_button=None):
    _normal_desktop(user)
    if (_geometry(user) != size or not user.IsWindowVisible(hwnd) or _cloaked(hwnd)
            or _rect(user,hwnd) != bounds):
        raise Unsupported("显示覆盖已改变")
    other = user.GetTopWindow(None)
    for _ in range(128):
        if other == hwnd:
            break
        if not other:
            raise Unsupported("无法确认窗口顺序")
        if other != stop_button and user.IsWindowVisible(other) and not _cloaked(other):
            l,t,r,b = _rect(user,other)
            if min(bounds[2],r)>max(bounds[0],l) and min(bounds[3],b)>max(bounds[1],t):
                raise Unsupported("隐私图上方出现其他窗口")
        other = user.GetWindow(other,2)
    else:
        raise Unsupported("无法确认窗口顺序")
    affinity = wt.DWORD()
    _checked(user.GetWindowDisplayAffinity(hwnd,ctypes.byref(affinity)))
    if affinity.value != 0x11:
        raise Unsupported("远端采集隔离已失效")


def _worker(pipe, owner, probe_only=False):
    import tkinter as tk
    from PIL import ImageGrab, ImageTk
    from privacy_artwork import load, render
    from privacy_editor import personal_artwork
    root = user = button = None
    hotkey = False
    try:
        user = _native()
        _checked(user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)))
        size = _geometry(user)
        bounds=(0,0,240,150) if probe_only else (0,0,*size)
        _normal_desktop(user)
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.geometry(f"{bounds[2]}x{bounds[3]}+0+0")
        image, frame = (None, ("fit",.5,.5,1)) if probe_only else load(personal_artwork())
        rendered = render(image,bounds[2:],*frame)
        photo = ImageTk.PhotoImage(rendered,master=root)
        canvas = tk.Canvas(root,highlightthickness=0,borderwidth=0)
        canvas.pack(fill="both",expand=True)
        canvas.create_image(0,0,anchor="nw",image=photo)
        canvas.create_rectangle(0,0,size[0],72,fill="#172134",outline="")
        canvas.create_text(18,35,anchor="w",fill="white",font=("Microsoft YaHei",16),
            text=f"nn远程 · 正在与 {owner[:64]} 共享桌面     Ctrl + Alt + F10 停止共享")
        # A random two-colour underlay proves that the actual GDI capture path
        # sees content behind the curtain, not black, an old frame or our wallpaper.
        marker = tk.Toplevel(root)
        marker.overrideredirect(True)
        marker.geometry("64x32+16+92")
        marker.attributes("-topmost",True)
        colors=[tuple(secrets.token_bytes(3)) for _ in range(2)]
        patch=tk.Canvas(marker,width=64,height=32,highlightthickness=0,borderwidth=0)
        patch.pack()
        for i,c in enumerate(colors):
            patch.create_rectangle(i*32,0,(i+1)*32,32,fill="#%02x%02x%02x"%c,outline="")
        root.update()
        root.deiconify()
        root.update()
        hwnd = _checked(user.GetAncestor(root.winfo_id(),2))
        hotkey = bool(user.RegisterHotKey(hwnd,0x411,0x4003,0x79))
        if not hotkey:
            raise Unsupported("无法注册 Ctrl+Alt+F10")
        style = user.GetWindowLongW(hwnd,-20)
        user.SetWindowLongW(hwnd,-20,style | 0x80000 | 0x20 | 0x08000000)
        _checked(user.SetLayeredWindowAttributes(hwnd,0,255,2))
        _checked(user.SetWindowPos(hwnd,ctypes.c_void_p(-1),0,0,*bounds[2:],0x0030))
        ctypes.WinDLL("dwmapi").DwmFlush()
        physical = ImageGrab.grab(bbox=(16,92,80,124),include_layered_windows=True)
        if any(physical.getpixel((x,16))[:3] != rendered.getpixel((16+x,108))
               for x in (16,48)):
            raise Unsupported("无法验证上层隐私图")
        _checked(user.SetWindowDisplayAffinity(hwnd,0x11))
        if not user.GetWindowLongW(hwnd,-20) & 0x20:
            raise Unsupported("无法保留桌面输入")
        ctypes.WinDLL("dwmapi").DwmFlush()
        _coverage(user,hwnd,size,bounds)
        snapshot=ImageGrab.grab(bbox=(16,92,80,124),include_layered_windows=False)
        if [snapshot.getpixel((x,16))[:3] for x in (16,48)] != colors:
            raise Unsupported("采集结果未通过隔离检查")
        marker.destroy()
        if probe_only is True:
            pipe.send("probe_passed")
            return
        # Keep a real, clickable local stop control independent of the remote
        # input path. It intentionally remains visible in captures as well.
        button = _checked(user.CreateWindowExW(0x08000088, "BUTTON", "停止共享",
            0x90000000, bounds[2]-126, 12, 116, 44, None, None, None, None))
        _coverage(user,hwnd,size,bounds,button)
        pipe.send("covered")
        msg=wt.MSG()
        while True:
            ping = pipe.poll(.05)
            if ping and pipe.recv() != "check":
                break
            while user.PeekMessageW(ctypes.byref(msg),None,0,0,1):
                if (msg.message == 0x312 and msg.wParam == 0x411 or
                        msg.hWnd == button and msg.message in (0x202,0xF5)):
                    pipe.send("stop")
                    pipe.poll(2)
                    return
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
            _coverage(user,hwnd,size,bounds,button)
            root.update_idletasks()
            if ping:
                pipe.send("covered")
    except (BrokenPipeError, EOFError):
        pass
    except Exception as exc:
        try:
            pipe.send(str(exc) if isinstance(exc,Unsupported) else "无法验证隐私屏")
        except OSError:
            pass
    finally:
        if button:
            user.DestroyWindow(button)
        if hotkey:
            user.UnregisterHotKey(hwnd,0x411)
        if root is not None:
            root.destroy()
        pipe.close()


class Curtain:
    def __init__(self, owner):
        ctx=mp.get_context("spawn")
        self.pipe, child=ctx.Pipe()
        self.process=ctx.Process(target=_worker,args=(child,owner))
        try:
            self.process.start()
        except BaseException:
            self.pipe.close()
            raise
        finally:
            child.close()

    async def wait(self):
        while not self.pipe.poll():
            if not self.process.is_alive():
                raise Unsupported("隐私屏进程已退出")
            await asyncio.sleep(.04)
        try:
            return self.pipe.recv()
        except EOFError as error:
            raise Unsupported("隐私屏进程已断开") from error

    async def check(self):
        self.pipe.send("check")
        return await asyncio.wait_for(self.wait(),1.5)

    async def close(self):
        self.pipe.close()
        await asyncio.to_thread(self.process.join,2)
        if self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(self.process.join,2)
        if not self.process.is_alive():
            self.process.close()
