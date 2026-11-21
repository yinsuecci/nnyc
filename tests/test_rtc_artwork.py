import unittest
from rtc_artwork import IncomingArtwork


class ReceiverTests(unittest.TestCase):
    def test_boundaries(self):
        rx = IncomingArtwork()
        for invalid in ['{"bytes":true}', '{"bytes":0}', '{"bytes":4194305}', '[]']:
            with self.assertRaises(ValueError):
                rx.feed(invalid)
        self.assertEqual(rx.feed('{"bytes":2}')[0], "ready")
        with self.assertRaises(ValueError):
            rx.feed(b"123")
        rx.feed(b"a")
        with self.assertRaises(ValueError):
            rx.feed("finish")
        rx.feed(b"b")
        self.assertEqual(rx.feed("finish"),("review",b"ab"))

    def test_timeout(self):
        now = [0]
        rx = IncomingArtwork(lambda: now[0])
        rx.feed('{"bytes":12}')
        now[0] = 91
        with self.assertRaises(ValueError):
            rx.feed(b"x")
