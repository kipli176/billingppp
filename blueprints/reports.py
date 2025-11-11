# blueprints/reports.py

from __future__ import annotations

from flask import (
    Blueprint,
    session,
    redirect,
    url_for,
    request,
)

import db
from app import render_terminal_page
from wa_client import send_wa, WhatsAppError

bp = Blueprint("reports", __name__)


def _require_login():
    """
    Pastikan reseller sudah login.
    """
    reseller_id = session.get("reseller_id")
    if not reseller_id:
        return None, redirect(url_for("auth_reseller.login"))

    reseller = db.query_one(
        """
        SELECT id, display_name, router_username,
               wa_number, use_notifications,
               is_active
        FROM resellers
        WHERE id = %(rid)s
        """,
        {"rid": reseller_id},
    )

    if reseller is None or not reseller["is_active"]:
        session.clear()
        return None, redirect(url_for("auth_reseller.login"))

    return reseller, None


# ======================================================================
# REPORT: Unpaid Customers (current period)
# ======================================================================

@bp.route("/reports/unpaid-users", methods=["GET"])
def unpaid_users():
    """
    Laporan pelanggan yang belum bayar periode bulan ini.
    Data dari v_unpaid_customers_current_period.
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None

    rows = []
    db_error = None

    try:
        rows = db.query_all(
            """
            SELECT
              customer_id,
              ppp_username,
              full_name,
              wa_number,
              petugas_name,
              profile_name,
              monthly_price,
              current_period
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
            ORDER BY ppp_username
            """,
            {"rid": reseller["id"]},
        )
    except Exception as e:
        db_error = f"Gagal mengambil data unpaid: {e}"

    body_html = """
<!-- HEADER -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-center md:justify-between">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <span class="text-slate-300">Reports</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>📊</span>
      <span>Laporan Unpaid (Bulan Ini)</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Reseller:
      <span class="font-medium text-slate-200">{{ reseller_name }}</span>
    </p>
  </div>

    <div class="flex flex-wrap gap-2">
    <!-- Kirim WA ke pelanggan unpaid -->
    <!--form method="post"
          action="{{ url_for('reports.send_wa_unpaid') }}"
          class="inline-flex">
      <button type="submit"
              class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
        📲 <span>WA ke Pelanggan Unpaid</span>
      </button>
    </form-->

    <!-- Kirim ringkasan ke WA reseller -->
    <form method="post"
          action="{{ url_for('reports.send_wa_unpaid_summary') }}"
          class="inline-flex">
      <button type="submit"
              class="inline-flex items-center gap-1 rounded-md border border-sky-500 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-100 hover:bg-sky-500/20">
        📑 <span>WA Ringkasan ke Reseller</span>
      </button>
    </form>

    <a href="{{ url_for('main.dashboard') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      🏠 <span>Dashboard</span>
    </a>
  </div>

</section>

<!-- ALERTS -->
{% if error %}
  <div class="mt-4 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ {{ error }}
  </div>
{% endif %}
{% if db_error %}
  <div class="mt-3 rounded-md border border-amber-500/70 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
    ⚠️ {{ db_error }}
  </div>
{% endif %}
{% if success %}
  <div class="mt-3 rounded-md border border-emerald-500/70 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
    ✅ {{ success }}
  </div>
{% endif %}

<!-- TABEL LAPORAN -->
<section class="mt-4 rounded-lg border border-slate-800 bg-slate-900/70 p-3">
  <div class="mb-2 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-slate-200">Daftar Pelanggan Belum Bayar</h2>
    <span class="text-[11px] text-slate-500">
      Total: {{ rows|length }} pelanggan
    </span>
  </div>

  {% if rows %}
    <div class="overflow-x-auto">
      <table class="min-w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-slate-800 bg-slate-900">
            <th class="px-3 py-2 text-left font-medium text-slate-300">User</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">Nama</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">WA</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">Petugas</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">Profile</th>
            <th class="px-3 py-2 text-right font-medium text-slate-300">Tagihan</th>
          </tr>
        </thead>
        <tbody>
          {% for r in rows %}
          <tr class="border-b border-slate-800/70 hover:bg-slate-900/60">
            <td class="px-3 py-1.5 font-mono text-slate-100">
              {{ r.ppp_username }}
            </td>
            <td class="px-3 py-1.5 text-slate-100">
              {{ r.full_name or '-' }}
            </td>
            <td class="px-3 py-1.5 text-slate-200">
              {{ r.wa_number or '-' }}
            </td>
            <td class="px-3 py-1.5 text-slate-200">
              {{ r.petugas_name or '-' }}
            </td>
            <td class="px-3 py-1.5 text-slate-200">
              {{ r.profile_name or '-' }}
            </td>
            <td class="px-3 py-1.5 text-right text-amber-300">
              {{ '{:,.0f}'.format(r.monthly_price or 0) }}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="text-sm text-emerald-300">
      Semua pelanggan sudah bayar bulan ini 🎉
    </p>
  {% endif %}

  <p class="mt-3 text-[11px] text-slate-500">
    Catatan:<br>
    • Tombol <b>"Kirim WA ke Yang Belum Bayar"</b> akan mengirim pesan ke semua pelanggan yang punya nomor WA dan belum bayar bulan ini.<br>
    • Pengiriman hanya dilakukan jika <b>notifikasi WA</b> di pengaturan reseller dalam kondisi ON.
  </p>
