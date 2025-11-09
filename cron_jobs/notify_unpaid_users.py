# cron_jobs/notify_unpaid_users.py
import db
from wa_client import send_wa, WhatsAppError

def is_valid_wa(number: str) -> bool:
    """
    Validator sederhana nomor WhatsApp.
    Silakan sesuaikan aturan validasi dengan kebutuhanmu.
    Contoh aturan:
    - hanya angka (boleh diawali +, nanti dibuang)
    - panjang 10–15 digit
    - diawali '62' (optional, kalau mau strict Indonesia)
    """
    if not number:
        return False

    number = number.strip()

    # buang plus di depan kalau ada
    if number.startswith("+"):
        number = number[1:]

    # cek hanya digit
    if not number.isdigit():
        return False

    # cek panjang
    if not (10 <= len(number) <= 15):
        return False

    # kalau mau wajib Indonesia:
    # if not number.startswith("62"):
    #     return False

    return True


def notify_unpaid_users():
    resellers = db.query_all("""
        SELECT id, display_name, use_notifications, wa_number
        FROM resellers
        WHERE is_active = TRUE
    """)

    for r in resellers:
        if not r["use_notifications"]:
            continue

        rid = r["id"]
        name = r["display_name"]
        reseller_wa = (r.get("wa_number") or "").strip()

        # kalau nomor WA reseller sendiri nggak valid, percuma kirim fallback
        if not is_valid_wa(reseller_wa):
            print(f"Reseller {name}: nomor WA reseller tidak valid, lewati.")
            continue

        # HAPUS filter wa_number di WHERE supaya tetap ambil customer yang kosong
        unpaid = db.query_all("""
            SELECT ppp_username, full_name, wa_number, monthly_price
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
        """, {"rid": rid})

        if not unpaid:
            continue

        print(f"Reseller {name}: kirim notifikasi ke {len(unpaid)} pelanggan.")

        for u in unpaid:
            customer_wa = (u.get("wa_number") or "").strip()

            # tentukan tujuan: pakai WA customer kalau valid, kalau tidak pakai WA reseller
            if is_valid_wa(customer_wa):
                target_wa = customer_wa
                target_info = "customer"
            else:
                target_wa = reseller_wa
                target_info = "reseller (fallback)"

            msg = (
                f"Halo {u.get('full_name') or u['ppp_username']},\n"
                f"Tagihan bulan ini sebesar Rp {u['monthly_price']:,} belum terbayar.\n"
                f"Segera lakukan pembayaran agar layanan tetap aktif.\n"
                f"Terima kasih.\n"
                f"- {name}"
            )

            try:
                send_wa(target_wa, msg)
                print(f"Berhasil kirim ke {target_wa} ({target_info}).")
            except WhatsAppError as e:
                print(f"Gagal kirim ke {target_wa} ({target_info}): {e}")

if __name__ == "__main__":
    notify_unpaid_users()
