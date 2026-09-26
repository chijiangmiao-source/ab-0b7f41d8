"""复核记录的 SQLite 持久化：提交后取得复核编号，刷新后可按编号回查。"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload   TEXT NOT NULL
)
"""


def _db_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "./data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "reviews.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(_SCHEMA)
    return conn


def init_db() -> None:
    with _LOCK:
        _connect().close()


def create_review(record: dict) -> str:
    """写入复核记录（含不可行结论），自动处理编号碰撞，返回复核编号。"""
    with _LOCK:
        conn = _connect()
        try:
            for _ in range(8):
                rid = "R-" + secrets.token_hex(5)
                payload = {**record, "review_id": rid}
                try:
                    conn.execute(
                        "INSERT INTO reviews (review_id, created_at, payload)"
                        " VALUES (?, ?, ?)",
                        (rid, record["created_at"], json.dumps(payload, ensure_ascii=False)),
                    )
                    conn.commit()
                    return rid
                except sqlite3.IntegrityError:
                    continue
            raise RuntimeError("无法分配复核编号")
        finally:
            conn.close()


def get_review(review_id: str) -> dict | None:
    with _LOCK:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT payload FROM reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        finally:
            conn.close()
    return json.loads(row[0]) if row else None
