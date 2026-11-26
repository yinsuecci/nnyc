"""Persistent pair relationships; invitations and session keys never go to disk."""
import secrets
import sqlite3
from pathlib import Path
from typing import Literal

from aiohttp import web

Role = Literal["host", "phone"]


class DeviceBindings:
    def __init__(self, path: Path | None = None) -> None:
        self.db = sqlite3.connect(path or ":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS binding (host TEXT, phone TEXT, PRIMARY KEY(host, phone))")
        self.db.execute("CREATE INDEX IF NOT EXISTS by_phone ON binding(phone)")
        self.codes: dict[str, tuple[Role, str, float]] = {}

    def close(self) -> None:
        self.db.close()

    def expire(self, now: float) -> None:
        for code, (_, _, deadline) in list(self.codes.items()):
            if deadline <= now:
                del self.codes[code]

    def issue(self, role: Role, owner: str, now: float) -> str:
        self.expire(now)
        self.clear_codes(role, owner)
        if len(self.codes) >= 4096:
            raise web.HTTPTooManyRequests()
        while (code := f"{secrets.randbelow(10**9):09d}") in self.codes:
            pass
        self.codes[code] = role, owner, now + 120
        return code

    def clear_codes(self, role: Role, owner: str) -> None:
        for code, record in list(self.codes.items()):
            if record[:2] == (role, owner):
                del self.codes[code]

    def pending(self, role: Role, owner: str, now: float) -> bool:
        return any(record[:2] == (role, owner) and record[2] > now for record in self.codes.values())

    def redeem(self, role: Role, owner: str, code: object, now: float) -> str:
        invite = self.codes.get(code) if isinstance(code, str) else None
        if not invite or invite[0] == role or invite[2] <= now:
            raise web.HTTPUnauthorized()
        other = invite[1]
        host, phone = (owner, other) if role == "host" else (other, owner)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO binding VALUES (?,?)", (host, phone))
        del self.codes[code]
        return other

    def peers(self, role: Role, owner: str) -> list[str]:
        sql = ("SELECT phone FROM binding WHERE host=? ORDER BY phone" if role == "host"
               else "SELECT host FROM binding WHERE phone=? ORDER BY host")
        return [row[0] for row in self.db.execute(sql, (owner,))]

    def contains(self, host: str, phone: str) -> bool:
        return self.db.execute("SELECT 1 FROM binding WHERE host=? AND phone=?", (host, phone)).fetchone() is not None

    def remove(self, host: str, phone: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM binding WHERE host=? AND phone=?", (host, phone))
        self.clear_codes("host", host)
        self.clear_codes("phone", phone)
