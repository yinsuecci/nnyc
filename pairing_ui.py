import tkinter as tk
from tkinter import messagebox
from typing import Any

import customtkinter as ctk
from aiohttp import ClientResponseError

from office_ui import BLUE, FONT, LINE, MUTED, button, label


def open_panel(app: Any) -> None:
    old = getattr(app, "_pair_window", None)
    if old is not None and old.winfo_exists():
        old.lift()
        return
    window = app._pair_window = tk.Toplevel(app.root, bg="white")
    window.title("nn远程 · 手机配对")
    window.transient(app.root)
    window.minsize(390, 410)
    body = tk.Frame(window, bg="white", padx=22, pady=16)
    body.pack(fill="both", expand=True)
    label(body, "设备配对", 16, True).pack(anchor="w")
    label(body, "任选一端生成配对码。绑定后，再次连接无需输码。", color=MUTED).pack(anchor="w", pady=(4, 12))
    row = tk.Frame(body, bg="#eef4ff", padx=12, pady=10)
    row.pack(fill="x")
    display = tk.StringVar(window, "电脑配对码")
    label(row, textvariable=display, size=14, bold=True, color=BLUE).pack(side="left")
    entry = ctk.CTkEntry(body, placeholder_text="输入手机显示的 9 位配对码", font=(FONT, 14), height=38)
    feedback = tk.StringVar(window)
    phones = ctk.CTkScrollableFrame(body, fg_color="white", height=100, border_color=LINE, border_width=1)
    busy = False
    expiry: str | None = None
    polling: str | None = None

    async def command(action: str, value: str = "") -> None:
        nonlocal busy, expiry, polling
        if busy or not window.winfo_exists():
            return
        if app.client is None:
            feedback.set("电脑尚未连接信令服务器")
            return
        busy = True
        try:
            args = {"code": value} if action == "accept" else {"phone": value} if action == "remove" else {}
            if action == "accept" and not (len(value) == 9 and value.isascii() and value.isdigit()):
                feedback.set("请输入完整的 9 位手机配对码")
                return
            result = await app.client.call("binding_" + action, **args)
            if not window.winfo_exists():
                return
            if action == "issue":
                code = result["code"]
                display.set(" ".join(code[i:i+3] for i in range(0, 9, 3)))
                if expiry is not None:
                    window.after_cancel(expiry)
                expiry = window.after(120000, lambda: display.set("配对码已过期"))
                feedback.set("请在手机输入，有效期 2 分钟")
            elif action in ("accept", "remove"):
                entry.delete(0, "end")
                feedback.set("已绑定手机" if action == "accept" else "已解除绑定")
            listing = result if action == "list" else await app.client.call("binding_list")
            if not window.winfo_exists():
                return
            for child in phones.winfo_children():
                child.destroy()
            if polling is not None:
                window.after_cancel(polling)
                polling = None
            if listing["pending"]:
                polling = window.after(2500, lambda: app.spawn(command("list")))
            elif display.get()[:1].isdigit():
                display.set("电脑配对码")
                feedback.set("配对码已使用或已失效")
                if expiry is not None:
                    window.after_cancel(expiry)
                    expiry = None
            if not listing["phones"]:
                ctk.CTkLabel(phones, text="暂无已绑定手机", text_color=MUTED).pack(anchor="w")
            for device in listing["phones"]:
                line = tk.Frame(phones, bg="white")
                line.pack(fill="x", pady=2)
                label(line, "iPhone · " + device[:8], bold=True).pack(side="left")
                def unlink(target: str = device) -> None:
                    if messagebox.askyesno("解除绑定", "解绑并断开这台手机的配对会话？", parent=window):
                        app.spawn(command("remove", target))
                button(line, "解除绑定", unlink).pack(side="right")
        except ClientResponseError as err:
            feedback.set({401: "配对码无效或已过期", 404: "服务器未更新或设备已解绑",
                          409: "请确认本机已上线并启用移动桌面", 429: "请求过于频繁，请稍后重试"}
                         .get(err.status, f"服务返回 HTTP {err.status}"))
        except (TimeoutError, OSError):
            feedback.set("无法联系配对服务，请检查网络")
        finally:
            busy = False

    button(row, "生成配对码", lambda: app.spawn(command("issue")), blue=True).pack(side="right")
    entry.pack(fill="x", pady=(14, 6))
    button(body, "绑定手机", lambda: app.spawn(command("accept", entry.get().replace(" ", "")))).pack(anchor="w")
    label(body, textvariable=feedback, color=MUTED).pack(anchor="w", pady=5)
    heading = tk.Frame(body, bg="white")
    heading.pack(fill="x", pady=6)
    label(heading, "已绑定手机", 12, True).pack(side="left")
    button(heading, "刷新", lambda: app.spawn(command("list"))).pack(side="right")
    phones.pack(fill="both", expand=True)
    app.spawn(command("list"))
