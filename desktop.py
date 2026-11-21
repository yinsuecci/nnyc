"""nn远程 A layout and authenticated peer controls."""
import argparse
import asyncio
import json
import os
import ssl
import sys
import tkinter as tk
from contextlib import AsyncExitStack
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

from aiohttp import ClientResponseError, web

from desktop_stream import WindowsDesktop, host_desktop, race, view_desktop
from desktop_view import DesktopView
from file_transfer import FileReceiver, send_file
from peer import Peer
from terminal_portal import Portal

BLUE, INK, FOG, BORDER = "#2855d9", "#20263a", "#f5f7fb", "#e1e7f0"


from office_ui import FONT, OfficeUI
from desktop_profile import read_profile, device_token


class Desktop(OfficeUI):
    def __init__(self, root, config, token):
        self.root, self.config, self.token = root, config, token
        self.alive = True
        self.devices = {}
        self.prompts = {}
        self.answered = set()
        self.host_id = None
        self.host_task = None
        self.view = None
        self.settings_path = Path(config["settings_path"])
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            fps = settings.get("fps", 30)
        except (OSError, ValueError, AttributeError):
            fps = 30
        self.fps = fps if type(fps) is int and fps in (30, 60) else 30
        self.jobs = set()
        self.client = None
        self.portal_runner = None
        self.status = tk.StringVar(value="未连接信令服务")
        self.build_office()

    @staticmethod
    def label(parent, text, size, bold=False, bg="white", variable=None):
        return tk.Label(parent, text=text, textvariable=variable, anchor="w", bg=bg, fg=INK,
                        font=(FONT, size, "bold" if bold else "normal"))

    def show_settings(self):
        host = urlsplit(self.config["signaling"]).hostname
        dialog = tk.Toplevel(self.root)
        dialog.title("连接设置")
        self.label(dialog, f"信令服务器：{host}", 12).pack(padx=16, pady=10, anchor="w")
        self.label(dialog, "被控端画面目标帧率", 12).pack(padx=16, anchor="w")
        value = tk.IntVar(value=self.fps)
        for fps in (30, 60):
            ttk.Radiobutton(dialog, text=f"{fps} FPS", value=fps, variable=value).pack(
                padx=16, anchor="w",
            )

        def save():
            try:
                temporary = self.settings_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"fps": value.get()}), encoding="utf-8")
                temporary.replace(self.settings_path)
            except OSError:
                self.status.set("无法保存帧率设置")
            else:
                self.fps = value.get()
                dialog.destroy()

        ttk.Button(dialog, text="保存", command=save).pack(padx=16, pady=10, anchor="w")
        from privacy_editor import open_editor, personal_artwork
        personal = personal_artwork()
        ttk.Button(dialog, text="编辑本机隐私屏图片", command=lambda: open_editor(dialog, personal)).pack(
            padx=16, pady=(0, 10), anchor="w")

    def close(self):
        self.alive = False

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        task.add_done_callback(self.job_done)

    def job_done(self, task):
        self.jobs.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None and self.alive:
            self.status.set(f"操作失败 · {type(error).__name__}")

    def choose_file(self):
        if self.sending:
            self.status.set("已有文件正在发送")
            return
        target = self.selected()
        if self.devices.get(target) is not True or self.client is None:
            self.status.set("请选择一台在线设备")
            return
        path = filedialog.askopenfilename(parent=self.root, title="选择要发送的文件")
        if path:
            self.spawn(self.send(target, path))

    async def send(self, target, path):
        if self.sending:
            return
        path = Path(path)
        self.sending = True
        self.render()
        self.transfer_name.set(path.name)
        self.transfer.set("等待对端同意接收")
        self.progress.set(0)
        sid = None
        outcome = "发送未完成"
        try:
            sid = (await self.client.call("request", target=target, purpose="file"))["id"]
            deadline = asyncio.get_running_loop().time() + 60
            def progress(sent, total):
                ratio = sent / total if total else 0
                self.progress.set(ratio)
                state = "等待完整性确认" if sent == total else "正在传输"
                self.transfer.set(f"{sent/1048576:.1f} / {total/1048576:.1f} MiB（{ratio:.0%}） · {state}")
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await send_file(self.client, target, sid, path, progress)
                    self.progress.set(1)
                    outcome = "已发送 · 对端已确认"
                    return
                except ClientResponseError as error:
                    if error.status != 409:
                        raise
                    await asyncio.sleep(1)
            outcome = "接收授权已过期"
        except asyncio.CancelledError:
            outcome = "发送已中断"
            raise
        except Exception as error:
            outcome = f"发送失败 · {type(error).__name__}"
            raise
        finally:
            try:
                if sid:
                    await self.cancel_invite(sid)
            finally:
                self.sending = False
                self.transfer.set(outcome)
                self.status.set(f"{path.name} · {outcome}")
                self.record_transfer(f"{path.name} → {target} · {outcome}")
                self.render()

    async def start_view(self, target=None, sid=None):
        target = self.selected() if target is None else target
        if target is None or self.view is not None or self.devices.get(target) is not True:
            if sid:
                await self.cancel_invite(sid)
            return
        view = self.view = DesktopView(self.root, target, self.selection)
        from time import strftime
        self.history.append(f"{strftime('%H:%M:%S')}   向 {target} 请求连接")
        self.history = self.history[-100:]
        self.selection()
        try:
            if not sid:
                sid = (await self.client.call("request", target=target, purpose="desktop"))["id"]

            async def connect():
                async with asyncio.timeout(60):
                    while True:
                        try:
                            return await self.client.call("endpoint", id=sid)
                        except ClientResponseError as error:
                            if error.status != 409:
                                raise
                        await asyncio.sleep(1)

            await race(view.closed.wait(), connect())
            if not view.closed.is_set():
                await race(view.closed.wait(), view_desktop(self.client, target, sid, view))
        except asyncio.IncompleteReadError as error:
            if not view.verified or error.partial:
                raise
            self.status.set("桌面连接已断开")
        except ClientResponseError as error:
            if error.status == 422:
                self.status.set("对端当前不支持桌面共享")
            else:
                raise
        finally:
            view.close()
            self.view = None
            self.selection()
            if sid:
                await self.cancel_invite(sid)

    async def cancel_invite(self, sid):
        try:
            await self.client.call("cancel", id=sid)
        except Exception:
            pass

    async def share_desktop(self, sid, source, enable_input, transport="tcp", private=False):
        if self.host_id is not None:
            self.status.set("已有桌面会话")
            return
        self.host_id = sid
        try:
            backend = WindowsDesktop()
        except BaseException:
            self.host_id = None
            raise
        started = asyncio.Event()
        dialog = tk.Toplevel(self.root)
        dialog.title("nn远程 · 桌面共享")
        dialog.attributes("-topmost", True)
        self.label(dialog, f"正在向 {source} 共享桌面", 13, True).pack(padx=16, pady=8)
        self.label(dialog, "操控已授权" if enable_input else "仅查看", 11).pack(padx=16)

        def stop():
            started.set()
            if self.host_task:
                self.host_task.cancel()

        ttk.Button(dialog, text="停止共享", command=stop).pack(padx=16, pady=10)
        dialog.protocol("WM_DELETE_WINDOW", stop)
        audio_task = None
        audio_ready = asyncio.Event()
        curtain = curtain_task = None
        privacy_ready = False
        async def mute_for_session():
            from privacy_audio import AudioGuard
            try:
                guard = AudioGuard()
            except OSError:
                self.status.set("无法启动静音守护进程，已停止共享")
                stop()
                return
            outcome = None
            try:
                while True:
                    event = guard.read()
                    if event == "muted":
                        audio_ready.set()
                        audio_button.configure(text="本机会话静音中")
                    elif event == "failed":
                        audio_ready.clear()
                        audio_button.configure(text="静音未确认")
                    elif event in ("restored", "restore_failed"):
                        audio_ready.clear()
                        outcome = event
                        self.status.set("无法保持静音，已停止共享" if event == "restored"
                                        else "请检查音频输出：未确认恢复")
                        stop()
                        break
                    await asyncio.sleep(.1)
            except (EOFError, OSError):
                self.status.set("静音守护进程异常，已停止共享")
                stop()
            finally:
                guard.stop()
                if outcome is None:
                    try:
                        async with asyncio.timeout(4):
                            while (event := guard.read()) != "restored":
                                if event == "restore_failed":
                                    raise OSError()
                                await asyncio.sleep(.05)
                    except (EOFError, OSError, TimeoutError):
                        self.status.set("未确认声音恢复，请检查播放设备")
                await asyncio.to_thread(guard.close)

        def start_audio():
            nonlocal audio_task
            if audio_task is not None:
                return
            audio_button.configure(state="disabled", text="正在验证静音")
            audio_task = asyncio.create_task(mute_for_session())
        audio_button = ttk.Button(dialog, text="本次共享期间静音", command=start_audio)
        audio_button.pack(padx=16, pady=4)

        async def serve(reader, writer):
            if started.is_set():
                return
            self.host_task = asyncio.current_task()
            started.set()
            try:
                await host_desktop(reader, writer, backend, self.fps, enable_input)
            finally:
                if audio_task:
                    audio_task.cancel()
                    await asyncio.gather(audio_task, return_exceptions=True)
                self.host_task = None
                if dialog.winfo_exists():
                    dialog.destroy()
                self.host_id = None

        try:
            if transport == "rtc":
                from PIL import ImageGrab
                from rtc_host import host_rtc
                async def confirm(expected):
                    value = tk.StringVar()
                    self.label(dialog, "核对身份：输入手机上显示的本次校验码", 11).pack(padx=12)
                    field = ttk.Entry(dialog, textvariable=value)
                    field.pack(pady=4)
                    field.focus_set()
                    result = asyncio.get_running_loop().create_future()
                    def verify():
                        if not result.done():
                            result.set_result(value.get().replace(" ", "").upper() == expected)
                    button = ttk.Button(dialog, text="验证并共享", command=verify)
                    button.pack(pady=4)
                    try:
                        if not await result:
                            return False
                        if private:
                            await setup_privacy()
                        return True
                    finally:
                        button.destroy()
                        field.destroy()
                async def setup_privacy():
                    nonlocal curtain, curtain_task, privacy_ready
                    from privacy_curtain import Curtain, Unsupported
                    curtain = Curtain(source)
                    async with asyncio.timeout(12):
                        state = await curtain.wait()
                        if state != "covered":
                            raise Unsupported(state)
                        start_audio()
                        await audio_ready.wait()
                        state = await curtain.check()
                        if state != "covered":
                            raise Unsupported("开启静音时隐私屏状态已变化")
                    async def monitor():
                        try:
                            while (reason := await curtain.check()) == "covered":
                                await asyncio.sleep(.35)
                            self.status.set("本机已停止共享" if reason == "stop" else reason)
                        except (OSError, Unsupported):
                            self.status.set("无法继续确认隐私屏，已停止共享")
                        stop()
                    curtain_task = asyncio.create_task(monitor())
                    privacy_ready = True
                self.host_task = asyncio.current_task()
                try:
                    from privacy_editor import review_received
                    await host_rtc(self.client, sid, backend, ImageGrab.grab, self.fps,
                                   enable_input, confirm, self.config.get("rtc_stun"),
                                   lambda image: review_received(dialog, image,
                                       lambda: self.client.call("active", id=sid)),
                                   privacy_verified=lambda: privacy_ready and audio_ready.is_set())
                finally:
                    self.host_task = None
                return
            await self.receiver.allow_service(sid, source, "desktop", serve)
            await asyncio.wait_for(started.wait(), 31)
        finally:
            if self.host_task is None:
                if curtain_task is not None:
                    curtain_task.cancel()
                    await asyncio.gather(curtain_task, return_exceptions=True)
                if curtain is not None:
                    await curtain.close()
                if audio_task and not audio_task.done():
                    audio_task.cancel()
                    await asyncio.gather(audio_task, return_exceptions=True)
                self.receiver.approvals.pop(sid, None)
                if dialog.winfo_exists():
                    dialog.destroy()
                self.host_id = None
                await self.cancel_invite(sid)

    def prompt(self, invite):
        sid, source = invite["id"], invite["source"]
        purpose = invite["purpose"]
        window = tk.Toplevel(self.root)
        window.title({"desktop":"共享桌面","help":"远程协助","file":"接收文件"}[purpose])
        self.prompts[sid] = window
        private = tk.BooleanVar(window, False)
        prompt = {"desktop":"请求共享桌面","help":"邀请你协助其电脑","file":"请求发送文件"}[purpose]
        self.label(window, f"{source} {prompt}", 13, True).pack(padx=18, pady=13)

        async def offer_help():
            response = await self.client.call("offer_help", id=sid)
            await self.start_view(source, response["id"])

        def answer(choice):
            directory = None
            if choice == "file":
                directory = filedialog.askdirectory(parent=window, mustexist=True)
                if not directory:
                    return
            self.answered.add(sid)
            self.prompts.pop(sid, None)
            if window.winfo_exists():
                window.destroy()
            if choice == "file":
                self.spawn(self.receiver.allow(sid, source, directory))
            elif choice == "help":
                self.spawn(offer_help())
            elif choice in ("view", "control"):
                self.spawn(self.share_desktop(sid, source, choice == "control", invite.get("transport", "tcp"),
                                             private.get()))
            else:
                self.spawn(self.client.call("deny", id=sid))

        if purpose == "desktop":
            if invite.get("transport") == "rtc":
                ttk.Checkbutton(window, text="本次隐私共享（单屏遮挡并静音，验证失败则不共享）",
                                variable=private).pack(padx=18, pady=4)
            ttk.Button(window, text="仅共享画面", command=lambda: answer("view")).pack(padx=18, pady=4)
            ttk.Button(window, text="共享并允许操控", command=lambda: answer("control")).pack(padx=18, pady=4)
        elif purpose == "help":
            ttk.Button(window, text="发起协助，等待对方授权", command=lambda: answer("help")).pack(padx=18, pady=4)
        else:
            ttk.Button(window, text="选择目录并接收", command=lambda: answer("file")).pack(padx=18, pady=4)
        window.protocol("WM_DELETE_WINDOW", lambda: answer("deny"))
        ttk.Button(window, text="拒绝", command=lambda: answer("deny")).pack(padx=18, pady=(4, 10))

    async def connect(self):
        async with AsyncExitStack() as stack:
            self.client = await stack.enter_async_context(Peer(self.config, self.token))
            def received(message):
                self.record_transfer(message)
                self.status.set(message)
            self.receiver = FileReceiver(self.client, received)
            port = await self.receiver.start(self.config.get("bind", "127.0.0.1"),
                                             self.config.get("port", 9443))
            stack.push_async_callback(self.receiver.close)
            allowed = list(self.config["peers"])
            while self.alive:
                pending = {}
                try:
                    await self.client.call("heartbeat", port=port, desktop=sys.platform == "win32",
                                           rtc=sys.platform == "win32" and self.config.get("mobile_desktop") is True)
                    self.devices = (await self.client.call("devices", peers=allowed))["devices"]
                    result = await self.client.call("inbox")
                    pending = {item["id"]: item for item in result["pending"]
                               if item.get("purpose") in ("file", "desktop", "help") and item["source"] in allowed}
                    if self.status.get().startswith(("未连接信令", "连接信令失败")):
                        self.status.set("已连接信令服务")
                except Exception as error:
                    if (isinstance(error, ClientResponseError) and error.status == 401
                            and self.portal_runner is not None):
                        await self.toggle_terminal()
                    self.devices = dict.fromkeys(allowed)
                    self.status.set(f"连接信令失败 · {type(error).__name__}")
                self.render()
                for sid in self.prompts.keys() - pending.keys():
                    self.prompts.pop(sid).destroy()
                self.answered.intersection_update(pending)
                for sid in pending.keys() - self.prompts.keys() - self.answered:
                    self.prompt(pending[sid])
                await asyncio.sleep(1.5)

    async def toggle_terminal(self):
        if self.portal_runner is not None:
            runner, self.portal_runner = self.portal_runner, None
            await runner.cleanup()
            self.term_button.configure(fg_color="#f5f7fa")
            self.status.set("手机终端已关闭")
            return
        config = self.config.get("terminal")
        if not config:
            messagebox.showinfo("手机终端", "请先配置 terminal 的 HTTPS 地址、证书和本机启动命令。")
            return
        origin = urlsplit(config["origin"])
        if origin.scheme != "https" or not origin.hostname or origin.path or origin.query \
                or origin.fragment or origin.username is not None:
            raise ValueError("必须配置 HTTPS origin")
        command = config["command"]
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError("终端命令须为非空参数数组")
        self.term_button.configure(state="disabled")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            tls.minimum_version = ssl.TLSVersion.TLSv1_3
            tls.load_cert_chain(config["cert"], config["key"])
            assets = Path(config["assets"]) if config.get("assets") else None
            if (assets is None or not assets.is_dir()) and getattr(sys, "frozen", False):
                assets = Path(__file__).resolve().parent / "xterm"
            portal = Portal(config["origin"], command, Path(config["cwd"]).resolve(strict=True), assets)
            runner = web.AppRunner(portal.app(), access_log=None, handler_cancellation=True)
            await runner.setup()
            try:
                site = web.TCPSite(runner, config.get("bind", "127.0.0.1"),
                                   origin.port or 443, ssl_context=tls)
                await site.start()
            except BaseException:
                await runner.cleanup()
                raise
            self.portal_runner = runner
            self.term_button.configure(fg_color="#ddeaff")
            dialog = tk.Toplevel(self.root)
            dialog.title("手机终端配对")
            self.label(dialog, config["origin"], 12).pack(padx=16, pady=8)
            self.label(dialog, "一次性配对码 · 5 分钟有效", 11).pack(padx=16, pady=4)
            field = ttk.Entry(dialog, width=34)
            field.insert(0, portal.pairing)
            field.configure(state="readonly")
            field.pack(padx=16, pady=(4, 12))
        finally:
            self.term_button.configure(state="normal")


