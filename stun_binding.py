"""Rate-limited STUN binding discovery, not a relay or an ICE connectivity check."""
import asyncio
import ipaddress
import struct
import time
import zlib

COOKIE = 0x2112A442
HEADER = struct.Struct("!HHI12s")


def binding_reply(packet, peer):
    if not 20 <= len(packet) <= 1024:
        return None
    kind, length, cookie, transaction = HEADER.unpack_from(packet)
    if (kind, length, cookie) != (1, len(packet) - 20, COOKIE) or length % 4:
        return None
    offset, fingerprint = 20, False
    while offset < len(packet):
        attr, size = struct.unpack_from("!HH", packet, offset)
        end = offset + 4 + ((size + 3) // 4) * 4
        if end > len(packet) or attr < 0x8000:
            return None
        if attr == 0x8028:
            if size != 4 or end != len(packet):
                return None
            if struct.unpack_from("!I", packet, offset + 4)[0] != zlib.crc32(packet[:offset]) ^ 0x5354554E:
                return None
            fingerprint = True
        offset = end
    addr = ipaddress.ip_address(peer[0])
    mask = struct.pack("!I", COOKIE) + transaction
    mapped = struct.pack("!BBH", 0, 1 if addr.version == 4 else 2, peer[1] ^ (COOKIE >> 16))
    mapped += bytes(a ^ b for a, b in zip(addr.packed, mask))
    body = struct.pack("!HH", 0x0020, len(mapped)) + mapped
    reply = HEADER.pack(0x0101, len(body) + 8 * fingerprint, COOKIE, transaction) + body
    if fingerprint:
        reply += struct.pack("!HHI", 0x8028, 4, zlib.crc32(reply) ^ 0x5354554E)
    return reply


class BindingServer(asyncio.DatagramProtocol):
    def __init__(self):
        self.second, self.total, self.sources = -1, 0, {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        now = int(time.monotonic())
        if now != self.second:
            self.second, self.total, self.sources = now, 0, {}
        if self.total >= 128 or self.sources.get(peer[0], 0) >= 12:
            return
        self.total += 1
        self.sources[peer[0]] = self.sources.get(peer[0], 0) + 1
        reply = binding_reply(data, peer)
        if reply:
            self.transport.sendto(reply, peer)


def install_stun(app, host, port):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid stun_port")
    if port:
        async def listen(_app):
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                BindingServer, local_addr=(host, port))
            try:
                yield
            finally:
                transport.close()
        app.cleanup_ctx.append(listen)
