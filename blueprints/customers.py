# blueprints/customers.py

from __future__ import annotations

import datetime

from flask import (
    Blueprint,
    session,
    redirect,
    url_for,
    request,
)
from datetime import date
import db
from app import render_terminal_page
from mikrotik_client import (
    get_ppp_secrets,
    get_ppp_active,
    terminate_ppp_active_by_name,
    update_ppp_secret,
    delete_ppp_secret,
    create_ppp_secret,
    MikrotikError,
)

bp = Blueprint("customers", __name__)


# ======================================================================
# Helper login
# ======================================================================

def _require_login():
    """
    Pastikan reseller sudah login.
    Return (reseller_row, router_ip) atau redirect ke login.
    """
    reseller_id = session.get("reseller_id")
    router_ip = session.get("router_ip")

    if not reseller_id:
        return None, None, redirect(url_for("auth_reseller.login"))

    reseller = db.query_one(
        """
        SELECT id, display_name, router_username, router_password,
               wa_number, email,
               use_notifications, use_auto_payment,
               is_active
        FROM resellers
        WHERE id = %(rid)s
        """,
        {"rid": reseller_id},
    )

    if reseller is None or not reseller["is_active"]:
        session.clear()
        return None, None, redirect(url_for("auth_reseller.login"))

    if not router_ip:
        router_ip = "-"

    return reseller, router_ip, None


# ======================================================================
# LIST + FILTER + PAGINASI
# ======================================================================

