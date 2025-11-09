"""
billing_logic.py
----------------
Berisi logika bisnis billing:
- generate invoice reseller untuk periode berjalan
- ambil user belum bayar untuk notifikasi/isolate
- update last_paid_period ketika user bayar N bulan

Sebagian logika memanfaatkan view yang sudah dibuat di database:
- v_customers
- v_unpaid_customers_current_period
- v_reseller_unpaid_summary
- v_payment_status_detail
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import db


# -----------------------------------------------------------------------------
# Helper tanggal
# -----------------------------------------------------------------------------

def get_current_period() -> date:
    """
    Mengembalikan tanggal 1 bulan berjalan.
    """
    today = date.today()
    return today.replace(day=1)


def add_months(base: date, months: int) -> date:
    """
    Tambah sejumlah bulan ke sebuah tanggal (mengabaikan hari, diset 1).
    Dipakai untuk menghitung last_paid_period baru.

    Contoh:
      add_months(date(2025, 10, 1), 2) -> 2025-12-01
    """
    if months == 0:
        return base.replace(day=1)

    month = base.month - 1 + months
    year = base.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


# -----------------------------------------------------------------------------
# Generate invoice reseller (dipanggil cron tanggal 5)
# -----------------------------------------------------------------------------

def generate_reseller_invoices_for_current_period() -> int:
    """
    Jalankan query generate invoice reseller untuk periode bulan berjalan.

    Mengembalikan jumlah invoice yang berhasil diinsert.
    """
    sql = """
    WITH period AS (
        SELECT
            date_trunc('month', CURRENT_DATE)::date AS period_start,
            (
                date_trunc('month', CURRENT_DATE)
                + INTERVAL '1 month - 1 day'
            )::date AS period_end
    ),
    enabled_counts AS (
        SELECT
            reseller_id,
            COUNT(*) AS total_enabled_users
        FROM ppp_customers
        WHERE is_enabled = TRUE
        GROUP BY reseller_id
    )
    INSERT INTO reseller_invoices (
        reseller_id,
        period_start,
        period_end,
        total_enabled_users,
        price_per_user,
        total_amount,
        use_notifications,
        use_auto_payment,
        status,
        due_date,
        created_at,
        updated_at
    )
    SELECT
        r.id AS reseller_id,
        p.period_start,
        p.period_end,
        COALESCE(ec.total_enabled_users, 0) AS total_enabled_users,

        CASE
            WHEN r.use_notifications = FALSE AND r.use_auto_payment = FALSE THEN 250
            WHEN r.use_notifications = TRUE  AND r.use_auto_payment = FALSE THEN 500
            WHEN r.use_notifications = TRUE  AND r.use_auto_payment = TRUE  THEN 1000
            ELSE 1000
        END AS price_per_user,

        COALESCE(ec.total_enabled_users, 0) *
        CASE
            WHEN r.use_notifications = FALSE AND r.use_auto_payment = FALSE THEN 250
            WHEN r.use_notifications = TRUE  AND r.use_auto_payment = FALSE THEN 500
            WHEN r.use_notifications = TRUE  AND r.use_auto_payment = TRUE  THEN 1000
            ELSE 1000
        END AS total_amount,

        r.use_notifications,
        r.use_auto_payment,

        'pending'::invoice_status AS status,

        CURRENT_DATE AS due_date,

        NOW() AS created_at,
        NOW() AS updated_at

    FROM resellers r
    CROSS JOIN period p
    LEFT JOIN enabled_counts ec
        ON ec.reseller_id = r.id

    WHERE
        r.is_active = TRUE
        AND COALESCE(ec.total_enabled_users, 0) > 0
        AND NOT EXISTS (
            SELECT 1
            FROM reseller_invoices ri
            WHERE ri.reseller_id = r.id
              AND ri.period_start = p.period_start
        );
    """
    return db.execute(sql)


# -----------------------------------------------------------------------------
# Ambil user yang belum bayar (untuk notifikasi / isolasi)
# -----------------------------------------------------------------------------

def get_unpaid_customers_for_notifications(reseller_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Ambil list user yang BELUM bayar bulan ini & boleh dikirimi WA.

    Data berasal dari view v_unpaid_customers_current_period JOIN resellers
    (hanya reseller yang use_notifications = TRUE).
    """
    sql = """
    SELECT
        v.customer_id,
        v.reseller_id,
        v.reseller_name,
        v.ppp_username,
        v.full_name,
        v.wa_number,
        v.petugas_name,
        v.profile_name,
        v.monthly_price,
        v.payment_status_text
    FROM v_unpaid_customers_current_period v
    JOIN resellers r ON r.id = v.reseller_id
    WHERE
        r.is_active = TRUE
        AND r.use_notifications = TRUE
        AND v.is_enabled = TRUE
        AND v.wa_number IS NOT NULL
        AND (%(reseller_id)s IS NULL OR v.reseller_id = %(reseller_id)s)
    ORDER BY v.reseller_id, v.ppp_username;
    """
    return db.query_all(sql, {"reseller_id": reseller_id})