</section>
    """

    return render_terminal_page(
        title="Laporan Unpaid",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "rows": rows,
            "error": error,
            "success": success,
            "db_error": db_error,
        },
    )


# ======================================================================
# ACTION: Kirim WA ke semua unpaid
# ======================================================================

@bp.route("/reports/unpaid-users/send-wa", methods=["POST"])
def send_wa_unpaid():
    """
    Kirim WhatsApp ke semua pelanggan yang belum bayar bulan ini.

    Syarat:
    - reseller.use_notifications = TRUE
    - wa_number pelanggan tidak kosong
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not reseller.get("use_notifications"):
        return redirect(
            url_for("reports.unpaid_users", error="Notifikasi WA belum diaktifkan di pengaturan reseller.")
        )

    # Ambil data unpaid
    try:
        rows = db.query_all(
            """
            SELECT
              ppp_username,
              full_name,
              wa_number,
              profile_name,
              monthly_price
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
              AND wa_number IS NOT NULL
              AND wa_number <> ''
            """,
            {"rid": reseller["id"]},
        )
    except Exception as e:
        return redirect(url_for("reports.unpaid_users", error=f"Gagal ambil data unpaid: {e}"))

    if not rows:
        return redirect(url_for("reports.unpaid_users", success="Tidak ada pelanggan unpaid yang punya nomor WA."))

    sukses = 0
    gagal = 0

    for r in rows:
        number = r["wa_number"]
        user = r["ppp_username"]
        name = r.get("full_name") or user
        profile_name = r.get("profile_name") or "-"
        price = r.get("monthly_price") or 0

        # Pesan WA sederhana (bisa kamu modif sesuka hati)
        message = (
            f"Halo {name},\n"
            f"Tagihan internet untuk akun *{user}* ({profile_name}) bulan ini belum tercatat lunas.\n"
            f"Total tagihan: Rp {price:,.0f}\n\n"
            f"Silakan melakukan pembayaran agar layanan tetap aktif.\n"
            f"Terima kasih.\n"
            f"- {reseller['display_name'] or reseller['router_username']}"
        )

        try:
            send_wa(number, message)
            sukses += 1
        except WhatsAppError as e:
            print(f"[send_wa_unpaid] gagal kirim WA ke {number}: {e}")
            gagal += 1

    msg = f"WA terkirim ke {sukses} pelanggan. Gagal: {gagal}."
    return redirect(url_for("reports.unpaid_users", success=msg))

# ======================================================================
# ACTION: Kirim ringkasan unpaid ke WA reseller
# ======================================================================

@bp.route("/reports/unpaid-users/wa-summary", methods=["POST"])
def send_wa_unpaid_summary():
    """
    Kirim satu pesan ringkasan ke nomor WA reseller,
    berisi list: nama, petugas, tagihan pelanggan yang belum bayar bulan ini.
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    wa_target = reseller.get("wa_number")
    if not wa_target:
        return redirect(
            url_for(
                "reports.unpaid_users",
                error="Nomor WA reseller belum diisi di Pengaturan Reseller.",
            )
        )

    # Ambil semua pelanggan unpaid (tidak perlu filter WA pelanggan,
    # karena pesan ditujukan ke reseller, bukan ke pelanggan langsung)
    try:
        rows = db.query_all(
            """
            SELECT
              full_name,
              petugas_name,
              monthly_price
            FROM v_unpaid_customers_current_period
            WHERE reseller_id = %(rid)s
            ORDER BY petugas_name NULLS LAST, full_name
            """,
            {"rid": reseller["id"]},
        )
    except Exception as e:
        return redirect(
            url_for("reports.unpaid_users", error=f"Gagal ambil data unpaid: {e}")
        )

    if not rows:
        return redirect(
            url_for(
                "reports.unpaid_users",
                error="Tidak ada pelanggan unpaid bulan ini.",
            )
        )

    # Susun teks ringkasan
    lines: list[str] = []
    header_name = reseller["display_name"] or reseller["router_username"]

    lines.append("Laporan pelanggan belum bayar bulan ini:")
    lines.append(f"Reseller: {header_name}")
    lines.append("")

    total = 0
    for idx, r in enumerate(rows, start=1):
        name = r.get("full_name") or "-"
        petugas = r.get("petugas_name") or "-"
        price = r.get("monthly_price") or 0
        total += price

        lines.append(f"{idx}. {name} (Petugas: {petugas}) - Rp {price:,.0f}")

    lines.append("")
    lines.append(f"Total tagihan: Rp {total:,.0f}")
    lines.append("")
    lines.append("Detail lengkap bisa dilihat di menu Reports » Unpaid.")
    lines.append(f"- {header_name}")

    message = "\n".join(lines)

    try:
        send_wa(wa_target, message)
    except WhatsAppError as e:
        print(f"[send_wa_unpaid_summary] gagal kirim WA ringkasan ke reseller: {e}")
        return redirect(
            url_for(
                "reports.unpaid_users",
                error=f"Gagal kirim WA ke reseller: {e}",
            )
        )

    return redirect(
        url_for(
            "reports.unpaid_users",
            success="Ringkasan unpaid berhasil dikirim ke WA reseller.",
        )
    )
