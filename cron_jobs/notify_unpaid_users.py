# cron_jobs/notify_unpaid_users.py

from __future__ import annotations

from typing import Optional

import db
from wa_client import send_wa, WhatsAppError


def is_valid_wa(number: Optional[str], return_clean: bool = False):
    """
    Validator + normalisasi sederhana nomor WhatsApp.

    - Menghapus spasi, titik, dan tanda hubung.
    - Menerima awalan '+', '0', atau '8', dikonversi ke format '62...'.
    - Panjang valid 10–15 digit.
    - Jika return_clean=True, kembalikan string nomor hasil normalisasi.
    - Jika return_clean=False, kembalikan True/False.
    """
    if not number:
        return None if return_clean else False

    # ambil digit & '+' saja
    s = "".join(ch for ch in number.strip() if ch.isdigit() or ch == "+")
    if not s:
        return None if return_clean else False

    if s.startswith("+"):
        s = s[1:]
    if s.startswith("0"):
        s = "62" + s[1:]
    elif s.startswith("8"):
        s = "62" + s

    # hanya digit
    if not s.isdigit() or not (10 <= len(s) <= 15):
        return None if return_clean else False

    return s if return_clean else True




def format_rupiah(amount: int) -> str:
    """
    Format angka ke bentuk Rupiah sederhana.
    Contoh: 38500 -> '38.500'
    """
    return f"{amount:,}".replace(",", ".")


def notify_unpaid_users() -> None:
    print("=== Mulai kirim notifikasi pelanggan unpaid ===")

    # 1. Ambil reseller aktif
    resellers = db.query_all("""
        SELECT id, display_name, use_notifications, wa_number
        FROM resellers
        WHERE is_active = TRUE
    """)

    for r in resellers:
        # cek apakah reseller mengaktifkan fitur notifikasi
        if not r.get("use_notifications"):
            continue

        rid = r["id"]
        name = r["display_name"]
        reseller_wa = (r.get("wa_number") or "").strip()

        # kalau nomor WA reseller sendiri tidak valid, skip
        if not is_valid_wa(reseller_wa):
            print(f"⚠️ Reseller {name}: nomor WA reseller tidak valid, lewati.")
            continue

        # 2. Ambil daftar pelanggan yang belum bayar (tanpa filter wa_number)
        unpaid = db.query_all("""
            SELECT ppp_username, full_name, wa_number, monthly_price
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
        """, {"rid": rid})

        if not unpaid:
            continue

        print(f"Reseller {name}: kirim notifikasi ke {len(unpaid)} pelanggan.")

        # 3. Kirim pesan ke tiap pelanggan
        for u in unpaid:
            customer_wa = (u.get("wa_number") or "").strip()

            # Tentukan tujuan:
            # - jika WA customer valid, kirim ke customer
            # - jika tidak, kirim ke WA reseller (fallback)
            target_wa = reseller_wa
            target_info = "reseller (fallback)"
            # if is_valid_wa(customer_wa):
            #     target_wa = customer_wa
            #     target_info = "customer"
            # else:
            #     target_wa = reseller_wa
            #     target_info = "reseller (fallback)"

            nama_pelanggan = (u.get("full_name") or "").strip() or u["ppp_username"]
            nominal = format_rupiah(int(u["monthly_price"]))

            msg = (
                f"Halo {nama_pelanggan},\n"
                f"Tagihan bulan ini sebesar Rp {nominal} belum terbayar.\n"
                f"Segera lakukan pembayaran agar layanan tetap aktif.\n"
                f"Terima kasih.\n"
                f"- {name}"
            )

            try:
                send_wa(target_wa, msg)
                print(f"✅ Berhasil kirim ke {target_wa} ({target_info}).")
            except WhatsAppError as e:
                print(f"❌ Gagal kirim ke {target_wa} ({target_info}): {e}")
            except Exception as e:
                # supaya error tak terduga tidak menghentikan loop reseller lain
                print(f"❌ Error tak terduga saat kirim ke {target_wa}: {e}")

    print("=== Selesai kirim notifikasi pelanggan unpaid ===")


if __name__ == "__main__":
    notify_unpaid_users()
