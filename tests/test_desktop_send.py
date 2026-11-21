import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from desktop import Desktop


class DesktopSendTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, fails=False):
        app = Mock(sending=False)
        app.client.call = AsyncMock(return_value={"id": "session"})
        app.cancel_invite = AsyncMock()

        async def transport(client, target, sid, path, progress):
            self.assertIsInstance(path, Path)
            progress(8, 8)
            self.assertIn("等待完整性确认", app.transfer.set.call_args.args[0])
            if fails:
                raise ConnectionError()

        with patch("desktop.send_file", transport):
            if fails:
                with self.assertRaises(ConnectionError):
                    await Desktop.send(app, "peer", "private-folder/report.zip")
            else:
                await Desktop.send(app, "peer", "private-folder/report.zip")
        self.assertFalse(app.sending)
        app.cancel_invite.assert_awaited_once_with("session")
        app.transfer_name.set.assert_called_once_with("report.zip")
        record = app.record_transfer.call_args.args[0]
        self.assertNotIn("private-folder", record)
        self.assertIn("发送失败" if fails else "对端已确认", record)

    async def test_confirmation(self):
        await self.exercise()

    async def test_missing_confirmation(self):
        await self.exercise(fails=True)
