"""Offline rendering checks; no device tokens or network access."""
import os
import sys
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "win32" or sys.platform.startswith("linux") and os.getenv("DISPLAY"),
                     "requires Windows or Linux with a display")
class OfficeLayoutTests(unittest.TestCase):
    def test_contacts_resize_and_progress(self):
        import tkinter as tk
        from office_ui import OfficeUI

        class Sample(OfficeUI):
            def close(self):
                pass
            choose_file = show_settings = close

        root = tk.Tk()
        self.addCleanup(root.destroy)
        ui = Sample()
        ui.root, ui.view = root, None
        ui.status = tk.StringVar(master=root)
        ui.devices = {"820 431 776": True, "home": True, "notebook": False}
        ui.config = {"peers": list(ui.devices), "device_names":
                     dict(zip(ui.devices, ("办公室电脑", "家用电脑", "笔记本")))}
        ui.build_office()
        widths = []
        for size in ("1100x740", "550x470", "1100x740"):
            root.geometry(size)
            root.update()
            if ui.compact:
                ui.open_pane(True)
                root.update()
            self.assertTrue(ui.view_button.winfo_ismapped())
            widths.append(ui.rail.winfo_width())
        self.assertEqual(len(set(widths)), 1)
        self.assertTrue(all(w.cget("text") == "" for w in ui.rail.winfo_children()))
        ui.search.set("home")
        self.assertEqual(ui.selected(), "home")
        ui.search.set("")
        ui.select_peer("820 431 776")
        from desktop_view import DesktopView
        ui.view = DesktopView(root, ui.selected(), ui.selection)
        ui.render()
        self.assertIn("等待", ui.session_state.get())
        self.assertNotIn("E2E", ui.security_state.get())
        with self.assertRaises(ValueError):
            ui.view.connected(500, True)
        self.assertFalse(ui.view.verified)
        ui.view.connected(30, False)
        self.assertIn("仅查看", ui.session_state.get())
        self.assertIn("E2E", ui.security_state.get())
        ui.select_peer("home")
        self.assertEqual(ui.session_state.get(), "未连接")
        self.assertNotIn("E2E", ui.security_state.get())
        ui.select_peer("820 431 776")
        ui.view.close()
        ui.view = None
        ui.render()
        self.assertNotIn("E2E", ui.security_state.get())
        self.assertEqual(ui.progress.get(), 0)
        ui.progress.set(.46)
        self.assertAlmostEqual(ui.progress.get(), .46)
        ui.transfer_name.set("项目资料.zip")
        ui.transfer.set("58.2 / 126.8 MiB（46%） · 正在传输（示例）")
        root.update()
        if path := os.environ.get("NN_OFFICE_PREVIEW"):
            from PIL import ImageGrab
            if sys.platform == "win32":
                import ctypes
                image = ImageGrab.grab(window=ctypes.windll.user32.GetParent(root.winfo_id()))
            else:
                x, y = root.winfo_rootx(), root.winfo_rooty()
                image = ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height()),
                                       xdisplay=os.environ["DISPLAY"])
            self.assertTrue(image.getbbox())
            image.save(Path(path))