def get_customers_to_isolate(reseller_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Ambil list user yang HARUS diisolir (dipakai cron tanggal 25).

    Basis data: v_customers (kolom should_isolate_current_period = TRUE).
    """
    sql = """
    SELECT
        v.customer_id,
        v.reseller_id,
        v.reseller_name,
        v.ppp_username,
        v.full_name,
        v.wa_number,
        v.profile_id,
        v.profile_name,
        v.payment_status_text
    FROM v_customers v
    JOIN resellers r ON r.id = v.reseller_id
    WHERE
        r.is_active = TRUE
        AND v.should_isolate_current_period = TRUE
        AND (%(reseller_id)s IS NULL OR v.reseller_id = %(reseller_id)s)
    ORDER BY v.reseller_id, v.ppp_username;
    """
    return db.query_all(sql, {"reseller_id": reseller_id})


# -----------------------------------------------------------------------------
# Update last_paid_period saat user bayar N bulan
# -----------------------------------------------------------------------------

def mark_customer_paid(customer_id: int, months_paid: int) -> Optional[date]:
    """
    Tandai user telah membayar N bulan (months_paid >= 1).

    Logika:
    - Ambil billing_start_date & last_paid_period dari ppp_customers
    - current_period = awal bulan sekarang
    - first_unpaid_period:
        * jika last_paid_period tidak NULL -> last_paid_period + 1 bulan
        * else jika billing_start_date ada -> awal bulan billing_start_date
        * else -> current_period
    - new_last_paid_period = first_unpaid_period + (months_paid - 1) bulan
    - UPDATE ppp_customers.last_paid_period

    Mengembalikan new_last_paid_period, atau None kalau customer tidak ditemukan.
    """
    if months_paid <= 0:
        raise ValueError("months_paid harus >= 1")

    row = db.query_one(
        """
        SELECT billing_start_date, last_paid_period
        FROM ppp_customers
        WHERE id = %(cid)s
        """,
        {"cid": customer_id},
    )
    if row is None:
        return None

    billing_start_date: Optional[date] = row["billing_start_date"]
    last_paid_period: Optional[date] = row["last_paid_period"]
    current_period = get_current_period()

    # Tentukan first_unpaid_period
    if last_paid_period is not None:
        first_unpaid = add_months(last_paid_period, 1)
    elif billing_start_date is not None:
        first_unpaid = billing_start_date.replace(day=1)
    else:
        first_unpaid = current_period

    # Hitung new_last_paid_period
    new_last_paid = add_months(first_unpaid, months_paid - 1)

    db.execute(
        """
        UPDATE ppp_customers
        SET last_paid_period = %(new_last_paid)s,
            updated_at = NOW()
        WHERE id = %(cid)s
        """,
        {"new_last_paid": new_last_paid, "cid": customer_id},
    )

    return new_last_paid
