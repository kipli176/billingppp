# cron_jobs/generate_reseller_invoices.py
import datetime
import db
from wa_client import send_wa, WhatsAppError  # <— tambahkan ini

TODAY = datetime.date.today()
PERIOD_START = TODAY.replace(day=1)
PERIOD_END = (PERIOD_START + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
DUE_DATE = PERIOD_START + datetime.timedelta(days=10)  # jatuh tempo tgl 10


def generate_invoices():
    # ambil wa_number juga biar bisa kirim notif
    resellers = db.query_all("""
        SELECT id, display_name, use_notifications, use_auto_payment, wa_number
        FROM resellers
        WHERE is_active = TRUE
    """)

    for r in resellers:
        rid = r["id"]
        name = r["display_name"]
        use_notif = r["use_notifications"]
        use_auto = r["use_auto_payment"]
        wa_number = (r.get("wa_number") or "").strip()

        price = 250
        if use_notif and not use_auto:
            price = 500
        elif use_notif and use_auto:
            price = 1000

        # cek apakah sudah ada invoice bulan ini
        exist = db.query_one("""
            SELECT 1 FROM reseller_invoices
            WHERE reseller_id=%(rid)s
              AND period_start=%(start)s
        """, {"rid": rid, "start": PERIOD_START})
        if exist:
            print(f"Reseller {rid} sudah ada invoice bulan ini, skip.")
            continue

        enabled_count = db.query_one("""
            SELECT COUNT(*) AS c FROM ppp_customers
            WHERE reseller_id=%(rid)s AND is_enabled=TRUE
        """, {"rid": rid})["c"]

        total = enabled_count * price

        db.execute("""
            INSERT INTO reseller_invoices
                (reseller_id, period_start, period_end,
                 total_enabled_users, price_per_user, total_amount,
                 use_notifications, use_auto_payment,
                 status, due_date, created_at, updated_at)
            VALUES
                (%(rid)s, %(ps)s, %(pe)s,
                 %(c)s, %(p)s, %(t)s,
                 %(n)s, %(a)s,
                 'pending', %(due)s, NOW(), NOW())
        """, {
            "rid": rid,
            "ps": PERIOD_START,
            "pe": PERIOD_END,
            "c": enabled_count,
            "p": price,
            "t": total,
            "n": use_notif,
            "a": use_auto,
            "due": DUE_DATE,
        })

        print(f"✅ Invoice dibuat untuk reseller {name} total Rp{total:,}")

        # === KIRIM WHATSAPP KE RESELLER BAHWA TAGIHAN SUDAH TERBIT ===
        # kirim WA hanya kalau:
        # - reseller mengaktifkan notifikasi
        # - wa_number tidak kosong
        if use_notif and wa_number:
            period_label = PERIOD_START.strftime("%B %Y")  # misal 'November 2025'
            due_label = DUE_DATE.strftime("%d-%m-%Y")

            msg = (
                f"Halo {name},\n\n"
                f"Tagihan untuk periode {period_label} sudah terbit.\n"
                f"Total user aktif: {enabled_count}\n"
                f"Harga per user: Rp {price:,}\n"
                f"Total tagihan: Rp {total:,}\n"
                f"Jatuh tempo: {due_label}.\n\n"
                f"Terima kasih."
            )

            try:
                send_wa(wa_number, msg)
                print(f"📲 Notifikasi invoice dikirim ke {wa_number} (reseller {name})")
            except WhatsAppError as e:
                print(f"⚠️ Gagal kirim WA ke reseller {name} ({wa_number}): {e}")


if __name__ == "__main__":
    generate_invoices()



# # Generate invoice tiap tgl 5 jam 02:00
# 0 2 5 * * /usr/bin/python3 /opt/billing/cron_jobs/generate_reseller_invoices.py >> /opt/billing/logs/invoice.log 2>&1

# # Kirim notifikasi WA tgl 20 jam 08:00
# 0 8 20 * * /usr/bin/python3 /opt/billing/cron_jobs/notify_unpaid_users.py >> /opt/billing/logs/wa_notify.log 2>&1

# # Isolir user unpaid tgl 25 jam 03:00
# 0 3 25 * * /usr/bin/python3 /opt/billing/cron_jobs/isolate_unpaid_users.py >> /opt/billing/logs/isolate.log 2>&1
