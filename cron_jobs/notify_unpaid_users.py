# cron_jobs/notify_unpaid_users.py

from __future__ import annotations

from typing import Optional

import db
from wa_client import send_wa, WhatsAppError   
import datetime
import time
import pytz
from pathlib import Path
import random
import argparse
import sys

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


def notify_unpaid_users(force=False) -> None:
    tz = pytz.timezone("Asia/Jakarta")
    now = datetime.datetime.now(tz)

    # Jalankan hanya tanggal 25, kecuali pakai --force
    if not force and now.day != 25:
        print(f"[{now:%Y-%m-%d %H:%M:%S}] ⏸️ Skip: Hari ini tanggal {now.day}, bukan 25 (gunakan --force untuk override).")
        return

    # Cegah pengiriman ganda di hari yang sama
    flag_file = Path("/tmp/notify_unpaid_flag.txt")
    if not force and flag_file.exists():
        mtime = datetime.datetime.fromtimestamp(flag_file.stat().st_mtime, tz)
        if mtime.date() == now.date():
            print(f"[{now:%Y-%m-%d %H:%M:%S}] ⏸️ Notifikasi tanggal {mtime.date()} sudah dikirim, skip ulang.")
            return

    print(f"[{now:%Y-%m-%d %H:%M:%S}] ✅ Mulai kirim notifikasi pelanggan unpaid{' (FORCED)' if force else ''}.\n")

    resellers = db.query_all("""
        SELECT id, display_name, use_notifications, wa_number
        FROM resellers
        WHERE is_active = TRUE
    """)

    total_sent = 0
    batch_size = 20       # kirim 20 pesan dulu
    delay_seconds = 10    # istirahat 10 detik antar batch

    for r in resellers:
        if not r.get("use_notifications"):
            continue

        rid = r["id"]
        reseller_name = r["display_name"]
        reseller_wa = is_valid_wa(r.get("wa_number") or "", return_clean=True)
        if not reseller_wa:
            print(f"⚠️ Reseller {reseller_name}: nomor WA reseller tidak valid, lewati.\n")
            continue

        unpaid = db.query_all("""
            SELECT ppp_username, full_name, wa_number, monthly_price
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
        """, {"rid": rid})

        if not unpaid:
            print(f"✅ {reseller_name}: semua pelanggan sudah bayar.\n")
            continue

        print(f"🔔 {reseller_name}: {len(unpaid)} pelanggan belum bayar.")

        for i, u in enumerate(unpaid, start=1):
            customer_wa = is_valid_wa(u.get("wa_number") or "", return_clean=True)

            # --- blok siap pakai: aktifkan kirim langsung ke pelanggan ---
            # if is_valid_wa(customer_wa):
            #     target_wa = customer_wa
            #     target_info = "customer"
            # else:
            #     target_wa = reseller_wa
            #     target_info = "reseller (fallback)"
            # ------------------------------------------------------------
            
            # default: kirim ke reseller saja
            target_wa = reseller_wa
            target_info = "reseller (default)"

            nama = (u.get("full_name") or "").strip() or u["ppp_username"]
            nominal = format_rupiah(int(u["monthly_price"]))
            msg = (
                f"Halo {nama}, 👋\n\n"
                f"Tagihan internet Anda bulan ini sebesar *Rp {nominal}* belum terbayar.\n"
                f"Segera lakukan pembayaran agar layanan tetap aktif.\n\n"
                f"Terima kasih 🙏\n"
                f"- {reseller_name}"
            )

            try:
                send_wa(target_wa, msg)
                print(f"✅ {i}/{len(unpaid)} Kirim ke {target_wa} ({target_info}) sukses.")
                total_sent += 1
            except Exception as e:
                print(f"❌ {i}/{len(unpaid)} Gagal kirim ke {target_wa}: {e}")

            # jeda acak antar kirim (0.5–2 detik)
            time.sleep(random.uniform(0.5, 2.0))

            # delay antar batch besar
            if i % batch_size == 0:
                print(f"⏳ Istirahat {delay_seconds} detik... (batch ke-{i // batch_size})")
                time.sleep(delay_seconds)

        print(f"✅ Selesai kirim untuk reseller {reseller_name}.\n")

    if not force:
        flag_file.touch()

    print(f"🎯 Total pesan terkirim: {total_sent}")
    print(f"[{datetime.datetime.now(tz):%Y-%m-%d %H:%M:%S}] ✅ Semua notifikasi selesai.")




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kirim notifikasi pelanggan unpaid.")
    parser.add_argument("--force", action="store_true", help="Jalankan meskipun bukan tanggal 25 atau sudah pernah kirim.")
    args = parser.parse_args()

    try:
        notify_unpaid_users(force=args.force)
    except KeyboardInterrupt:
        print("\n🛑 Dibatalkan oleh pengguna.")
        sys.exit(0)