@bp.route("/customers", methods=["GET"])
def list_customers():
    """
    Tampilkan daftar PPP customers untuk reseller yang login.

    Data utama diambil dari view v_payment_status_detail.
    Fitur:
    - Status online (dari /ppp/active)
    - Filter status (all, paid, unpaid, isolated, disabled)
    - Filter petugas
    - Pencarian (username / nama / petugas)
    - Paginasi (page & per_page)
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None

    # ---------------- Filter & pagination params ----------------
    status_filter = (request.args.get("status") or "all").strip()
    q = (request.args.get("q") or "").strip()
    petugas_q = (request.args.get("petugas") or "").strip()

    try:
        page = int(request.args.get("page") or "1")
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    try:
        per_page = int(request.args.get("per_page") or "50")
    except ValueError:
        per_page = 50
    if per_page < 10:
        per_page = 10
    if per_page > 500:
        per_page = 500

    offset = (page - 1) * per_page

    # 1) build WHERE SQL
    where_clauses = ["reseller_id = %(rid)s"]
    params = {"rid": reseller["id"]}

    if status_filter == "paid":
        where_clauses.append("payment_status_text = 'paid_current_period'")
    elif status_filter == "unpaid":
        where_clauses.append(
            "payment_status_text IN ('unpaid_current_period','never_paid')"
        )
    elif status_filter == "isolated":
        where_clauses.append("is_isolated = TRUE")
    elif status_filter == "disabled":
        where_clauses.append("is_enabled = FALSE")

    if petugas_q:
        where_clauses.append("petugas_name ILIKE %(petugas)s")
        params["petugas"] = f"%{petugas_q}%"

    if q:
        where_clauses.append(
            "(ppp_username ILIKE %(q)s OR full_name ILIKE %(q)s OR petugas_name ILIKE %(q)s)"
        )
        params["q"] = f"%{q}%"

    where_sql = " AND ".join(where_clauses)

    customers = []
    db_error = None
    router_error = None
    online_names = set()
    total_rows = 0
    total_pages = 1

    # ringkasan pembayaran
    paid_count = 0
    paid_total = 0
    unpaid_count = 0
    unpaid_total = 0


        # 2) hitung total rows + ringkasan paid/unpaid
    try:
        # total rows
        row = db.query_one(
            f"""
            SELECT COUNT(*) AS cnt
            FROM v_payment_status_detail
            WHERE {where_sql}
            """,
            params,
        )
        total_rows = row["cnt"] if row else 0
        if total_rows > 0:
            total_pages = (total_rows + per_page - 1) // per_page

        # total paid (lunas bulan ini)
        paid_row = db.query_one(
            f"""
            SELECT
              COUNT(*) AS cnt,
              COALESCE(SUM(monthly_price), 0) AS total_amount
            FROM v_payment_status_detail
            WHERE {where_sql}
              AND payment_status_text = 'paid_current_period'
            """,
            params,
        )
        if paid_row:
            paid_count = paid_row["cnt"] or 0
            paid_total = paid_row["total_amount"] or 0

        # total unpaid (belum pernah bayar + belum bayar bulan ini)
        unpaid_row = db.query_one(
            f"""
            SELECT
              COUNT(*) AS cnt,
              COALESCE(SUM(monthly_price), 0) AS total_amount
            FROM v_payment_status_detail
            WHERE {where_sql}
              AND payment_status_text IN ('unpaid_current_period','never_paid')
            """,
            params,
        )
        if unpaid_row:
            unpaid_count = unpaid_row["cnt"] or 0
            unpaid_total = unpaid_row["total_amount"] or 0

    except Exception as e:
        db_error = f"Gagal menghitung ringkasan data customers: {e}"


    # 3) ambil data dengan LIMIT/OFFSET
    try:
        customers = db.query_all(
            f"""
            SELECT
              customer_id,
              ppp_username,
              full_name,
              address,
              wa_number,
              petugas_name,
              profile_name,
              monthly_price,
              is_enabled,
              is_isolated,
              payment_status_text,
              has_paid_current_period,
              should_isolate_current_period,
              last_connected_at,
              last_disconnected_at
            FROM v_payment_status_detail
            WHERE {where_sql}
            ORDER BY ppp_username
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                **params,
                "limit": per_page,
                "offset": offset,
            },
        )
    except Exception as e:
        db_error = f"Gagal mengambil data customers: {e}"
        customers = []

    # 4) Ambil PPP active untuk status online
    if router_ip and router_ip != "-":
        api_user = reseller["router_username"]
        api_pass = reseller["router_password"]
        try:
            active_list = get_ppp_active(router_ip, api_user, api_pass)
            if isinstance(active_list, list):
                for a in active_list:
                    if isinstance(a, dict):
                        nm = a.get("name")
                        if nm:
                            online_names.add(nm)
        except MikrotikError as e:
            router_error = f"Gagal membaca PPP active: {e}"
        except Exception as e:
            router_error = f"Error tidak terduga saat akses PPP active: {e}"
    else:
        router_error = "Router IP tidak tersedia di session. Silakan login ulang."

    # 5) Tambahkan flag is_online ke tiap row & hitung summary
    for c in customers:
        c["is_online"] = c["ppp_username"] in online_names

    online_count = sum(1 for c in customers if c.get("is_online"))
    offline_count = len(customers) - online_count

    # ---------------- HTML ----------------
    body_html = """
<!-- HEADER HALAMAN -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-center md:justify-between">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <span class="text-slate-300">Customers</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>👤</span>
      <span>PPP Customers</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Daftar pelanggan PPP untuk reseller
      <span class="font-medium text-slate-200">{{ reseller_name }}</span>.
    </p>
  </div>

  <div class="flex flex-wrap gap-2">
    <!-- Sinkron Customers -->
    <form method="post"
          action="{{ url_for('customers.sync_customers') }}"
          class="inline-flex">
      <button type="submit"
              class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
        🔄 <span>Sinkron Customers</span>
      </button>
    </form>

    <!-- Tambah Customer -->
    <a href="{{ url_for('customers.create_customer') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      ➕ <span>Tambah Customer</span>
    </a>
  </div>
</section>

<!-- ALERTS -->
{% if router_error %}
  <div class="mt-3 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ Router: {{ router_error }}
  </div>
{% endif %}
{% if error %}
  <div class="mt-3 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
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

<!-- FILTER & RINGKASAN -->
<section class="mt-4 space-y-3">
  <!-- Filter bar -->
  <div class="rounded-lg border border-slate-800 bg-slate-900/70 p-3">
    <form method="get"
          action="{{ url_for('customers.list_customers') }}"
          class="grid gap-3 text-xs md:grid-cols-2 lg:grid-cols-4 lg:items-end">

      <!-- Status -->
      <div class="space-y-1">
        <label class="block text-[11px] font-medium text-slate-300">Status</label>
        <select name="status"
                class="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none">
          <option value="all" {% if status_filter=='all' %}selected{% endif %}>All</option>
          <option value="paid" {% if status_filter=='paid' %}selected{% endif %}>Paid</option>
          <option value="unpaid" {% if status_filter=='unpaid' %}selected{% endif %}>Unpaid</option>
          <option value="isolated" {% if status_filter=='isolated' %}selected{% endif %}>Isolated</option>
          <option value="disabled" {% if status_filter=='disabled' %}selected{% endif %}>Disabled</option>
        </select>
      </div>

      <!-- Petugas -->
      <div class="space-y-1">
        <label class="block text-[11px] font-medium text-slate-300">Petugas</label>
        <input type="text"
               name="petugas"
               value="{{ petugas_q or '' }}"
               placeholder="nama petugas"
               class="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none">
      </div>

      <!-- Pencarian -->
      <div class="space-y-1">
        <label class="block text-[11px] font-medium text-slate-300">Cari</label>
        <input type="text"
               name="q"
               value="{{ q or '' }}"
               placeholder="username / nama / WA"
               class="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none">
      </div>

      <!-- Per halaman + submit -->
      <div class="flex items-end gap-2">
        <div class="flex-1 space-y-1">
          <label class="block text-[11px] font-medium text-slate-300">Per halaman</label>
          <input type="text"
                 name="per_page"
                 value="{{ per_page }}"
                 class="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none">
        </div>
        <div class="pb-1">
          <button type="submit"
                  class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
            🔍 <span>Tampilkan</span>
          </button>
        </div>
      </div>
    </form>
  </div>

  <!-- Ringkasan angka -->
  <div class="text-[11px] text-slate-400">
    <div>
      Total pelanggan (sesuai filter):
      <span class="font-semibold text-slate-100">{{ total_rows }}</span> ·
      Ditampilkan halaman ini:
      <span class="font-semibold text-slate-100">{{ customers|length }}</span> ·
      Online:
      <span class="font-semibold text-emerald-300">{{ online_count }}</span> ·
      Offline:
      <span class="font-semibold text-slate-200">{{ offline_count }}</span>
    </div>
    <div class="mt-1">
      Lunas:
      <span class="font-semibold text-emerald-300">{{ paid_count }}</span>
      <span class="text-slate-300"> (Rp {{ '{:,.0f}'.format(paid_total or 0) }})</span> ·
      Unpaid:
      <span class="font-semibold text-amber-300">{{ unpaid_count }}</span>
      <span class="text-slate-300"> (Rp {{ '{:,.0f}'.format(unpaid_total or 0) }})</span>
    </div>
    <div class="mt-1">
      Halaman
      <span class="font-semibold text-slate-100">{{ page }}</span>
      dari
      <span class="font-semibold text-slate-100">{{ total_pages }}</span>.
    </div>
  </div>
</section>

<!-- PAGINASI -->
<section class="mt-3 flex flex-wrap gap-2">
  {% if page > 1 %}
    <a href="{{ url_for('customers.list_customers', status=status_filter, q=q, petugas=petugas_q, per_page=per_page, page=page-1) }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      ⬅️ <span>Prev</span>
    </a>
  {% endif %}
  {% if page < total_pages %}
    <a href="{{ url_for('customers.list_customers', status=status_filter, q=q, petugas=petugas_q, per_page=per_page, page=page+1) }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      <span>Next</span> ➡️
    </a>
  {% endif %}
</section>

<!-- TABEL CUSTOMERS -->
<section class="mt-4 rounded-lg border border-slate-800 bg-slate-900/70 p-3">
  <div class="mb-2 flex items-center justify-between">
    <h2 class="text-sm font-semibold text-slate-200">Daftar Pelanggan PPP</h2>
    <span class="text-[11px] text-slate-500">Aksi cepat: edit, suspend, bayar, hapus.</span>
  </div>

  {% if customers %}
    <div class="overflow-x-auto">
      <table class="min-w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-slate-800 bg-slate-900">
            <th class="px-2 py-2 text-left font-medium text-slate-300">Aksi</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Nama</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Username</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Alamat</th>
            <th class="px-2 py-2 text-right font-medium text-slate-300">Harga</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Online</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Petugas</th>
            <th class="px-2 py-2 text-left font-medium text-slate-300">Status</th>
          </tr>
        </thead>
        <tbody>
          {% for c in customers %}
          <tr class="border-b border-slate-800/70 hover:bg-slate-900/60">
            <!-- Aksi -->
            <td class="px-1 py-1 align-top whitespace-nowrap">
                <div class="flex flex-nowrap gap-1 overflow-x-auto text-[9px]">
                    <!-- Edit -->
                    <a href="{{ url_for('customers.edit_customer', customer_id=c.customer_id) }}"
                    class="inline-flex items-center gap-1 rounded border border-sky-500/70 bg-sky-500/10 px-2 py-1 text-sky-200 hover:bg-sky-500/20"
                    title="Edit">
                    ✏️ 
                    </a>

                    <!-- Terminate -->
                    <form method="post"
                        action="{{ url_for('customers.terminate_customer', customer_id=c.customer_id) }}"
                        onsubmit="return confirm('Terminate session PPP {{ c.ppp_username }} sekarang?');">
                    <button type="submit"
                            class="inline-flex items-center gap-1 rounded border border-rose-500/70 bg-rose-500/10 px-2 py-1 text-rose-200 hover:bg-rose-500/20"
                            title="Terminate PPP">
                        ⏹ <span>Rest</span>
                    </button>
                    </form>

                    <!-- Toggle Enabled -->
                    <form method="post"
                        action="{{ url_for('customers.toggle_enable_customer', customer_id=c.customer_id) }}"
                        onsubmit="return confirm('Ubah status enable/disable user {{ c.ppp_username }}?');">
                    <button type="submit"
                            class="inline-flex items-center gap-1 rounded border border-slate-600 bg-slate-900 px-2 py-1 text-slate-200 hover:border-emerald-500 hover:text-emerald-300"
                            title="Enable / Disable">
                        {% if c.is_enabled %}🚫 <span>Off</span>{% else %}✅ <span>On</span>{% endif %}
                    </button>
                    </form>

                    <!-- Suspend / Unsuspend -->
                    {% if not c.is_isolated %}
                    <form method="post"
                            action="{{ url_for('customers.isolate_customer', customer_id=c.customer_id) }}"
                            onsubmit="return confirm('Suspend (isolate) user {{ c.ppp_username }} ke profil isolasi?');">
                        <button type="submit"
                                class="inline-flex items-center gap-1 rounded border border-amber-500/70 bg-amber-500/10 px-2 py-1 text-amber-200 hover:bg-amber-500/20"
                                title="Suspend / Isolate">
                        🧊 <span>Susp</span>
                        </button>
                    </form>
                    {% else %}
                    <form method="post"
                            action="{{ url_for('customers.unisolate_customer', customer_id=c.customer_id) }}"
                            onsubmit="return confirm('Unsuspend user {{ c.ppp_username }} ke profil normal?');">
                        <button type="submit"
                                class="inline-flex items-center gap-1 rounded border border-emerald-500/70 bg-emerald-500/10 px-2 py-1 text-emerald-200 hover:bg-emerald-500/20"
                                title="Unsuspend">
                        ⬅️ <span>Unsusp</span>
                        </button>
                    </form>
                    {% endif %}

                    <!-- Bayar / Batalkan bayar -->
                    {% if c.has_paid_current_period %}
                    <form method="post"
                            action="{{ url_for('customers.cancel_pay_customer', customer_id=c.customer_id) }}"
                            onsubmit="return confirm('Batalkan 1 bulan pembayaran terakhir untuk {{ c.ppp_username }}?');">
                        <input type="hidden" name="months" value="1">
                        <button type="submit"
                                class="inline-flex items-center gap-1 rounded border border-rose-500/70 bg-rose-500/10 px-2 py-1 text-rose-200 hover:bg-rose-500/20"
                                title="Batalkan bayar">
                        ↩️ <span>Unpaid</span>
                        </button>
                    </form>
                    {% else %}
                    <form method="post"
                            action="{{ url_for('customers.pay_customer', customer_id=c.customer_id) }}"
                            onsubmit="return confirm('Catat pembayaran 1 bulan untuk {{ c.ppp_username }}?');">
                        <input type="hidden" name="months" value="1">
                        <button type="submit"
                                class="inline-flex items-center gap-1 rounded border border-green-400/70 bg-green-400/10 px-2 py-1 text-green-200 hover:bg-green-400/20"
                                title="Tandai sudah bayar">
                        💰 <span>Paid</span>
                        </button>
                    </form>
                    {% endif %}

                    <!-- Delete -->
                    <form method="post"
                        action="{{ url_for('customers.delete_customer', customer_id=c.customer_id) }}"
                        onsubmit="return confirm('Yakin hapus user {{ c.ppp_username }} dari DB dan Mikrotik?');">
                    <button type="submit"
                            class="inline-flex items-center gap-1 rounded border border-rose-600/80 bg-rose-600/10 px-2 py-1 text-rose-200 hover:bg-rose-600/25"
                            title="Hapus">
                        🗑 
                    </button>
                    </form>
                </div>
                </td>


            <!-- Nama -->
            <td class="px-2 py-1 align-top uppercase text-slate-100">
              {{ c.full_name or '-' }}
            </td>

            <!-- Username -->
            <td class="px-2 py-1 align-top font-mono text-slate-100">
              {{ c.ppp_username }}
            </td>

            <!-- Alamat -->
            <td class="px-2 py-1 align-top uppercase text-slate-300">
              {{ c.address or '-' }}
            </td>

            <!-- Harga -->
            <td class="px-2 py-1 align-top text-right text-slate-100">
              {{ '{:,.0f}'.format(c.monthly_price or 0) }}
            </td>

            <!-- Online -->
            <td class="px-2 py-1 align-top">
              {% if c.is_online %}
                <span class="inline-flex items-center gap-1 text-[11px] text-emerald-300">
                  <span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span> ON
                </span>
              {% else %}
                <span class="inline-flex items-center gap-1 text-[11px] text-slate-400">
                  <span class="h-1.5 w-1.5 rounded-full bg-rose-500"></span> OFF
                </span>
              {% endif %}
            </td>

            <!-- Petugas -->
            <td class="px-2 py-1 align-top text-slate-200">
              {{ c.petugas_name or '-' }}
            </td>

            <!-- Status singkat -->
            <td class="px-2 py-1 align-top">
              {% if c.payment_status_text == 'paid_current_period' %}
                <span class="inline-flex items-center rounded-full border border-emerald-500/70 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                  Lunas
                </span>
              {% elif c.payment_status_text == 'unpaid_current_period' %}
                <span class="inline-flex items-center rounded-full border border-amber-500/70 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-300">
                  Unpaid
                </span>
              {% elif c.payment_status_text == 'isolated' %}
                <span class="inline-flex items-center rounded-full border border-sky-500/70 bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-300">
                  Iso
                </span>
              {% elif c.payment_status_text == 'never_paid' %}
                <span class="inline-flex items-center rounded-full border border-slate-500/70 bg-slate-800/70 px-2 py-0.5 text-[11px] text-slate-200">
                  Baru
                </span>
              {% else %}
                <span class="text-[11px] text-slate-300">
                  {{ c.payment_status_text }}
                </span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="text-xs text-slate-400">
      Tidak ada customer di database. Coba klik <b>"Sinkron Customers"</b>.
    </p>
  {% endif %}

  <p class="mt-3 text-[11px] text-slate-500">
    Catatan singkat:<br>
    • Status: Lunas / Unpaid / Iso / Baru.<br>
    • Suspend/Unsuspend akan mengubah profil ke isolasi / normal dan terlihat di sisi klien.<br>
    • Status Online diambil dari <code>/ppp/active</code> pada router.
  </p>
</section>
    """

    return render_terminal_page(
        title="PPP Customers",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "customers": customers,
            "router_error": router_error,
            "error": error,
            "success": success,
            "db_error": db_error,
            "status_filter": status_filter,
            "q": q,
            "petugas_q": petugas_q,
            "page": page,
            "per_page": per_page,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "online_count": online_count,
            "offline_count": offline_count,
            "paid_count": paid_count,
            "paid_total": paid_total,
            "unpaid_count": unpaid_count,
            "unpaid_total": unpaid_total,

        },
    )



