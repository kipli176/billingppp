# blueprints/admin.py

from __future__ import annotations

from flask import (
    Blueprint,
    request,
    session,
    redirect,
    url_for,
)

import db
from app import render_terminal_page
from config import Config

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ======================================================================
# Helper: cek admin
# ======================================================================

def _require_admin():
    """
    Pastikan user adalah admin.
    """
    if not session.get("is_admin"):
        return False, redirect(url_for("admin.admin_login"))
    return True, None


# ======================================================================
# Login / Logout Admin
# ======================================================================

@bp.route("/login", methods=["GET", "POST"])
def admin_login():
    """
    Login admin panel.
    Pakai username/password dari Config.ADMIN_USERNAME / ADMIN_PASSWORD.
    """
    error = None
    info = None
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            error = "Username dan password wajib diisi."
        else:
            if (
                username == Config.ADMIN_USERNAME
                and password == Config.ADMIN_PASSWORD
            ):
                session.clear()
                session["is_admin"] = True
                info = "Login admin berhasil."
                return redirect(url_for("admin.admin_invoices"))
            else:
                error = "Username atau password admin salah."

    body_html = """
<h1>🛠 Admin Login</h1>

{% if info %}
  <p style="color:#00ff00;">ℹ️ {{ info }}</p>
{% endif %}
{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}

<form method="post" style="max-width:360px; margin-top:10px;">
  <label>
    👤 Username<br>
    <input type="text" name="username" value="{{ username or '' }}"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    🔒 Password<br>
    <input type="password" name="password"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    ▶️ Login Admin
  </button>
</form>
    """

    return render_terminal_page(
        title="Admin Login",
        body_html=body_html,
        context={
            "error": error,
            "info": info,
            "username": username,
        },
    )


@bp.route("/logout")
def admin_logout():
    session.pop("is_admin", None)
    # jangan clear semua, supaya tidak ganggu session reseller kalau kebetulan dipakai
    return redirect(url_for("admin.admin_login"))


# ======================================================================
# Admin: Tabel Invoice
# ======================================================================

@bp.route("/invoices", methods=["GET"])
def admin_invoices():
    """
    Halaman admin: list semua invoice dari semua reseller.

    Fitur:
    - filter status (?status=pending|overdue|paid)
    - tombol "Paid" satu klik.
    """
    ok, resp = _require_admin()
    if not ok:
        return resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None
    status_filter = (request.args.get("status") or "").strip()

    db_error = None
    invoices = []

    try:
        if status_filter:
            invoices = db.query_all(
                """
                SELECT *
                FROM v_reseller_invoices
                WHERE status = %(st)s
                ORDER BY period_start DESC, reseller_name
                """,
                {"st": status_filter},
            )
        else:
            invoices = db.query_all(
                """
                SELECT *
                FROM v_reseller_invoices
                ORDER BY period_start DESC, reseller_name
                """,
                {},
            )
    except Exception as e:
        db_error = f"Gagal mengambil data invoice: {e}"

    body_html = """
<h1>🛠 Admin – Semua Invoice</h1>

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
  <a href="{{ url_for('admin.admin_invoices') }}" class="btn">📄 Semua</a>
  <a href="{{ url_for('admin.admin_invoices', status='pending') }}" class="btn">⏳ Pending</a>
  <a href="{{ url_for('admin.admin_invoices', status='overdue') }}" class="btn">⏰ Overdue</a>
  <a href="{{ url_for('admin.admin_invoices', status='paid') }}" class="btn">✅ Paid</a>

  <a href="{{ url_for('admin.admin_logout') }}" class="btn btn-danger" style="margin-left:8px;">🚪 Logout Admin</a>
</div>

<div style="border:1px solid #0f0; padding:8px; max-height:540px; overflow:auto;">
  <h3>Daftar Invoice</h3>

  {% if invoices %}
    <table>
      <tr>
        <th>ID</th>
        <th>Reseller</th>
        <th>Periode</th>
        <th>Status</th>
        <th>Enabled Users</th>
        <th>Tarif/User</th>
        <th>Total</th>
        <th>Jatuh Tempo</th>
        <th>Paid At</th>
        <th>Aksi</th>
      </tr>
      {% for inv in invoices %}
      <tr>
        <td>{{ inv.invoice_id }}</td>
        <td>{{ inv.reseller_name }}</td>
        <td>{{ inv.period_start }} s/d {{ inv.period_end }}</td>
        <td>
          {% if inv.status == 'paid' %}
            ✅ PAID
          {% elif inv.status == 'overdue' %}
            ⏰ OVERDUE
          {% else %}
            ⏳ {{ inv.status }}
          {% endif %}
        </td>
        <td>{{ inv.total_enabled_users }}</td>
        <td>Rp {{ '{:,.0f}'.format(inv.price_per_user) }}</td>
        <td>Rp {{ '{:,.0f}'.format(inv.total_amount) }}</td>
        <td>{{ inv.due_date }}</td>
        <td>{{ inv.paid_at or '-' }}</td>
        <td style="white-space:nowrap;">
          {% if inv.status != 'paid' %}
            <form method="post" action="{{ url_for('admin.admin_mark_paid', invoice_id=inv.invoice_id) }}" style="display:inline;">
              <button type="submit" class="btn" style="font-size:11px; padding:2px 4px;">
                ✅ Paid
              </button>
            </form>
          {% else %}
            -
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Tidak ada invoice untuk filter ini.</p>
  {% endif %}
</div>

<pre style="font-size:12px; opacity:0.8; margin-top:8px;">
Catatan:
- Tombol "✅ Paid" langsung mengubah status invoice menjadi PAID dan set paid_at = NOW().
- Untuk catatan tambahan (nomor transfer, dll) bisa diisi manual lewat menu reseller atau query SQL.
</pre>
    """

    return render_terminal_page(
        title="Admin – Invoices",
        body_html=body_html,
        context={
            "error": error,
            "success": success,
            "db_error": db_error,
            "invoices": invoices,
        },
    )


# ======================================================================
# Admin: Mark Paid (satu klik)
# ======================================================================

@bp.route("/invoices/<int:invoice_id>/paid", methods=["POST"])
def admin_mark_paid(invoice_id: int):
    """
    Admin menandai invoice sebagai PAID (satu klik).
    """
    ok, resp = _require_admin()
    if not ok:
        return resp

    try:
        db.execute(
            """
            UPDATE reseller_invoices
            SET status = 'paid',
                paid_at = NOW(),
                payment_channel = COALESCE(payment_channel, 'admin-panel'),
                updated_at = NOW()
            WHERE id = %(iid)s
            """,
            {"iid": invoice_id},
        )
    except Exception as e:
        return redirect(url_for("admin.admin_invoices", error=f"Gagal update invoice: {e}"))

    return redirect(url_for("admin.admin_invoices", success=f"Invoice {invoice_id} ditandai PAID."))
