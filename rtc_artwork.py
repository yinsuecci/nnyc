"""Bounded, ordered messages on the authorized session's separate artwork channel."""
import io
import json
import time

from privacy_artwork import read_photo


class IncomingArtwork:
    LIMIT = 4 * 1024 * 1024
    CHUNK = 16384

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.expected = None
        self.buffer = bytearray()
        self.deadline = 0
        self.attempts = 0

    def feed(self, message):
        if self.expected is None:
            if not isinstance(message, str) or len(message) > 80 or self.attempts >= 4:
                raise ValueError("invalid artwork request")
            fields = json.loads(message)
            if not isinstance(fields, dict) or set(fields) != {"bytes"}:
                raise ValueError("invalid artwork header")
            size = fields["bytes"]
            if type(size) is not int or not 1 <= size <= self.LIMIT:
                raise ValueError("invalid artwork size")
            self.expected = size
            self.attempts += 1
            self.deadline = self.clock() + 90
            return "ready", None
        if self.clock() > self.deadline:
            raise ValueError("artwork timed out")
        if isinstance(message, bytes):
            if not 1 <= len(message) <= self.CHUNK or len(self.buffer) + len(message) > self.expected:
                raise ValueError("invalid artwork chunk")
            self.buffer.extend(message)
            return "next", None
        if message != "finish" or len(self.buffer) != self.expected:
            raise ValueError("incomplete artwork")
        data = bytes(self.buffer)
        self.expected = None
        self.buffer.clear()
        return "review", data


def decode_artwork(data):
    return read_photo(io.BytesIO(data))
