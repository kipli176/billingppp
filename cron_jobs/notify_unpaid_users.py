# cron_jobs/notify_unpaid_users.py

from __future__ import annotations

from typing import Optional

import db
from wa_client import send_wa, WhatsAppError


def is_valid_wa(number: Optional[str]) -> bool:
    """
    Validator sederhana nomor WhatsApp.

    Aturan:
    - Boleh diawali dengan '+' (akan dibuang)
    - Hanya digit
    - Panjang 10–15 digit
    - (Opsional) bisa dipaksa mulai dengan '62' kalau mau strict Indonesia
    """
    if not number:
        return False

    number = number.strip()
    if not number:
        return False

    # buang plus di depan kalau ada
    if number.startswith("+"):
        number = number[1:]

    # cek hanya digit
    if not number.isdigit():
        return False

    # cek panjang
    if not (10 <= len(number) <= 15):
        return False

    # Kalau mau wajib Indonesia, aktifkan ini:
    # if not number.startswith("62"):
    #     return False

    return True


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