# ======================================================================
# SYNC
# ======================================================================

@bp.route("/customers/sync", methods=["POST"])
def sync_customers():
    """
    Sinkron customers dari router reseller:

    - Ambil /ppp/secret dari router
    - Ambil daftar existing ppp_username dari DB
    - Untuk setiap secret:
        - kalau name belum ada → INSERT ppp_customers
        - mapping profile_name -> profile_id jika ada di ppp_profiles
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        error = "Router IP tidak tersedia di session. Silakan login ulang."
        return redirect(url_for("customers.list_customers", error=error))

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    # 1. Ambil PPP secret dari router
    try:
        secrets = get_ppp_secrets(router_ip, api_user, api_pass)
    except MikrotikError as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal mengambil PPP secret: {e}"))
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Error tidak terduga saat akses router: {e}"))

    if not secrets:
        return redirect(url_for("customers.list_customers", error="Router tidak punya PPP secret."))

    # 2. Ambil existing username dari DB
    existing_rows = db.query_all(
        """
        SELECT ppp_username
        FROM ppp_customers
        WHERE reseller_id = %(rid)s
        """,
        {"rid": reseller["id"]},
    )
    existing_usernames = {r["ppp_username"] for r in existing_rows}

    # 3. Buat mapping profile_name -> profile_id
    profile_rows = db.query_all(
        """
        SELECT id, name
        FROM ppp_profiles
        WHERE reseller_id = %(rid)s
        """,
        {"rid": reseller["id"]},
    )
    profile_map = {p["name"]: p["id"] for p in profile_rows}

    inserted = 0

    for sec in secrets:
        if not isinstance(sec, dict):
            continue

        name = sec.get("name")
        if not name:
            continue

        if name in existing_usernames:
            continue  # sudah ada di DB, skip

        password = sec.get("password") or None
        sec_profile_name = sec.get("profile") or None
        profile_id = profile_map.get(sec_profile_name) if sec_profile_name else None
        try:
            db.execute(
                """
                INSERT INTO ppp_customers
                    (reseller_id, profile_id, ppp_username, ppp_password,
                     is_enabled, is_isolated, created_at, updated_at)
                VALUES
                    (%(rid)s, %(pid)s, %(user)s, %(pass)s,
                     TRUE, FALSE, NOW(), NOW())
                """,
                {
                    "rid": reseller["id"],
                    "pid": profile_id,
                    "user": name,
                    "pass": password,
                },
            )
            inserted += 1
        except Exception as e:
            err_msg = f"Gagal insert user {name}: {e}. Sinkron dihentikan."
            return redirect(url_for("customers.list_customers", error=err_msg))



    success = f"Sinkron selesai. {inserted} user baru ditambahkan."
    return redirect(url_for("customers.list_customers", success=success))
@bp.route("/customers/new", methods=["GET", "POST"])
def create_customer():
    """
    Tambah customer baru:
    - Insert ke ppp_customers.
    - (opsional) nanti bisa ditambah create PPP secret ke router.
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = None
    success = None

    # Ambil daftar profil untuk select
    profiles = db.query_all(
        """
        SELECT id, name, is_isolation
        FROM ppp_profiles
        WHERE reseller_id = %(rid)s
        ORDER BY name
        """,
        {"rid": reseller["id"]},
    )

    if request.method == "POST":
        ppp_username = (request.form.get("ppp_username") or "").strip()
        ppp_password = (request.form.get("ppp_password") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        address = (request.form.get("address") or "").strip()
        wa_number = (request.form.get("wa_number") or "").strip()
        petugas_name = (request.form.get("petugas_name") or "").strip()
        billing_start_raw = (request.form.get("billing_start_date") or "").strip()
        profile_id_raw = (request.form.get("profile_id") or "").strip()
        is_enabled_raw = request.form.get("is_enabled") or "1"

        if not ppp_username:
            error = "PPP Username tidak boleh kosong."

        billing_start_date = None
        if billing_start_raw:
            try:
                billing_start_date = datetime.date.fromisoformat(billing_start_raw)
            except ValueError:
                error = "Format tanggal Billing Start harus YYYY-MM-DD."

        profile_id = None
        if profile_id_raw:
            try:
                profile_id = int(profile_id_raw)
            except ValueError:
                error = "Profile ID tidak valid."

        is_enabled = (is_enabled_raw == "1")

        if not error:
            # Cek apakah username sudah ada di sistem (semua reseller)
            exist = db.query_one(
                """
                SELECT id
                FROM ppp_customers
                WHERE ppp_username = %(user)s
                """,
                {"user": ppp_username},
            )
            if exist:
                error = "PPP Username sudah terdaftar di sistem."

        if not error:
            try:
                db.execute(
                    """
                    INSERT INTO ppp_customers
                        (reseller_id, profile_id, ppp_username, ppp_password,
                         full_name, address, wa_number, petugas_name,
                         billing_start_date,
                         is_enabled, is_isolated,
                         created_at, updated_at)
                    VALUES
                        (%(rid)s, %(pid)s, %(user)s, %(pwd)s,
                         %(fn)s, %(addr)s, %(wa)s, %(pt)s,
                         %(bsd)s,
                         %(en)s, FALSE,
                         NOW(), NOW())
                    """,
                    {
                        "rid": reseller["id"],
                        "pid": profile_id,
                        "user": ppp_username,
                        "pwd": ppp_password or None,
                        "fn": full_name or None,
                        "addr": address or None,
                        "wa": wa_number or None,
                        "pt": petugas_name or None,
                        "bsd": billing_start_date,
                        "en": is_enabled,
                    },
                )
            except Exception as e:
                error = f"Gagal insert customer ke DB: {e}"

        # (Opsional) Tambah PPP secret ke router di sini,
        # kalau kamu sudah punya create_ppp_secret di mikrotik_client.
        if not error and router_ip and router_ip != "-" and ppp_password:
            api_user = reseller["router_username"]
            api_pass = reseller["router_password"]
            profile_name = None
            if profile_id:
                for p in profiles:
                    if p["id"] == profile_id:
                        profile_name = p["name"]
                        break
            secret_payload = {
                "name": ppp_username,
                "password": ppp_password,
            }
            if profile_name:
                secret_payload["profile"] = profile_name
            try:
                create_ppp_secret(router_ip, api_user, api_pass, secret_payload)
            except Exception as e:
                error = f"DB sudah insert, tetapi gagal membuat PPP secret di router: {e}"

        if not error:
            success = "Customer baru berhasil ditambahkan."
            # kosongkan form
            ppp_username = ""
            ppp_password = ""
            full_name = ""
            address = ""
            wa_number = ""
            petugas_name = ""
            billing_start_raw = ""
            profile_id_raw = ""
            is_enabled_raw = "1"

    else:
        # default nilai form
        ppp_username = ""
        ppp_password = ""
        full_name = ""
        address = ""
        wa_number = ""
        petugas_name = ""
        billing_start_raw = ""
        profile_id_raw = ""
        is_enabled_raw = "1"

    body_html = """
<!-- HEADER -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <a href="{{ url_for('customers.list_customers') }}" class="hover:text-emerald-300">Customers</a>
      <span>›</span>
      <span class="text-slate-300">Tambah</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>➕</span>
      <span>Tambah Customer PPP</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Reseller: <span class="font-medium text-slate-200">{{ reseller_name }}</span>
    </p>
  </div>
</section>

<!-- ALERT -->
{% if error %}
  <div class="mt-4 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ {{ error }}
  </div>
{% endif %}
{% if success %}
  <div class="mt-4 rounded-md border border-emerald-500/70 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
    ✅ {{ success }}
  </div>
{% endif %}

<!-- FORM -->
<form method="post" class="mt-4 space-y-4 max-w-xl">

  <!-- PPP SECRET -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">🔑 PPP Secret</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          PPP Username
        </label>
        <input
          type="text"
          name="ppp_username"
          value="{{ ppp_username }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          PPP Password
        </label>
        <input
          type="text"
          name="ppp_password"
          value="{{ ppp_password }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
    </div>
  </section>

  <!-- PROFIL & STATUS -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">📡 Profil PPP &amp; Status</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Profil PPP
        </label>
        <select
          name="profile_id"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
          <option value="">-- pilih profile --</option>
          {% for p in profiles %}
            <option value="{{ p.id }}" {% if profile_id_raw|int == p.id %}selected{% endif %}>
              {{ p.name }}
            </option>
          {% endfor %}
        </select>
      </div>

      <div class="space-y-1">
        <span class="block text-xs font-medium text-slate-300">Status User</span>
        <div class="flex flex-wrap gap-4 text-xs text-slate-200">
          <label class="inline-flex items-center gap-2">
            <input
              type="radio"
              name="is_enabled"
              value="1"
              {% if is_enabled_raw=='1' %}checked{% endif %}
              class="h-3 w-3 rounded border-slate-600 bg-slate-900"
            >
            <span>Enable</span>
          </label>
          <label class="inline-flex items-center gap-2">
            <input
              type="radio"
              name="is_enabled"
              value="0"
              {% if is_enabled_raw=='0' %}checked{% endif %}
              class="h-3 w-3 rounded border-slate-600 bg-slate-900"
            >
            <span>Disable</span>
          </label>
        </div>
      </div>
    </div>
  </section>

  <!-- DATA PELANGGAN -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">👤 Data Pelanggan</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Nama Lengkap
        </label>
        <input
          type="text"
          name="full_name"
          value="{{ full_name }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Alamat
        </label>
        <textarea
          name="address"
          rows="3"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >{{ address }}</textarea>
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          No. WhatsApp
        </label>
        <input
          type="text"
          name="wa_number"
          value="{{ wa_number }}"
          placeholder="6285xxxx"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Nama Petugas
        </label>
        <input
          type="text"
          name="petugas_name"
          value="{{ petugas_name }}"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
    </div>
  </section>

  <!-- BILLING -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">💳 Billing</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Billing Start Date
        </label>
        <input
          type="date"
          name="billing_start_date"
          value="{{ billing_start_raw or today.strftime('%Y-%m-%d') }}"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
      <p class="text-[11px] text-slate-500">
        Tanggal ini akan digunakan sebagai acuan awal periode tagihan untuk customer ini.
      </p>
    </div>
  </section>

  <!-- TOMBOL AKSI -->
  <div class="flex flex-wrap items-center gap-2 pt-1">
    <button
      type="submit"
      class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-4 py-2 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
      💾 <span>Simpan Customer</span>
    </button>

    <a href="{{ url_for('customers.list_customers') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      ⬅️ <span>Kembali</span>
    </a>
  </div>
</form>
    """

    return render_terminal_page(
        title="Tambah Customer",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "profiles": profiles,
            "error": error,
            "success": success,
            "ppp_username": ppp_username,
            "ppp_password": ppp_password,
            "full_name": full_name,
            "address": address,
            "wa_number": wa_number,
            "petugas_name": petugas_name,
            "billing_start_raw": billing_start_raw,
            "profile_id_raw": profile_id_raw,
            "is_enabled_raw": is_enabled_raw,
            "today": date.today(),
        },
    )