async def run(config, token):
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    desktop = Desktop(root, config, token)
    connection = asyncio.create_task(desktop.connect())
    try:
        while desktop.alive:
            root.update()
            if connection.done() and not connection.cancelled():
                if error := connection.exception():
                    desktop.status.set(f"连接信令失败 · {type(error).__name__}")
            await asyncio.sleep(1 / 45)
    finally:
        if desktop.portal_runner:
            await desktop.portal_runner.cleanup()
        for task in desktop.jobs:
            task.cancel()
        await asyncio.gather(*desktop.jobs, return_exceptions=True)
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        root.destroy()


def first_run():
    import customtkinter as ctk
    from office_ui import BLUE, MUTED
    ctk.set_appearance_mode("light")
    window = ctk.CTk()
    window.title("nn远程 · 连接设备")
    window.geometry("500x390")
    window.minsize(440, 370)
    page = ctk.CTkFrame(window, fg_color="white", corner_radius=0)
    page.pack(fill="both", expand=True)
    frame = ctk.CTkFrame(page, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=28, pady=22)
    ctk.CTkLabel(frame, text="连接设备", font=(FONT, 24, "bold")).pack(anchor="w")
    ctk.CTkLabel(frame, text="只导入可信来源的配置。连接前请核对信令地址。", text_color=MUTED).pack(anchor="w")
    configuration = None
    result = None
    origin = tk.StringVar(master=window, value="尚未选择设备配置")
    error = tk.StringVar(master=window)
    def browse():
        nonlocal configuration
        filename = filedialog.askopenfilename(parent=window, title="选择设备配置",
                                              filetypes=[("设备配置", "*.json")])
        if not filename:
            return
        secret.delete(0, "end")
        configuration = None
        origin.set("尚未选择设备配置")
        try:
            configuration = read_profile(filename)
        except (ValueError, OSError):
            error.set("配置无效，请检查 HTTPS 地址、设备清单及证书路径。")
            return
        origin.set(configuration["signaling"])
        error.set("")
        secret.focus_set()
    ctk.CTkButton(frame, text="选择设备配置", command=browse).pack(anchor="w", pady=(16, 4))
    ctk.CTkLabel(frame, textvariable=origin, wraplength=370, anchor="w").pack(fill="x")
    secret = ctk.CTkEntry(frame, show="*", placeholder_text="设备令牌（仅本次使用）", height=36)
    secret.pack(fill="x", pady=10)
    def connect():
        nonlocal result
        if configuration is None:
            error.set("请先选择设备配置。")
            return
        try:
            result = configuration, device_token(secret.get())
        except ValueError as exc:
            error.set(str(exc))
            return
        secret.delete(0, "end")
        window.quit()
    ctk.CTkButton(frame, text="连接", fg_color=BLUE, command=connect).pack(anchor="w", pady=4)
    ctk.CTkLabel(frame, textvariable=error, text_color="#94302c").pack(anchor="w")
    secret.bind("<Return>", lambda _: connect())
    window.protocol("WM_DELETE_WINDOW", window.quit)
    window.mainloop()
    window.destroy()
    return result


