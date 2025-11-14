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
<div class="flex min-h-[60vh] items-center justify-center">
  <div class="w-full max-w-md space-y-6 rounded-xl border border-slate-800 bg-slate-900/80 p-6 shadow-lg">
    <div class="space-y-1 text-center">
      <h1 class="flex items-center justify-center gap-2 text-lg font-semibold">
        <span>🛠</span>
        <span>Admin Panel</span>
      </h1>
      <p class="text-xs text-slate-400">
        Masuk sebagai <span class="font-mono">ADMIN</span> menggunakan kredensial
        yang dikonfigurasi di server (Config.ADMIN_USERNAME / ADMIN_PASSWORD).
      </p>
    </div>

    {% if info %}
      <div class="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
        {{ info }}
      </div>
    {% endif %}
    {% if error %}
      <div class="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
        {{ error }}
      </div>
    {% endif %}

    <form method="post" action="{{ url_for('admin.admin_login') }}" class="space-y-4">
      <div class="space-y-1 text-sm">
        <label class="block text-xs font-medium text-slate-300">👤 Username</label>
        <input
          type="text"
          name="username"
          value="{{ username or '' }}"
          class="w-full rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-100
                 focus:border-emerald-500 focus:outline-none focus:ring-0"
          placeholder="admin username"
          required
        >
      </div>

      <div class="space-y-1 text-sm">
        <label class="block text-xs font-medium text-slate-300">🔑 Password</label>
        <input
          type="password"
          name="password"
          class="w-full rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-100
                 focus:border-emerald-500 focus:outline-none focus:ring-0"
          placeholder="••••••••"
          required
        >
      </div>

      <div class="flex items-center justify-between pt-2">
        <button
          type="submit"
          class="inline-flex items-center gap-1 rounded-md border border-emerald-500/60 bg-emerald-500/10
                 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20"
        >
          ▶️ <span>Login Admin</span>
        </button>

        <p class="text-[11px] text-slate-500">
          Hanya untuk operator / admin sistem.
        </p>
      </div>
    </form>
  </div>
