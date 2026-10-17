"""Persistencia en SQLite (WAL). Una conexion compartida protegida por un lock."""

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signatures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    ip_hash TEXT NOT NULL,
    net_hash TEXT NOT NULL,
    device_hash TEXT NOT NULL,
    phash TEXT NOT NULL,
    stroke_stats TEXT NOT NULL,
    strokes_json TEXT,
    public_wall INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    png BLOB,
    webhook_sent INTEGER NOT NULL DEFAULT 0,
    webhook_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sig_ip ON signatures(ip_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_sig_net ON signatures(net_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_sig_device ON signatures(device_hash, created_at);
CREATE INDEX IF NOT EXISTS idx_sig_status ON signatures(status, created_at);
CREATE INDEX IF NOT EXISTS idx_sig_pending ON signatures(webhook_sent, status);

CREATE TABLE IF NOT EXISTS used_nonces (
    nonce TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS banned_nets (
    net_hash TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reactions (
    message_id TEXT NOT NULL,
    device_hash TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (message_id, device_hash)
);
CREATE INDEX IF NOT EXISTS idx_react_ip ON reactions(message_id, ip_hash);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DAY = 86400


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._accepted_cache: int | None = None

    def close(self):
        with self._lock:
            self._conn.close()

    def _one(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _exec(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    # ---- meta ----
    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        self._exec("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ---- contador ----
    def count_accepted(self) -> int:
        if self._accepted_cache is None:
            self._accepted_cache = self._one("SELECT COUNT(*) AS n FROM signatures WHERE status = 'accepted'")["n"]
        return self._accepted_cache

    def invalidate_count(self):
        self._accepted_cache = None

    def last_signed_at(self) -> int | None:
        row = self._one("SELECT MAX(created_at) AS t FROM signatures WHERE status = 'accepted'")
        return row["t"]

    def accepted_since(self, since: int) -> int:
        return self._one("SELECT COUNT(*) AS n FROM signatures WHERE status = 'accepted' AND created_at >= ?", (since,))["n"]

    # ---- limites ----
    def count_by(self, column: str, value: str, since: int, statuses=("accepted", "flagged")) -> int:
        if column not in {"ip_hash", "net_hash", "device_hash"}:
            raise ValueError(column)
        marks = ",".join("?" * len(statuses))
        row = self._one(
            f"SELECT COUNT(*) AS n FROM signatures WHERE {column} = ? AND created_at >= ? AND status IN ({marks})",
            (value, since, *statuses),
        )
        return row["n"]

    def recent_phashes(self, ip_hash: str, net_hash: str, since: int) -> list[str]:
        rows = self._all(
            "SELECT phash FROM signatures WHERE (ip_hash = ? OR net_hash = ?) AND created_at >= ?",
            (ip_hash, net_hash, since),
        )
        return [r["phash"] for r in rows]

    def is_banned(self, net_hash: str) -> bool:
        return self._one("SELECT 1 FROM banned_nets WHERE net_hash = ?", (net_hash,)) is not None

    def ban_net(self, net_hash: str):
        self._exec("INSERT OR IGNORE INTO banned_nets(net_hash, created_at) VALUES(?, ?)", (net_hash, int(time.time())))

    # ---- nonces ----
    def use_nonce(self, nonce: str, expires_at: int) -> bool:
        """Marca el nonce como usado. Devuelve False si ya lo estaba."""
        try:
            self._exec("INSERT INTO used_nonces(nonce, expires_at) VALUES(?, ?)", (nonce, expires_at))
            return True
        except sqlite3.IntegrityError:
            return False

    def purge_nonces(self, now: int):
        self._exec("DELETE FROM used_nonces WHERE expires_at < ?", (now,))

    # ---- firmas ----
    def insert_signature(self, *, ip_hash, net_hash, device_hash, phash, stroke_stats, strokes_json, public_wall, status, png) -> int:
        cur = self._exec(
            """INSERT INTO signatures(created_at, ip_hash, net_hash, device_hash, phash, stroke_stats,
                                      strokes_json, public_wall, status, png)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(time.time()), ip_hash, net_hash, device_hash, phash, stroke_stats,
             strokes_json, int(public_wall), status, png),
        )
        self.invalidate_count()
        return cur.lastrowid

    def set_status(self, sig_id: int, status: str) -> bool:
        cur = self._exec("UPDATE signatures SET status = ? WHERE id = ?", (status, sig_id))
        self.invalidate_count()
        return cur.rowcount > 0

    def list_by_status(self, status: str, limit: int = 50):
        return self._all(
            "SELECT id, created_at, net_hash, phash, stroke_stats, public_wall FROM signatures WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )

    def stats(self) -> dict:
        rows = self._all("SELECT status, COUNT(*) AS n FROM signatures GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def public_wall(self, limit: int):
        return self._all(
            "SELECT id, created_at, strokes_json FROM signatures WHERE status = 'accepted' AND public_wall = 1 AND strokes_json IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    # ---- webhook ----
    def pending_webhook(self, status: str, limit: int = 10, max_attempts: int = 8):
        return self._all(
            "SELECT id, png FROM signatures WHERE status = ? AND webhook_sent = 0 AND png IS NOT NULL AND webhook_attempts < ? ORDER BY id LIMIT ?",
            (status, max_attempts, limit),
        )

    def mark_sent(self, ids: list[int]):
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        # Se borra el PNG una vez entregado: solo hacia falta para Discord.
        self._exec(f"UPDATE signatures SET webhook_sent = 1, png = NULL WHERE id IN ({marks})", ids)

    def bump_attempts(self, ids: list[int]):
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self._exec(f"UPDATE signatures SET webhook_attempts = webhook_attempts + 1 WHERE id IN ({marks})", ids)

    # ---- reacciones (solo 🔥) ----
    def reaction_counts(self) -> dict[str, int]:
        rows = self._all("SELECT message_id, COUNT(*) AS n FROM reactions GROUP BY message_id")
        return {r["message_id"]: r["n"] for r in rows}

    def reaction_count(self, message_id: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM reactions WHERE message_id = ?", (message_id,))["n"]

    def reactions_from_ip(self, message_id: str, ip_hash: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM reactions WHERE message_id = ? AND ip_hash = ?", (message_id, ip_hash))["n"]

    def has_reaction(self, message_id: str, device_hash: str) -> bool:
        return self._one("SELECT 1 FROM reactions WHERE message_id = ? AND device_hash = ?", (message_id, device_hash)) is not None

    def set_reaction(self, message_id: str, device_hash: str, ip_hash: str, on: bool):
        if on:
            self._exec("INSERT OR IGNORE INTO reactions(message_id, device_hash, ip_hash, created_at) VALUES(?, ?, ?, ?)",
                       (message_id, device_hash, ip_hash, int(time.time())))
        else:
            self._exec("DELETE FROM reactions WHERE message_id = ? AND device_hash = ?", (message_id, device_hash))
