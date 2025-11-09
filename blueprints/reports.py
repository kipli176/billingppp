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
<h1>📊 Laporan Unpaid (Bulan Ini)</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}
{% if db_error %}
  <p style="color:#ff5555;">⚠️ {{ db_error }}</p>
{% endif %}
{% if success %}
  <p style="color:#00ff00;">✅ {{ success }}</p>
{% endif %}

<div style="margin-bottom:8px;">
  <form method="post" action="{{ url_for('reports.send_wa_unpaid') }}" style="display:inline;">
    <button type="submit"
            style="padding:4px 10px; background:#001a00; color:#0f0;
                   border:1px solid #0f0; border-radius:4px; cursor:pointer;">
      📲 Kirim WA ke Yang Belum Bayar
    </button>
  </form>

  <a href="{{ url_for('main.dashboard') }}" class="btn" style="margin-left:8px;">🏠 Dashboard</a>
</div>

<div style="border:1px solid #0f0; padding:8px; margin-top:4px; max-height:540px; overflow:auto;">
  <h3>Daftar Pelanggan Belum Bayar</h3>

  {% if rows %}
    <table>
      <tr>
        <th>User</th>
        <th>Nama</th>
        <th>WA</th>
        <th>Petugas</th>
        <th>Profile</th>
        <th>Tagihan</th>
      </tr>
      {% for r in rows %}
      <tr>
        <td>{{ r.ppp_username }}</td>
        <td>{{ r.full_name or '-' }}</td>
        <td>{{ r.wa_number or '-' }}</td>
        <td>{{ r.petugas_name or '-' }}</td>
        <td>{{ r.profile_name or '-' }}</td>
        <td>Rp {{ '{:,.0f}'.format(r.monthly_price or 0) }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Semua pelanggan sudah bayar bulan ini 🎉</p>
  {% endif %}
</div>

<pre style="font-size:12px; opacity:0.8; margin-top:8px;">
Catatan:
- Tombol "Kirim WA ke Yang Belum Bayar" akan mengirim pesan ke semua
  pelanggan yang punya nomor WA dan belum bayar bulan ini.
- Pengiriman hanya dilakukan jika "use_notifications" di pengaturan reseller = ON.
</pre>
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