</div>
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
<div class="space-y-4">
  <!-- Header + Logout -->
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div>
      <h1 class="flex items-center gap-2 text-base font-semibold text-slate-100">
        <span>🧾</span>
        <span>Admin – Semua Invoice</span>
      </h1>
      <p class="text-[11px] text-slate-500">
        Monitoring dan kontrol invoice untuk semua reseller. Gunakan dengan hati-hati. 🔐
      </p>
    </div>

    <a
      href="{{ url_for('admin.admin_logout') }}"
      class="inline-flex items-center gap-1 rounded-md border border-rose-500/60 bg-rose-500/10
             px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-500/20"
    >
      🚪 <span>Logout Admin</span>
    </a>
  </div>

  <!-- Alerts -->
  <div class="space-y-2">
    {% if error %}
      <div class="rounded-md border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
        ⚠️ {{ error }}
      </div>
    {% endif %}
    {% if db_error %}
      <div class="rounded-md border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
        ⚠️ {{ db_error }}
      </div>
    {% endif %}
    {% if success %}
      <div class="rounded-md border border-emerald-500/60 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
        ✅ {{ success }}
      </div>
    {% endif %}
  </div>

  <!-- Filter bar -->
  <div class="flex flex-wrap items-center gap-2 text-[11px]">
    <span class="text-slate-500">Filter status:</span>
    <div class="inline-flex flex-wrap gap-1">
      <a
        href="{{ url_for('admin.admin_invoices') }}"
        class="rounded-md border border-slate-700 px-2 py-1
               {% if not request.args.get('status') %}bg-slate-800 text-slate-100{% else %}text-slate-300 hover:bg-slate-800{% endif %}"
      >
        📄 Semua
      </a>
      <a
        href="{{ url_for('admin.admin_invoices', status='pending') }}"
        class="rounded-md border border-slate-700 px-2 py-1
               {% if request.args.get('status') == 'pending' %}bg-slate-800 text-slate-100{% else %}text-slate-300 hover:bg-slate-800{% endif %}"
      >
        ⏳ Pending
      </a>
      <a
        href="{{ url_for('admin.admin_invoices', status='overdue') }}"
        class="rounded-md border border-slate-700 px-2 py-1
               {% if request.args.get('status') == 'overdue' %}bg-slate-800 text-slate-100{% else %}text-slate-300 hover:bg-slate-800{% endif %}"
      >
        ⏰ Overdue
      </a>
      <a
        href="{{ url_for('admin.admin_invoices', status='paid') }}"
        class="rounded-md border border-slate-700 px-2 py-1
               {% if request.args.get('status') == 'paid' %}bg-slate-800 text-slate-100{% else %}text-slate-300 hover:bg-slate-800{% endif %}"
      >
        ✅ Paid
      </a>
    </div>
  </div>

  <!-- Tabel Invoice -->
  <div class="overflow-hidden rounded-lg border border-slate-800 bg-slate-900/60">
    <div class="border-b border-slate-800 px-4 py-2">
      <h2 class="text-xs font-semibold text-slate-200">Daftar Invoice</h2>
      <p class="text-[11px] text-slate-500">
        Klik <span class="font-mono">✅ Tandai Paid</span> untuk mengubah status invoice menjadi PAID.
      </p>
    </div>

    {% if invoices %}
      <div class="overflow-x-auto">
        <table class="min-w-full border-collapse text-xs">
          <thead>
            <tr class="border-b border-slate-800 bg-slate-900">
              <th class="px-2 py-2 text-left font-medium text-slate-300">ID</th>
              <th class="px-2 py-2 text-left font-medium text-slate-300">Reseller</th>
              <th class="px-2 py-2 text-left font-medium text-slate-300">Periode</th>
              <th class="px-2 py-2 text-left font-medium text-slate-300">Status</th>
              <th class="px-2 py-2 text-right font-medium text-slate-300">Enabled Users</th>
              <th class="px-2 py-2 text-right font-medium text-slate-300">Tarif/User</th>
              <th class="px-2 py-2 text-right font-medium text-slate-300">Total</th>
              <th class="px-2 py-2 text-left font-medium text-slate-300">Jatuh Tempo</th>
              <th class="px-2 py-2 text-left font-medium text-slate-300">Paid At</th>
              <th class="px-2 py-2 text-center font-medium text-slate-300">Aksi</th>
            </tr>
          </thead>
          <tbody>
            {% for inv in invoices %}
            <tr class="border-b border-slate-800 hover:bg-slate-900/80">
              <td class="px-2 py-2 align-top text-slate-200">{{ inv.invoice_id }}</td>
              <td class="px-2 py-2 align-top text-slate-200">{{ inv.reseller_name }}</td>
              <td class="px-2 py-2 align-top text-slate-200">
                {{ inv.period_start }} s/d {{ inv.period_end }}
              </td>
              <td class="px-2 py-2 align-top">
                {% if inv.status == 'paid' %}
                  <span class="inline-flex items-center rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                    ✅ PAID
                  </span>
                {% elif inv.status == 'overdue' %}
                  <span class="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-300">
                    ⏰ OVERDUE
                  </span>
                {% else %}
                  <span class="inline-flex items-center rounded-full bg-slate-700/50 px-2 py-0.5 text-[11px] text-slate-200">
                    ⏳ {{ inv.status }}
                  </span>
                {% endif %}
              </td>
              <td class="px-2 py-2 align-top text-right tabular-nums text-slate-200">
                {{ inv.total_enabled_users }}
              </td>
              <td class="px-2 py-2 align-top text-right tabular-nums text-slate-200">
                Rp {{ '{:,.0f}'.format(inv.price_per_user) }}
              </td>
              <td class="px-2 py-2 align-top text-right tabular-nums text-slate-200">
                Rp {{ '{:,.0f}'.format(inv.total_amount) }}
              </td>
              <td class="px-2 py-2 align-top text-slate-200">
                {{ inv.due_date }}
              </td>
              <td class="px-2 py-2 align-top text-slate-200">
                {{ inv.paid_at or '-' }}
              </td>
              <td class="px-2 py-2 align-top text-center">
                {% if inv.status != 'paid' %}
                  <form
                    method="post"
                    action="{{ url_for('admin.admin_mark_paid', invoice_id=inv.invoice_id) }}"
                    class="inline"
                  >
                    <button
                      type="submit"
                      class="inline-flex items-center gap-1 rounded-md border border-emerald-500/60 bg-emerald-500/10
                             px-2 py-1 text-[11px] font-medium text-emerald-300 hover:bg-emerald-500/20"
                    >
                      ✅ <span>Tandai Paid</span>
                    </button>
                  </form>
                {% else %}
                  <span class="text-[11px] text-slate-500">-</span>
                {% endif %}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <div class="px-4 py-6 text-center text-xs text-slate-400">
        Tidak ada invoice untuk filter ini.
      </div>
    {% endif %}
  </div>

  <p class="text-[11px] text-slate-500">
    Catatan: tombol <span class="font-mono">✅ Tandai Paid</span> langsung mengubah status invoice menjadi
    <span class="font-mono">paid</span> dan mengisi <span class="font-mono">paid_at = NOW()</span>.
    Untuk catatan tambahan (nomor transfer, dsb) bisa dicatat di sisi reseller atau lewat query terpisah.
  </p>
</div>
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
