# blueprints/invoices.py

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

bp = Blueprint("invoices", __name__)


def _require_login():
    """
    Pastikan reseller sudah login.
    Return reseller_row atau redirect ke login.
    """
    reseller_id = session.get("reseller_id")
    if not reseller_id:
        return None, redirect(url_for("auth_reseller.login"))

    reseller = db.query_one(
        """
        SELECT id, display_name, router_username,
               wa_number, email,
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
# LIST
# ======================================================================

@bp.route("/invoices", methods=["GET"])
def list_invoices():
    """
    List invoice untuk reseller yang login.
    Data diambil dari v_reseller_invoices.
    Optional filter status via ?status=pending|paid|overdue
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None
    status_filter = (request.args.get("status") or "").strip()

    invoices = []
    db_error = None

    try:
        if status_filter:
            invoices = db.query_all(
                """
                SELECT *
                FROM v_reseller_invoices
                WHERE reseller_id = %(rid)s
                  AND status = %(st)s
                ORDER BY period_start DESC
                """,
                {"rid": reseller["id"], "st": status_filter},
            )
        else:
            invoices = db.query_all(
                """
                SELECT *
                FROM v_reseller_invoices
                WHERE reseller_id = %(rid)s
                ORDER BY period_start DESC
                """,
                {"rid": reseller["id"]},
            )
    except Exception as e:
        db_error = f"Gagal mengambil data invoice: {e}"

    body_html = """
<h1>📑 Reseller Invoices</h1>
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
  <a href="{{ url_for('invoices.list_invoices') }}" class="btn">📄 Semua</a>
  <a href="{{ url_for('invoices.list_invoices', status='pending') }}" class="btn">⏳ Pending</a>
  <a href="{{ url_for('invoices.list_invoices', status='overdue') }}" class="btn">⏰ Overdue</a>
  <a href="{{ url_for('invoices.list_invoices', status='paid') }}" class="btn">✅ Paid</a>

  <a href="{{ url_for('main.dashboard') }}" class="btn" style="margin-left:8px;">🏠 Dashboard</a>
</div>

<div style="border:1px solid #0f0; padding:8px; margin-top:4px;">
  <h3>Daftar Invoice</h3>

  {% if invoices %}
    <table>
      <tr>
        <th>Periode</th>
        <th>Status</th>
        <th>Enabled Users</th>
        <th>Tarif/User</th>
        <th>Total</th>
        <th>Jatuh Tempo</th>
        <th>Bayar</th>
        <th>Aksi</th>
      </tr>
      {% for inv in invoices %}
      <tr>
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
        <td>
          {% if inv.status == 'paid' %}
            {{ inv.paid_at or '-' }}
          {% else %}
            -
          {% endif %}
        </td>
        <td style="white-space:nowrap;">
          <a href="{{ url_for('invoices.view_invoice', invoice_id=inv.invoice_id) }}" class="btn" style="font-size:11px; padding:2px 4px;">
            🔍 Detail
          </a>
        </td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Tidak ada invoice untuk filter ini.</p>
  {% endif %}
</div>
    """

    return render_terminal_page(
        title="Invoices",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "invoices": invoices,
            "error": error,
            "success": success,
            "db_error": db_error,
        },
    )


# ======================================================================
# DETAIL
# ======================================================================

@bp.route("/invoices/<int:invoice_id>", methods=["GET"])
def view_invoice(invoice_id: int):
    """
    Detail satu invoice untuk reseller.
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    inv = db.query_one(
        """
        SELECT *
        FROM v_reseller_invoices
        WHERE invoice_id = %(iid)s
          AND reseller_id = %(rid)s
        """,
        {"iid": invoice_id, "rid": reseller["id"]},
    )
    if inv is None:
        return redirect(url_for("invoices.list_invoices", error="Invoice tidak ditemukan."))

    body_html = """
<h1>📄 Detail Invoice</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

<div style="border:1px solid #0f0; padding:8px; max-width:720px;">

  <table>
    <tr><th>ID Invoice</th><td>{{ inv.invoice_id }}</td></tr>
    <tr><th>Periode</th><td>{{ inv.period_start }} s/d {{ inv.period_end }}</td></tr>
    <tr><th>Status</th>
      <td>
        {% if inv.status == 'paid' %}
          ✅ PAID
        {% elif inv.status == 'overdue' %}
          ⏰ OVERDUE
        {% else %}
          ⏳ {{ inv.status }}
        {% endif %}
      </td>
    </tr>
    <tr><th>Total Enabled Users</th><td>{{ inv.total_enabled_users }}</td></tr>
    <tr><th>Tarif/User</th><td>Rp {{ '{:,.0f}'.format(inv.price_per_user) }}</td></tr>
    <tr><th>Total Tagihan</th><td>Rp {{ '{:,.0f}'.format(inv.total_amount) }}</td></tr>
    <tr><th>Jatuh Tempo</th><td>{{ inv.due_date }}</td></tr>
    <tr><th>Tanggal Bayar</th><td>{{ inv.paid_at or '-' }}</td></tr>
    <tr><th>Payment Ref</th><td>{{ inv.payment_reference or '-' }}</td></tr>
    <tr><th>Channel</th><td>{{ inv.payment_channel or '-' }}</td></tr>
    <tr><th>Payment URL</th><td>{{ inv.external_payment_url or '-' }}</td></tr>
  </table>

  <form method="post" action="{{ url_for('invoices.mark_paid', invoice_id=inv.invoice_id) }}" style="margin-top:10px;">
    {% if inv.status != 'paid' %}
      <h3>🔐 Tandai Sudah Dibayar</h3>
      <label>
        Ref. Pembayaran<br>
        <input type="text" name="payment_reference"
               placeholder="misal: transfer-BCA-123"
               style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
      </label>
      <br><br>
      <label>
        Channel<br>
        <input type="text" name="payment_channel"
               placeholder="misal: bank transfer / cash / duitku"
               style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
      </label>
      <br><br>
      <button type="submit"
              style="padding:6px 12px; background:#001a00; color:#0f0;
                     border:1px solid #0f0; border-radius:4px; cursor:pointer;">
        ✅ Tandai Paid
      </button>
    {% else %}
      <p>Invoice ini sudah PAID.</p>
    {% endif %}
  </form>

</div>

<p style="margin-top:10px;">
  <a href="{{ url_for('invoices.list_invoices') }}" class="btn">⬅️ Kembali ke List</a>
</p>
    """

    return render_terminal_page(
        title=f"Invoice {invoice_id}",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "inv": inv,
        },
    )


# ======================================================================
# MARK PAID
# ======================================================================

@bp.route("/invoices/<int:invoice_id>/mark-paid", methods=["POST"])
def mark_paid(invoice_id: int):
    """
    Tandai invoice sebagai paid.
    """
    reseller, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    payment_reference = (request.form.get("payment_reference") or "").strip()
    payment_channel = (request.form.get("payment_channel") or "").strip()

    try:
        db.execute(
            """
            UPDATE reseller_invoices
            SET status = 'paid',
                paid_at = NOW(),
                payment_reference = %(ref)s,
                payment_channel = %(ch)s,
                updated_at = NOW()
            WHERE id = %(iid)s
              AND reseller_id = %(rid)s
            """,
            {
                "ref": payment_reference or None,
                "ch": payment_channel or None,
                "iid": invoice_id,
                "rid": reseller["id"],
            },
        )
    except Exception as e:
        return redirect(url_for("invoices.list_invoices", error=f"Gagal update invoice: {e}"))

    return redirect(url_for("invoices.view_invoice", invoice_id=invoice_id, _anchor="top"))