def main():
    parser = argparse.ArgumentParser(description="nn远程桌面客户端")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--config")
    mode.add_argument("--check-rtc-runtime", action="store_true")
    mode.add_argument("--check-ui-assets", action="store_true",
                      help="Validate bundled UI artwork without connecting to a server")
    args = parser.parse_args()
    if args.check_rtc_runtime:
        from rtc_runtime_check import verify
        if sys.platform == "win32":
            from privacy_curtain import _native
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            _native()
        asyncio.run(verify())
        return
    if args.check_ui_assets:
        from office_ui import glyph
        root = Path(__file__).resolve().parent
        xterm = root / ("xterm" if getattr(sys, "frozen", False) else "node_modules/@xterm/xterm")
        for asset in [root / "ui_assets/nn-remote.png", root / "terminal_web/index.html",
                      root / "terminal_web/portal.css", root / "terminal_web/portal.js",
                      xterm / "lib/xterm.js", xterm / "css/xterm.css"]:
            if not asset.is_file() or asset.stat().st_size == 0:
                raise FileNotFoundError(f"Missing client resource: {asset.name}")
        for icon in ("desktop", "people", "folder_arrow_right", "history", "settings", "code",
                     "copy", "lock_closed", "shield_checkmark", "arrow_swap", "search"):
            glyph(icon, 24, "#526173")
        print("UI assets OK")
        return
    if args.config:
        try:
            connection = read_profile(args.config), device_token(os.environ.get("NN_DEVICE_TOKEN", ""))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    else:
        connection = first_run()
    if connection is not None:
        asyncio.run(run(*connection))


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
