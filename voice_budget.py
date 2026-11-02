"""Durable, conservative per-day reservation for paid and sent voice attempts."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import uuid


class Ticket:
    def __init__(self, store, token, day):
        self.store, self.token, self.day, self.done = store, token, day, False

    def commit(self):
        # Reserved row is already durable before any provider request.
        self.done = True

    def release(self):
        if not self.done:
            try:
                with closing(sqlite3.connect(self.store.path, timeout=0.2)) as db:
                    db.execute('DELETE FROM reservations WHERE token=?', (self.token,))
                    db.commit()
            except sqlite3.Error:
                # Fail closed: the reserved use stays consumed.
                pass
            self.done = True


class Budget:
    def __init__(self, path, category, limit):
        self.path = str(path)
        self.category = category
        self.limit = int(limit)

    def exhausted(self):
        """今天是否已经用满；读不了库时按用满算（和 reserve 一样宁可少发）。"""
        if self.limit <= 0 or self.limit > 1000:
            return True
        if not Path(self.path).is_file():
            return False
        try:
            with closing(sqlite3.connect(self.path, timeout=0.2)) as db:
                used = db.execute('SELECT COUNT(*) FROM reservations WHERE day=? AND category=?',
                                  (time.strftime('%Y-%m-%d'), self.category)).fetchone()[0]
        except sqlite3.Error:
            return True
        return used >= self.limit

    def reserve(self, *bounds):
        # Bounds are fixed by the caller's hard max (120 chars, 512 output tokens).
        if self.limit <= 0 or self.limit > 1000:
            return None
        try:
            path = Path(self.path)
            if not path.is_absolute() or not path.parent.is_dir():
                return None
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(self.path, timeout=0.2, isolation_level=None)) as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE IF NOT EXISTS reservations (token TEXT PRIMARY KEY, day TEXT NOT NULL, category TEXT NOT NULL)')
                db.execute('BEGIN IMMEDIATE')
                day = time.strftime('%Y-%m-%d')
                used = db.execute('SELECT COUNT(*) FROM reservations WHERE day=? AND category=?', (day, self.category)).fetchone()[0]
                if used >= self.limit:
                    db.execute('ROLLBACK')
                    return None
                token = uuid.uuid4().hex
                db.execute('INSERT INTO reservations VALUES (?, ?, ?)', (token, day, self.category))
                db.execute('COMMIT')
                return Ticket(self, token, day)
        except (OSError, sqlite3.Error, ValueError):
            return None
