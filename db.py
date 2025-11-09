"""
db.py
------
Helper koneksi Postgres menggunakan psycopg2 connection pool.

Menyediakan fungsi:
- init_app(app)    : dipanggil sekali di create_app()
- query_one(sql, params)
- query_all(sql, params)
- execute(sql, params)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from flask import current_app, g

_connection_pool: Optional[pool.SimpleConnectionPool] = None


def init_app(app):
    """
    Inisialisasi connection pool waktu Flask app dibuat.
    Dipanggil dari app.py -> create_app().
    """
    global _connection_pool

    dsn = app.config.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL belum diset di konfigurasi.")

    _connection_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=dsn,
    )

    @app.teardown_appcontext
    def _close_db_connection(exception=None):
        """
        Setiap akhir request: kembalikan koneksi ke pool.
        """
        conn = g.pop("db_conn", None)
        if conn is not None and _connection_pool is not None:
            _connection_pool.putconn(conn)


def _get_conn():
    """
    Ambil koneksi dari pool, disimpan di flask.g selama 1 request.
    """
    if _connection_pool is None:
        raise RuntimeError("Connection pool belum diinisialisasi. Panggil db.init_app(app) dulu.")

    if "db_conn" not in g:
        g.db_conn = _connection_pool.getconn()
    return g.db_conn


def query_one(sql: str, params: Dict[str, Any] | Tuple | None = None) -> Optional[Dict[str, Any]]:
    """
    Eksekusi SELECT dan ambil 1 baris (dict) atau None.
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or {})
        row = cur.fetchone()
    return row


def query_all(sql: str, params: Dict[str, Any] | Tuple | None = None) -> List[Dict[str, Any]]:
    """
    Eksekusi SELECT dan ambil semua baris (list of dict).
    """
    conn = _get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or {})
        rows = cur.fetchall()
    return list(rows)


def execute(sql: str, params: Dict[str, Any] | Tuple | None = None, commit: bool = True) -> int:
    """
    Eksekusi INSERT / UPDATE / DELETE.
    Mengembalikan jumlah row yang terpengaruh.
    """
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        rowcount = cur.rowcount
    if commit:
        conn.commit()
    return rowcount
