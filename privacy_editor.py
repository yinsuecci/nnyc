"""Windows desktop preferences: edit wallpaper without activating a privacy session."""
import tkinter as tk
import asyncio
import os
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from privacy_artwork import load, read_photo, render, save


def personal_artwork():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "NNRemote" / "privacy-artwork.json"


async def review_received(parent, image, verify_active):
    sheet = tk.Toplevel(parent)
    sheet.title("手机请求更换隐私屏图片")
    sheet.transient(parent)
    result = asyncio.get_running_loop().create_future()
    def decide(value):
        if not result.done():
            result.set_result(value)
    sheet.protocol("WM_DELETE_WINDOW", lambda: decide(False))
    try:
        preview = ImageTk.PhotoImage(render(image,(320,180),"fit"), master=sheet)
        ttk.Label(sheet,image=preview).pack(padx=12,pady=8)
        ttk.Label(sheet,text="仅替换本机图片，不开启隐私屏。").pack()
        ttk.Button(sheet,text="拒绝",command=lambda: decide(False)).pack(side="left",padx=12,pady=8)
        ttk.Button(sheet,text="保存图片",command=lambda: decide(True)).pack(side="right",padx=12,pady=8)
        if not await result:
            return False
        await verify_active()
        save(personal_artwork(),image)
        return True
    finally:
        if sheet.winfo_exists():
            sheet.destroy()


def open_editor(parent, path):
    try:
        picture, (mode, x, y, zoom) = load(path)
    except (OSError, ValueError, TypeError, KeyError, Image.DecompressionBombError):
        messagebox.showwarning("隐私屏图片", "原有设置无法读取，可选择一张新图。", parent=parent)
        picture, (mode, x, y, zoom) = None, ("fill", .5, .5, 1)
    dialog = tk.Toplevel(parent)
    dialog.title("隐私屏图片")
    dialog.transient(parent)
    box = ttk.Frame(dialog, padding=16)
    box.pack(fill="both", expand=True)
    ttk.Label(box, text="本机隐私屏图片 · 仅预览").pack(anchor="w")
    preview = ttk.Label(box)
    preview.pack(pady=8)
    mode_var = tk.StringVar(dialog, value=mode)
    scale = tk.DoubleVar(dialog, value=zoom)
    def draw(*_):
        im = ImageTk.PhotoImage(render(picture, (400, 225), mode_var.get(), x, y, scale.get()), master=dialog)
        preview.configure(image=im)
        preview.image = im

    def choose():
        nonlocal picture, x, y
        name = filedialog.askopenfilename(parent=dialog, filetypes=[("JPEG / PNG", "*.jpg *.jpeg *.png")])
        if not name:
            return
        try:
            with open(name, "rb") as f:
                picture = read_photo(f)
            x = y = .5
            scale.set(1)
            draw()
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            messagebox.showerror("无法使用图片", str(e), parent=dialog)

    def position(event):
        nonlocal x, y
        if mode_var.get() == "fill":
            x = min(1, max(0, event.x / max(1, preview.winfo_width())))
            y = min(1, max(0, event.y / max(1, preview.winfo_height())))
            draw()
    preview.bind("<Button-1>", position)
    preview.bind("<B1-Motion>", position)
    controls = ttk.Frame(box)
    controls.pack(fill="x")
    ttk.Button(controls, text="更换图片", command=choose).pack(side="left")
    for text, key in (("填充", "fill"), ("完整显示", "fit")):
        ttk.Radiobutton(controls, text=text, variable=mode_var, value=key, command=draw).pack(side="left")
    ttk.Scale(box, from_=1, to=3, variable=scale, command=draw).pack(fill="x", pady=6)
    ttk.Label(box, text="填充时拖动预览可调整取景；保存不会开启遮屏。").pack(anchor="w")
    def reset():
        nonlocal picture, x, y
        picture, x, y = None, .5, .5
        scale.set(1)
        mode_var.set("fill")
        draw()
    def commit():
        try:
            save(path, picture, mode_var.get(), x, y, scale.get())
        except (ValueError, OSError) as e:
            messagebox.showerror("保存失败", str(e), parent=dialog)
            return
        dialog.destroy()
    actions = ttk.Frame(box)
    actions.pack(fill="x", pady=(12,0))
    ttk.Button(actions, text="恢复默认", command=reset).pack(side="left")
    ttk.Button(actions, text="保存图片", command=commit).pack(side="right")
    draw()
