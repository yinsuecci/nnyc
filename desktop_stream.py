"""Consent-bound desktop stream over the same pinned TLS as file transfer."""
import asyncio
import ctypes
import io
import json
import sys
from ctypes import wintypes
from collections import deque

from PIL import Image, ImageGrab, UnidentifiedImageError

from peer import active_session, peer_connection, read_message, write_message

MAX_FRAME = 512 * 1024
MAX_SIZE = (1920, 1080)


def encode_frame(image):
    image.thumbnail(MAX_SIZE)
    if image.mode != "RGB":
        image = image.convert("RGB")
    for quality in (76, 55, 35):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_FRAME:
            return buffer.getvalue()
    raise ValueError("编码帧超出限制")


def decode_frame(frame):
    try:
        with Image.open(io.BytesIO(frame), formats=["JPEG"]) as image:
            if not 1 <= image.width <= MAX_SIZE[0] or not 1 <= image.height <= MAX_SIZE[1]:
                raise ValueError("帧分辨率超出限制")
            return image.convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("无效桌面帧") from error


async def read_frame(reader):
    size = int.from_bytes(await reader.readexactly(4), "big")
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("帧大小超出限制")
    return await reader.readexactly(size)


# Use the correctly aligned INPUT union, including its largest member on 32/64-bit.
class Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class Key(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t)]


class Payload(ctypes.Union):
    _fields_ = [("mouse", Mouse), ("key", Key)]


class Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("data", Payload)]


KEYS = {
    "BackSpace": 0x08, "Tab": 0x09, "Return": 0x0D, "Escape": 0x1B,
    "space": 0x20, "Delete": 0x2E, "Insert": 0x2D, "Home": 0x24,
    "End": 0x23, "Prior": 0x21, "Next": 0x22, "Left": 0x25,
    "Up": 0x26, "Right": 0x27, "Down": 0x28, "Shift_L": 0xA0,
    "Shift_R": 0xA1, "Control_L": 0xA2, "Control_R": 0xA3,
    "Alt_L": 0xA4, "Alt_R": 0xA5, **{f"F{i}": 0x6F + i for i in range(1, 13)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0xA3, 0xA5}
BUTTONS = {"left": (2, 4), "middle": (32, 64), "right": (8, 16)}


def input_event(data):
    if len(data) > 256:
        raise ValueError("输入事件过长")
    event = json.loads(data)
    if not isinstance(event, list) or not event or not isinstance(event[0], str):
        raise ValueError("输入事件无效")
    kind, *args = event
    if kind == "move" and len(args) == 2 and all(type(v) is int and 0 <= v <= 65535 for v in args):
        return kind, args
    if (kind in ("key", "button") and len(args) == 2 and isinstance(args[0], str)
            and args[0] in (KEYS if kind == "key" else BUTTONS) and type(args[1]) is bool):
        return kind, args
    if kind == "wheel" and len(args) == 1 and type(args[0]) is int and args[0] in (-1, 1):
        return kind, args
    if kind == "release" and not args:
        return kind, args
    raise ValueError("输入事件无效")


class WindowsDesktop:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("当前系统不支持作为桌面被控端")
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        self.api.SendInput.restype = wintypes.UINT
        self.api.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.api.SetCursorPos.restype = wintypes.BOOL
        self.down = {"key": set(), "button": set()}

    @staticmethod
    def capture():
        return encode_frame(ImageGrab.grab(all_screens=False))

    def inject(self, data):
        if self.api.SendInput(1, ctypes.byref(data), ctypes.sizeof(data)) != 1:
            raise OSError("系统拒绝输入")

    def handle(self, kind, args):
        if kind == "release":
            self.release()
        elif kind == "move":
            width, height = [self.api.GetSystemMetrics(i) for i in (0, 1)]
            x, y = (v * (size - 1) // 65535 for v, size in zip(args, (width, height)))
            if not self.api.SetCursorPos(x, y):
                raise OSError("系统拒绝移动光标")
        elif kind == "wheel":
            data = Mouse(data=args[0] * 120 & 0xFFFFFFFF, flags=0x800)
            self.inject(Input(0, Payload(mouse=data)))
        else:
            name, pressed = args
            if kind == "button":
                flags = BUTTONS[name][not pressed]
                self.inject(Input(0, Payload(mouse=Mouse(flags=flags))))
            else:
                vk = KEYS[name]
                flags = (0 if pressed else 2) | int(vk in EXTENDED)
                self.inject(Input(1, Payload(key=Key(vk=vk, flags=flags))))
            if pressed:
                self.down[kind].add(name)
            else:
                self.down[kind].discard(name)

    def release(self):
        for kind, keys in self.down.items():
            for name in tuple(keys):
                try:
                    self.handle(kind, (name, False))
                except OSError:
                    keys.discard(name)


async def stream_frames(writer, capture, fps, clock=None):
    loop = asyncio.get_running_loop()
    now = clock or loop.time
    interval = 1 / fps
    while True:
        began = now()
        frame = await asyncio.to_thread(capture)
        if not 1 <= len(frame) <= MAX_FRAME:
            raise ValueError("无效编码帧")
        writer.write(len(frame).to_bytes(4, "big") + frame)
        async with asyncio.timeout(5):
            await writer.drain()
        await asyncio.sleep(max(0, interval - (now() - began)))


async def race(left, right):
    tasks = [asyncio.create_task(left), asyncio.create_task(right)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def host_desktop(reader, writer, backend, fps, enable_input):
    async def read_input():
        arrivals = deque()
        loop = asyncio.get_running_loop()
        while True:
            kind, args = input_event(await read_message(reader))
            now = loop.time()
            while arrivals and arrivals[0] <= now - 1:
                arrivals.popleft()
            if len(arrivals) >= 240:
                raise ValueError("输入频率过高")
            arrivals.append(now)
            if enable_input:
                backend.handle(kind, args)

    await write_message(writer, json.dumps({"fps": fps, "input": enable_input}).encode())
    try:
        await race(stream_frames(writer, backend.capture, fps), read_input())
    finally:
        backend.release()


async def view_desktop(client, target, sid, view):
    async with peer_connection(client, target, sid) as (reader, writer):
        async with asyncio.timeout(5):
            await write_message(writer, bytes.fromhex(sid))
            if await read_message(reader) != b"desktop":
                raise ValueError("对端未授权桌面会话")
            settings = json.loads(await read_message(reader))
        view.connected(settings["fps"], settings["input"])

        async def receive():
            while True:
                frame = await read_frame(reader)
                view.show(await asyncio.to_thread(decode_frame, frame))

        async def send():
            while (event := await view.events.get()) is not None:
                await write_message(writer, json.dumps(event).encode())

        async with active_session(client, sid, writer):
            await race(receive(), send())
