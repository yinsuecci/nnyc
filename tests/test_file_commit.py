import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from file_transfer import receive_file


def frame(data):
    return len(data).to_bytes(4, "big") + data


class Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass


class FileCommitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = Writer()
        self.authorized = 0

    async def authorize(self):
        self.authorized += 1
        return {"active": True}

    def reader(self, content, checksum):
        reader = asyncio.StreamReader()
        reader.feed_data(frame(json.dumps({"name": "test.bin", "size": len(content)}).encode()))
        if content:
            reader.feed_data(frame(content))
        if checksum is not None:
            reader.feed_data(frame(checksum))
        reader.feed_eof()
        return reader

    async def test_checksum_mismatch_or_missing_never_publishes(self):
        for checksum in (None, b"", b"x" * 32, b"x" * 33):
            with self.subTest(checksum=checksum):
                with self.assertRaises((ValueError, EOFError)):
                    await receive_file(self.reader(b"data", checksum), self.writer,
                                       self.root, self.authorize)
                self.assertEqual(list(self.root.iterdir()), [])
                self.assertEqual(self.writer.data, b"")
                self.assertEqual(self.authorized, 0)

    async def test_false_or_malformed_authorization_never_publishes(self):
        content = b"data"
        for result in (None, {}, {"active": False}, {"active": 1}):
            async def authorize():
                return result

            with self.subTest(result=result):
                with self.assertRaises(PermissionError):
                    await receive_file(self.reader(content, hashlib.sha256(content).digest()),
                                       self.writer, self.root, authorize)
                self.assertEqual(list(self.root.iterdir()), [])
                self.assertEqual(self.writer.data, b"")

    async def test_empty_file_is_verified_and_acknowledged(self):
        digest = hashlib.sha256(b"").digest()
        destination = await receive_file(self.reader(b"", digest), self.writer,
                                         self.root, self.authorize)
        self.assertEqual(destination.read_bytes(), b"")
        self.assertEqual(self.writer.data, frame(digest))
        self.assertEqual(list(self.root.iterdir()), [destination])
        self.assertEqual(self.authorized, 1)

    async def test_flush_failure_never_publishes(self):
        digest = hashlib.sha256(b"data").digest()
        with patch("file_transfer.os.fsync", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                await receive_file(self.reader(b"data", digest), self.writer,
                                   self.root, self.authorize)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.writer.data, b"")
        self.assertEqual(self.authorized, 0)

    async def test_cancellation_waiting_for_checksum_cleans_partial_file(self):
        reader = asyncio.StreamReader()
        reader.feed_data(frame(b'{"name":"test.bin","size":4}') + frame(b"data"))
        task = asyncio.create_task(receive_file(reader, self.writer, self.root, self.authorize))
        try:
            async with asyncio.timeout(2):
                while not list(self.root.iterdir()):
                    await asyncio.sleep(0.01)
            self.assertFalse((self.root / "test.bin").exists())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.authorized, 0)