# ======================================================================
# AKSI: Terminate
# ======================================================================

@bp.route("/customers/<int:customer_id>/terminate", methods=["POST"])
def terminate_customer(customer_id: int):
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        return redirect(url_for("customers.list_customers", error="Router IP hilang dari session."))

    cust = db.query_one(
        """
        SELECT ppp_username
        FROM ppp_customers
        WHERE id = %(cid)s AND reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    username = cust["ppp_username"]
    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    try:
        ok = terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
        if ok:
            msg = f"Session PPP '{username}' telah di-terminate."
        else:
            msg = f"Tidak ada session aktif untuk '{username}'."
        return redirect(url_for("customers.list_customers", success=msg))
    except MikrotikError as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal terminate PPP: {e}"))
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Error terminate PPP: {e}"))


# ======================================================================
# AKSI: Toggle Enable/Disable
# ======================================================================

@bp.route("/customers/<int:customer_id>/toggle-enable", methods=["POST"])
def toggle_enable_customer(customer_id: int):
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        return redirect(url_for("customers.list_customers", error="Router IP hilang dari session."))

    cust = db.query_one(
        """
        SELECT id, ppp_username, is_enabled
        FROM ppp_customers
        WHERE id = %(cid)s AND reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    username = cust["ppp_username"]
    is_enabled = bool(cust["is_enabled"])

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    new_is_enabled = not is_enabled
    mt_disabled = "no" if new_is_enabled else "yes"

    try:
        db.execute(
            """
            UPDATE ppp_customers
            SET is_enabled = %(en)s,
                updated_at = NOW()
            WHERE id = %(cid)s
            """,
            {"en": new_is_enabled, "cid": customer_id},
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal update DB: {e}"))

    try:
        update_ppp_secret(
            router_ip,
            api_user,
            api_pass,
            secret_name=username,
            updates={"disabled": mt_disabled},
        )
    except MikrotikError as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah berubah, tapi gagal update router: {e}"))
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah berubah, tapi error update router: {e}"))
    # setelah update router, kill session aktif (kalau ada) supaya tidak nyantol
    try:
        terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
    except Exception as e:
        print(f"[toggle_enable_customer] gagal terminate session {username}: {e}")

    msg = f"User '{username}' sekarang {'ENABLED' if new_is_enabled else 'DISABLED'}."
    return redirect(url_for("customers.list_customers", success=msg))


# ======================================================================
# AKSI: Isolate (ganti ke isolasi profile)
# ======================================================================

@bp.route("/customers/<int:customer_id>/isolate", methods=["POST"])
def isolate_customer(customer_id: int):
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        return redirect(url_for("customers.list_customers", error="Router IP hilang dari session."))

    cust = db.query_one(
        """
        SELECT c.id, c.ppp_username, c.profile_id,
               p.name AS profile_name
        FROM ppp_customers c
        LEFT JOIN ppp_profiles p ON p.id = c.profile_id
        WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    username = cust["ppp_username"]

    iso_profile = db.query_one(
        """
        SELECT id, name
        FROM ppp_profiles
        WHERE reseller_id = %(rid)s
          AND is_isolation = TRUE
        ORDER BY id
        LIMIT 1
        """,
        {"rid": reseller["id"]},
    )
    if iso_profile is None:
        return redirect(url_for("customers.list_customers", error="Belum ada profile isolasi (is_isolation=TRUE) untuk reseller ini."))

    iso_profile_id = iso_profile["id"]
    iso_profile_name = iso_profile["name"]

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    try:
        db.execute(
            """
            UPDATE ppp_customers
            SET 
                is_isolated = TRUE,
                updated_at = NOW()
            WHERE id = %(cid)s
            """,
            {"pid": iso_profile_id, "cid": customer_id},
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal update DB untuk isolate: {e}"))

    try:
        update_ppp_secret(
            router_ip,
            api_user,
            api_pass,
            secret_name=username,
            updates={"profile": iso_profile_name},
        )
    except MikrotikError as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah isolate, tapi gagal ganti profile di router: {e}"))
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah isolate, tapi error ganti profile di router: {e}"))
    # kill session aktif agar reconnect dengan profile isolasi
    try:
        terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
    except Exception as e:
        print(f"[isolate_customer] gagal terminate session {username}: {e}")

    msg = f"User '{username}' sudah di-isolate dengan profile '{iso_profile_name}'."
    return redirect(url_for("customers.list_customers", success=msg))


# ======================================================================
# AKSI: Unisolate (kembali ke profil normal default)
# ======================================================================

@bp.route("/customers/<int:customer_id>/unisolate", methods=["POST"])
def unisolate_customer(customer_id: int):
    """
    Un-isolate user:
    - Cari salah satu profile normal (is_isolation = FALSE) untuk reseller ini,
      gunakan sebagai "profil normal default".
    - Ganti profile_id + profile di router ke profil normal tersebut.
    NOTE: Tanpa kolom extra di DB, kita tidak bisa tahu profil sebelumnya dengan pasti.
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        return redirect(url_for("customers.list_customers", error="Router IP hilang dari session."))

    cust = db.query_one(
        """
        SELECT c.id, c.ppp_username, c.profile_id,
               c.is_isolated
        FROM ppp_customers c
        WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    if not cust["is_isolated"]:
        return redirect(url_for("customers.list_customers", error="User ini tidak dalam status isolasi."))

    username = cust["ppp_username"]

    normal_profile = db.query_one(
        """
        SELECT id, name
        FROM ppp_profiles
        WHERE reseller_id = %(rid)s
          AND is_isolation = FALSE
        ORDER BY id
        LIMIT 1
        """,
        {"rid": reseller["id"]},
    )
    if normal_profile is None:
        return redirect(url_for("customers.list_customers", error="Belum ada profile normal (is_isolation=FALSE) untuk reseller ini."))

    norm_pid = normal_profile["id"]
    norm_name = normal_profile["name"]

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    try:
        db.execute(
            """
            UPDATE ppp_customers
            SET 
                is_isolated = FALSE,
                updated_at = NOW()
            WHERE id = %(cid)s
            """,
            {"pid": norm_pid, "cid": customer_id},
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal update DB untuk unisolate: {e}"))

    try:
        update_ppp_secret(
            router_ip,
            api_user,
            api_pass,
            secret_name=username,
            updates={"profile": norm_name},
        )
    except MikrotikError as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah unisolate, tapi gagal ganti profile di router: {e}"))
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"DB sudah unisolate, tapi error ganti profile di router: {e}"))

    # kill session aktif supaya reconnect pakai profil normal
    try:
        terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
    except Exception as e:
        print(f"[unisolate_customer] gagal terminate session {username}: {e}")

    msg = f"User '{username}' sudah dikembalikan dari isolasi ke profile normal '{norm_name}'."

    return redirect(url_for("customers.list_customers", success=msg))


# ======================================================================
# AKSI: Delete
# ======================================================================

@bp.route("/customers/<int:customer_id>/delete", methods=["POST"]) 
def delete_customer(customer_id: int):
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    if not router_ip or router_ip == "-":
        return redirect(url_for("customers.list_customers", error="Router IP hilang dari session."))

    cust = db.query_one(
        """
        SELECT id, ppp_username
        FROM ppp_customers
        WHERE id = %(cid)s AND reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    username = cust["ppp_username"]
    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    # 1) kill session aktif (kalau ada)
    try:
        terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
    except Exception as e:
        print(f"[delete_customer] gagal terminate session {username}: {e}")

    # 2) hapus PPP secret di router
    try:
        delete_ppp_secret(router_ip, api_user, api_pass, username)
    except MikrotikError as e:
        # lanjut saja, DB tetap dihapus supaya billing bersih
        print(f"[delete_customer] gagal delete PPP secret {username} di router: {e}")
    except Exception as e:
        print(f"[delete_customer] error delete PPP secret {username} di router: {e}")

    # 3) hapus dari DB
    try:
        db.execute(
            "DELETE FROM ppp_customers WHERE id = %(cid)s",
            {"cid": customer_id},
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"User sudah dihapus/diupayakan di router, tapi gagal hapus dari DB: {e}"))

    msg = f"User '{username}' sudah dihapus dari router (sebisa mungkin) dan DB."
    return redirect(url_for("customers.list_customers", success=msg))



# ======================================================================
# AKSI: Edit (lengkap: username, password, profil, enable/disable)
# ======================================================================

@bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id: int):
    """
    Halaman edit lengkap untuk metadata dan setting PPP:
    - ppp_username
    - ppp_password
    - profile (select)
    - is_enabled (radio)
    - full_name, address, wa_number, petugas_name, billing_start_date
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = None
    success = None

    # Ambil data customer + profil
    cust = db.query_one(
        """
        SELECT
          c.id,
          c.ppp_username,
          c.ppp_password,
          c.full_name,
          c.address,
          c.wa_number,
          c.petugas_name,
          c.billing_start_date,
          c.last_paid_period,
          c.is_enabled,
          c.profile_id,
          p.name AS profile_name
        FROM ppp_customers c
        LEFT JOIN ppp_profiles p ON p.id = c.profile_id
        WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    # profiling list untuk dropdown
    profiles = db.query_all(
        """
        SELECT id, name, is_isolation
        FROM ppp_profiles
        WHERE reseller_id = %(rid)s
        ORDER BY name
        """,

        {"rid": reseller["id"]},
    )

    if request.method == "POST":
        old_username = cust["ppp_username"]

        # username tidak boleh diubah lagi, kita abaikan input form
        new_username = old_username
        new_password = (request.form.get("ppp_password") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        address = (request.form.get("address") or "").strip()
        wa_number = (request.form.get("wa_number") or "").strip()
        petugas_name = (request.form.get("petugas_name") or "").strip()
        billing_start_raw = (request.form.get("billing_start_date") or "").strip()
        profile_id_raw = (request.form.get("profile_id") or "").strip()
        is_enabled_raw = request.form.get("is_enabled") or "1"


        billing_start_date = None
        if billing_start_raw:
            try:
                billing_start_date = datetime.date.fromisoformat(billing_start_raw)
            except ValueError:
                error = "Format tanggal Billing Start harus YYYY-MM-DD."

        # parse profile_id 
        new_profile_id = None
        new_profile_name = None
        new_is_isolated = False  # default

        if profile_id_raw:
            try:
                new_profile_id = int(profile_id_raw)
            except ValueError:
                error = "Profile ID tidak valid."

        # cari nama profile + status isolasi untuk router & DB
        if new_profile_id:
            for p in profiles:
                if p["id"] == new_profile_id:
                    new_profile_name = p["name"]
                    new_is_isolated = bool(p.get("is_isolation", False))
                    break
            if new_profile_name is None:
                error = "Profile yang dipilih tidak ditemukan."
        else:
            # jika tidak pilih profile, anggap bukan isolasi
            new_is_isolated = False


        new_is_enabled = (is_enabled_raw == "1")
        mt_disabled = "no" if new_is_enabled else "yes"

        if not error:
            # Update DB terlebih dahulu
            try:
                db.execute(
                    """
                    UPDATE ppp_customers
                    SET ppp_username      = %(user)s,
                        ppp_password      = %(pwd)s,
                        full_name         = %(fn)s,
                        address           = %(addr)s,
                        wa_number         = %(wa)s,
                        petugas_name      = %(pt)s,
                        billing_start_date= %(bsd)s,
                        profile_id        = %(pid)s,
                        is_enabled        = %(en)s,
                        is_isolated       = %(iso)s,
                        updated_at        = NOW()

                    WHERE id = %(cid)s
                      AND reseller_id = %(rid)s
                    """,
                    {
                        "user": new_username,
                        "pwd": new_password or cust["ppp_password"],
                        "fn": full_name or None,
                        "addr": address or None,
                        "wa": wa_number or None,
                        "pt": petugas_name or None,
                        "bsd": billing_start_date,
                        "pid": new_profile_id,
                        "en": new_is_enabled,
                        "cid": customer_id,
                        "rid": reseller["id"],
                        "iso": new_is_isolated,
                    },
                )
            except Exception as e:
                error = f"Gagal update customer di DB: {e}"

                # Update router PPP secret (tanpa rename username)
        if not error and router_ip and router_ip != "-":
            api_user = reseller["router_username"]
            api_pass = reseller["router_password"]

            updates = {"disabled": mt_disabled}
            if new_password:
                updates["password"] = new_password
            if new_profile_name:
                updates["profile"] = new_profile_name


            try:
                update_ppp_secret(
                    router_ip,
                    api_user,
                    api_pass,
                    secret_name=old_username,
                    updates=updates,
                )
            except MikrotikError as e:
                error = f"DB sudah berubah, tapi gagal update PPP secret di router: {e}"
            except Exception as e:
                error = f"DB sudah berubah, tapi error update PPP secret di router: {e}"

        if not error:
            success = "Data dan PPP secret berhasil diperbarui."
            # refresh data
            cust = db.query_one(
                """
                SELECT
                  c.id,
                  c.ppp_username,
                  c.ppp_password,
                  c.full_name,
                  c.address,
                  c.wa_number,
                  c.petugas_name,
                  c.billing_start_date,
                  c.last_paid_period,
                  c.is_enabled,
                  c.profile_id,
                  p.name AS profile_name
                FROM ppp_customers c
                LEFT JOIN ppp_profiles p ON p.id = c.profile_id
                WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s
                """,
                {"cid": customer_id, "rid": reseller["id"]},
            )

    body_html = """
<!-- HEADER -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <a href="{{ url_for('customers.list_customers') }}" class="hover:text-emerald-300">Customers</a>
      <span>›</span>
      <span class="text-slate-300">Edit</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>✏️</span>
      <span>Edit Customer</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Reseller: <span class="font-medium text-slate-200">{{ reseller_name }}</span><br>
      User PPP: <span class="font-mono text-emerald-300">{{ cust.ppp_username }}</span>
    </p>
  </div>
</section>

{% if error %}
  <div class="mt-4 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ {{ error }}
  </div>
{% endif %}
{% if success %}
  <div class="mt-4 rounded-md border border-emerald-500/70 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
    ✅ {{ success }}
  </div>
{% endif %}

<form method="post" class="mt-4 space-y-4 max-w-xl">

  <!-- PPP SECRET -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">🔑 PPP Secret</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          PPP Username
        </label>
        <input
          type="text"
          name="ppp_username"
          value="{{ cust.ppp_username }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          PPP Password <span class="text-slate-400">(biarkan kosong jika tidak diubah)</span>
        </label>
        <input
          type="password"
          name="ppp_password"
          value=""
          placeholder="••••••••"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
    </div>
  </section>

  <!-- PROFIL & STATUS -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">📡 Profil PPP &amp; Status</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Profil PPP
        </label>
        <select
          name="profile_id"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
          <option value="">-- pilih profile --</option>
          {% for p in profiles %}
            <option value="{{ p.id }}" {% if cust.profile_id == p.id %}selected{% endif %}>
              {{ p.name }}
            </option>
          {% endfor %}
        </select>
      </div>

      <div class="space-y-1">
        <span class="block text-xs font-medium text-slate-300">Status User</span>
        <div class="flex flex-wrap gap-4 text-xs text-slate-200">
          <label class="inline-flex items-center gap-2">
            <input
              type="radio"
              name="is_enabled"
              value="1"
              {% if cust.is_enabled %}checked{% endif %}
              class="h-3 w-3 rounded border-slate-600 bg-slate-900"
            >
            <span>Enable</span>
          </label>
          <label class="inline-flex items-center gap-2">
            <input
              type="radio"
              name="is_enabled"
              value="0"
              {% if not cust.is_enabled %}checked{% endif %}
              class="h-3 w-3 rounded border-slate-600 bg-slate-900"
            >
            <span>Disable</span>
          </label>
        </div>
      </div>
    </div>
  </section>

  <!-- DATA PELANGGAN -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">👤 Data Pelanggan</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Nama Lengkap
        </label>
        <input
          type="text"
          name="full_name"
          value="{{ cust.full_name or '' }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Alamat
        </label>
        <textarea
          name="address"
          rows="3"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >{{ cust.address or '' }}</textarea>
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          No. WhatsApp
        </label>
        <input
          type="text"
          name="wa_number"
          value="{{ cust.wa_number or '' }}"
          placeholder="6285xxxx"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Nama Petugas
        </label>
        <input
          type="text"
          name="petugas_name"
          value="{{ cust.petugas_name or '' }}"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
    </div>
  </section>

  <!-- BILLING -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">💳 Billing</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          Billing Start Date
        </label>
        <input
          type="date"
          name="billing_start_date"
          value="{{ cust.billing_start_date or '' }}"
          class="w-52 max-w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none"
        >
      </div>
      <p class="text-[11px] text-slate-500">
        Tanggal ini digunakan sebagai acuan awal periode tagihan customer ini.
      </p>
    </div>
  </section>

  <!-- TOMBOL AKSI -->
  <div class="flex flex-wrap items-center gap-2 pt-1">
    <button
      type="submit"
      class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-4 py-2 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
      💾 <span>Simpan Perubahan</span>
    </button>

    <a href="{{ url_for('customers.list_customers') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      ⬅️ <span>Kembali</span>
    </a>
  </div>
</form>
    """

    return render_terminal_page(
        title="Edit Customer",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "cust": cust,
            "profiles": profiles,
            "error": error,
            "success": success,
        },
    )


# ======================================================================
# AKSI: Bayar
# ======================================================================

@bp.route("/customers/<int:customer_id>/pay", methods=["POST"])
def pay_customer(customer_id: int):
    """
    Aksi bayar sederhana:
    - Menandai last_paid_period untuk customer ini.
    - months (int) = jumlah bulan yang dibayar, default 1.
    - Pembayaran dihitung mulai bulan berjalan:
        last_paid_period = current_period + (months-1) bulan.
    """
    reseller, _, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    raw_months = (request.form.get("months") or "1").strip()
    try:
        months = int(raw_months)
    except ValueError:
        months = 1
    if months < 1:
        months = 1

    today = datetime.date.today()
    current_period = today.replace(day=1)

    cust = db.query_one(
        """
        SELECT id, ppp_username
        FROM ppp_customers
        WHERE id = %(cid)s AND reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    try:
        db.execute(
            """
            UPDATE ppp_customers
            SET last_paid_period = (
                    date_trunc('month', %(cp)s::timestamp)
                    + ((%(m)s::int - 1) * INTERVAL '1 month')
                )::date,
                updated_at = NOW()
            WHERE id = %(cid)s
              AND reseller_id = %(rid)s
            """,
            {
                "cp": current_period,
                "m": months,
                "cid": customer_id,
                "rid": reseller["id"],
            },
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal update pembayaran customer: {e}"))

    msg = f"Pembayaran {months} bulan tercatat untuk user {cust['ppp_username']}."
    return redirect(url_for("customers.list_customers", success=msg))
@bp.route("/customers/<int:customer_id>/cancel-pay", methods=["POST"])
def cancel_pay_customer(customer_id: int):
    """
    Membatalkan pembayaran terakhir (mundurkan last_paid_period).
    - months (int) = jumlah bulan yang dibatalkan, default 1.
    - Jika last_paid_period NULL, tidak ada yang dibatalkan.
    """
    reseller, _, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    raw_months = (request.form.get("months") or "1").strip()
    try:
        months = int(raw_months)
    except ValueError:
        months = 1
    if months < 1:
        months = 1

    cust = db.query_one(
        """
        SELECT id, ppp_username, last_paid_period
        FROM ppp_customers
        WHERE id = %(cid)s AND reseller_id = %(rid)s
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )
    if cust is None:
        return redirect(url_for("customers.list_customers", error="Customer tidak ditemukan."))

    if not cust["last_paid_period"]:
        return redirect(url_for("customers.list_customers", error=f"Tidak ada last_paid_period untuk user {cust['ppp_username']}."))

    try:
        db.execute(
            """
            UPDATE ppp_customers
            SET last_paid_period = (
                    (last_paid_period::timestamp)
                    - (%(m)s::int * INTERVAL '1 month')
                )::date,
                updated_at = NOW()
            WHERE id = %(cid)s
              AND reseller_id = %(rid)s
            """,
            {
                "m": months,
                "cid": customer_id,
                "rid": reseller["id"],
            },
        )
    except Exception as e:
        return redirect(url_for("customers.list_customers", error=f"Gagal membatalkan pembayaran customer: {e}"))

    msg = f"Pembayaran {months} bulan terakhir dibatalkan untuk user {cust['ppp_username']}."
    return redirect(url_for("customers.list_customers", success=msg))
