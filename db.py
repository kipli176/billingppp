"""
db.py
------
Helper koneksi Postgres menggunakan psycopg2 connection pool.

Menyediakan fungsi:
- init_app(app=None)
- query_one(sql, params)
- query_all(sql, params)
- execute(sql, params, commit=True)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config import Config

# Pool koneksi global
_DB_POOL: Optional[pool.SimpleConnectionPool] = None


def init_app(app=None, minconn: int = 1, maxconn: int = 10) -> None:
    """
    Inisialisasi connection pool.

    - Jika dipanggil dari Flask: kirim `app`, dan akan baca app.config["DATABASE_URL"].
    - Jika dipanggil dari script/cron: cukup `init_app()` tanpa argumen,
      akan pakai Config.DATABASE_URL (dari .env / environment).
    """
    global _DB_POOL
    if _DB_POOL is not None:
        # Sudah di-init, tidak perlu diulang
        return

    dsn: Optional[str] = None

    # Prioritas 1: app.config kalau ada
    if app is not None and getattr(app, "config", None):
        dsn = app.config.get("DATABASE_URL")

    # Prioritas 2: Config.DATABASE_URL
    if not dsn:
        dsn = getattr(Config, "DATABASE_URL", None)

    if not dsn:
        raise RuntimeError(
            "DATABASE_URL belum diset. Pastikan environment / .env berisi DATABASE_URL."
        )

    _DB_POOL = pool.SimpleConnectionPool(minconn, maxconn, dsn)


def _get_conn():
    """
    Ambil 1 koneksi dari pool.
    Jika pool belum dibuat, otomatis panggil init_app() (lazy init).
    """
    global _DB_POOL
    if _DB_POOL is None:
        # lazy init: baca dari Config / .env
        init_app()

    if _DB_POOL is None:
        # Kalau masih None berarti init gagal
        raise RuntimeError(
            "Connection pool belum diinisialisasi dan init_app() gagal. "
            "Periksa konfigurasi DATABASE_URL."
        )

    return _DB_POOL.getconn()


def _put_conn(conn) -> None:
    """
    Kembalikan koneksi ke pool.
    """
    global _DB_POOL
    if _DB_POOL is not None:
        _DB_POOL.putconn(conn)
    else:
        # fallback keamanan
        conn.close()


def close_all() -> None:
    """
    Tutup semua koneksi di pool (opsional, dipakai saat shutdown).
    """
    global _DB_POOL
    if _DB_POOL is not None:
        _DB_POOL.closeall()
        _DB_POOL = None


ParamsType = Union[Dict[str, Any], Sequence[Any], None]


def query_one(sql: str, params: ParamsType = None) -> Optional[Dict[str, Any]]:
    """
    Jalankan SELECT dan ambil 1 row (atau None).
    Mengembalikan dict: field_name -> value.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        _put_conn(conn)


def query_all(sql: str, params: ParamsType = None) -> List[Dict[str, Any]]:
    """
    Jalankan SELECT dan ambil semua row.
    Mengembalikan list of dict.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            rows = cur.fetchall()
        # pastikan list of plain dict, bukan RealDictRow
        return [dict(r) for r in rows]
    finally:
        _put_conn(conn)


def execute(
    sql: str,
    params: ParamsType = None,
    commit: bool = True,
) -> int:
    """
    Jalankan INSERT / UPDATE / DELETE.
    Mengembalikan jumlah row yang terpengaruh.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            rowcount = cur.rowcount
        if commit:
            conn.commit()
        return rowcount
    finally:
        _put_conn(conn)
