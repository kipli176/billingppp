# blueprints/main.py

from __future__ import annotations

import datetime
from urllib.parse import quote_plus

from flask import (
    Blueprint,
    session,
    redirect,
    url_for,
    request,
    jsonify,
)

import db
from app import render_terminal_page
from mikrotik_client import (
    get_system_resource,
    get_system_identity,
    get_ppp_active,
    get_ppp_profiles,
    MikrotikError,
)

bp = Blueprint("main", __name__)


# ======================================================================
# Helper login reseller
# ======================================================================

def _get_logged_in_reseller():
    """
    Ambil reseller yang sedang login beserta router_ip dari session.
    Return: (reseller_row, router_ip) atau (None, None) kalau tidak login.
    """
    reseller_id = session.get("reseller_id")
    router_ip = session.get("router_ip")

    if not reseller_id:
        return None, None

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
        return None, None

    if not router_ip:
        router_ip = "-"

    return reseller, router_ip


# ======================================================================
# DASHBOARD
# ======================================================================

@bp.route("/dashboard")
def dashboard():
    """
    Dashboard utama reseller.

    LOGIKA:
    1) Cek invoice reseller untuk periode bulan ini.
       - Jika hari > 5 dan invoice bulan ini status != 'paid' → LOCK MODE:
         hanya tampil tabel invoice + tombol WA bayar ke admin.
    2) Jika tidak terkunci:
       - Tampilkan router status
       - Info reseller
       - Ringkasan user
       - (BARU) Tabel profil PPP + tombol sinkron dan edit harga/isolasi.
    """

    reseller, router_ip = _get_logged_in_reseller()
    if reseller is None:
        return redirect(url_for("auth_reseller.login"))

    reseller_name = reseller["display_name"] or reseller["router_username"]
    router_username = reseller["router_username"]
    router_password = reseller["router_password"]

    # ------------------------------------------------------------------
    # Cek invoice bulan ini (lock dashboard kalau belum bayar)
    # ------------------------------------------------------------------
    today = datetime.date.today()
    current_period_start = today.replace(day=1)

    locked_by_invoice = False
    current_invoice = None
    wa_pay_url = None
    db_error = None

    try:
        current_invoice = db.query_one(
            """
            SELECT *
            FROM v_reseller_invoices
            WHERE reseller_id = %(rid)s
              AND period_start = %(ps)s
            ORDER BY period_start DESC
            LIMIT 1
            """,
            {"rid": reseller["id"], "ps": current_period_start},
        )
    except Exception as e:
        db_error = f"Gagal mengambil invoice bulan ini: {e}"

    if current_invoice and current_invoice["status"] != "paid" and today.day > 10:
        locked_by_invoice = True
        # Bangun pesan WA ke admin (08562603077 -> 628562603077)
        msg = (
            f"Halo admin, saya reseller {reseller_name} ingin membayar invoice "
            f"ID {current_invoice['invoice_id']} "
            f"periode {current_invoice['period_start']} s/d {current_invoice['period_end']} "
            f"dengan total Rp {current_invoice['total_amount']:,}. "
            f"Status saat ini: {current_invoice['status']}."
        )
        wa_pay_url = "https://wa.me/628562603077?text=" + quote_plus(msg)

    # Kalau terkunci invoice → tampilkan hanya info invoice + WA admin
    if locked_by_invoice:
        body_html = """
<section class="mt-6 rounded-lg border border-amber-500/60 bg-amber-500/10 p-6 shadow-md">
  <h1 class="flex items-center gap-2 text-lg font-semibold text-amber-300 mb-2">
    <span>📑</span>
    <span>Invoice Bulan Ini (WAJIB DIBAYAR)</span>
  </h1>
  <p class="text-sm text-amber-100 mb-4">
    ⚠️ Sistem dikunci karena invoice bulan ini belum dibayar.<br>
    Silakan selesaikan pembayaran ke admin agar fitur lain aktif kembali.
  </p>

  {% if db_error %}
    <div class="mb-3 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
      ⚠️ DB info: {{ db_error }}
    </div>
  {% endif %}

  {% if invoice %}
  <div class="rounded-md border border-slate-800 bg-slate-900/70 p-4 text-sm text-slate-200">
    <table class="min-w-full border-collapse text-xs">
      <tbody>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">ID Invoice</th>
          <td class="py-1 font-mono text-slate-100">{{ invoice.invoice_id }}</td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Periode</th>
          <td class="py-1 text-slate-100">
            {{ invoice.period_start }} s/d {{ invoice.period_end }}
          </td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Status</th>
          <td class="py-1">
            {% if invoice.status == 'paid' %}
              <span class="inline-flex items-center rounded-full border border-emerald-500/70 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
                ✅ PAID
              </span>
            {% elif invoice.status == 'overdue' %}
              <span class="inline-flex items-center rounded-full border border-amber-500/70 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-300">
                ⏰ OVERDUE
              </span>
            {% else %}
              <span class="inline-flex items-center rounded-full border border-slate-500/70 bg-slate-800/50 px-2 py-0.5 text-[11px] text-slate-300">
                ⏳ {{ invoice.status }}
              </span>
            {% endif %}
          </td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Total Enabled Users</th>
          <td class="py-1 font-mono">{{ invoice.total_enabled_users }}</td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Tarif/User</th>
          <td class="py-1 text-slate-100">
            Rp {{ '{:,.0f}'.format(invoice.price_per_user) }}
          </td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Total Tagihan</th>
          <td class="py-1 font-semibold text-amber-300">
            Rp {{ '{:,.0f}'.format(invoice.total_amount) }}
          </td>
        </tr>
        <tr class="border-b border-slate-800/60">
          <th class="py-1 pr-3 text-left text-slate-400">Jatuh Tempo</th>
          <td class="py-1 text-slate-100">{{ invoice.due_date }}</td>
        </tr>
        <tr>
          <th class="py-1 pr-3 text-left text-slate-400">Link Pembayaran (WA Admin)</th>
          <td class="py-1">
            <a href="{{ wa_pay_url }}" target="_blank"
               class="inline-flex items-center gap-1 rounded-md border border-emerald-500 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20">
              💬 Chat Admin untuk Bayar
            </a>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  {% else %}
    <p class="mt-3 text-sm text-slate-400">Invoice bulan ini tidak ditemukan, hubungi admin.</p>
  {% endif %}

  <p class="mt-4 text-xs text-slate-400">
    Setelah pembayaran dikonfirmasi oleh admin,
    status invoice akan diubah menjadi <b class="text-emerald-300">PAID</b> dan dashboard akan terbuka kembali.
  </p>
</section>
        """

        return render_terminal_page(
            title="Invoice Wajib Dibayar",
            body_html=body_html,
            context={
                "reseller_name": reseller_name,
                "invoice": current_invoice,
                "wa_pay_url": wa_pay_url,
                "db_error": db_error,
            },
        )

    # ------------------------------------------------------------------
    # TIDAK terkunci invoice → dashboard normal
    # ------------------------------------------------------------------

    # 1) Data router via REST Mikrotik
    router_error = None
    router_name = "N/A"
    uptime = "N/A"
    cpu_load = "N/A"
    mem_display = "N/A"
    active_ppp_count = None
    cpu_percent = None
    mem_used_pct = None

    if router_ip != "-":
        try:
            identity = get_system_identity(router_ip, router_username, router_password)
            resource = get_system_resource(router_ip, router_username, router_password)
            active_list = get_ppp_active(router_ip, router_username, router_password)

            router_name = identity.get("name") or "N/A"
            uptime = resource.get("uptime") or "N/A"
            cpu_load = resource.get("cpu-load") or resource.get("cpu_load") or "N/A"

            # coba konversi ke int untuk progress bar
            try:
                cpu_percent = int(str(cpu_load))
            except Exception:
                cpu_percent = None
            free_mem = resource.get("free-memory") or resource.get("free_memory")
            total_mem = resource.get("total-memory") or resource.get("total_memory")

            def _fmt_bytes(b):
                try:
                    b = int(b)
                except Exception:
                    return "N/A"
                mb = b / (1024 * 1024)
                return f"{mb:.0f} MB"

            if free_mem is not None and total_mem is not None:
                mem_display = f"{_fmt_bytes(total_mem)} total / {_fmt_bytes(free_mem)} free"
                # hitung persentase RAM terpakai
                try:
                    total_i = int(total_mem)
                    free_i = int(free_mem)
                    used_i = total_i - free_i
                    mem_used_pct = int(used_i * 100 / total_i)
                except Exception:
                    mem_used_pct = None
            else:
                mem_display = "N/A"

            active_ppp_count = len(active_list) if isinstance(active_list, list) else None

        except MikrotikError as e:
            router_error = str(e)
        except Exception as e:
            router_error = f"Error tidak terduga saat akses router: {e}"
    else:
        router_error = "Router IP tidak tersedia di session. Silakan login ulang."

    # 2) Ringkasan billing & user
    unpaid_count = 0
    unpaid_total = 0

    try:
        unpaid_summary = db.query_one(
            """
            SELECT unpaid_customer_count, unpaid_total_amount
            FROM v_reseller_unpaid_summary
            WHERE reseller_id = %(rid)s
            """,
            {"rid": reseller["id"]},
        )
        if unpaid_summary:
            unpaid_count = unpaid_summary.get("unpaid_customer_count") or 0
            unpaid_total = unpaid_summary.get("unpaid_total_amount") or 0
    except Exception as e:
        if db_error:
            db_error += f" | Gagal mengambil ringkasan unpaid: {e}"
        else:
            db_error = f"Gagal mengambil ringkasan unpaid: {e}"

    total_users = paid_users = unpaid_users = isolated_users = disabled_users = 0
    try:
        user_stats = db.query_one(
            """
            SELECT
              COUNT(*) AS total_users,
              COUNT(*) FILTER (WHERE payment_status_text = 'paid_current_period') AS paid_current,
              COUNT(*) FILTER (WHERE payment_status_text = 'unpaid_current_period') AS unpaid_current,
              COUNT(*) FILTER (WHERE payment_status_text = 'isolated') AS isolated,
              COUNT(*) FILTER (WHERE is_enabled = FALSE) AS disabled
            FROM v_payment_status_detail
            WHERE reseller_id = %(rid)s
            """,
            {"rid": reseller["id"]},
        ) or {}
        total_users = user_stats.get("total_users", 0) or 0
        paid_users = user_stats.get("paid_current", 0) or 0
        unpaid_users = user_stats.get("unpaid_current", 0) or 0
        isolated_users = user_stats.get("isolated", 0) or 0
        disabled_users = user_stats.get("disabled", 0) or 0
    except Exception as e:
        if db_error:
            db_error += f" | Gagal mengambil statistik user: {e}"
        else:
            db_error = f"Gagal mengambil statistik user: {e}"

    # 3) Data profil untuk section di bawah summary
    profile_error = request.args.get("p_error") or None
    profile_success = request.args.get("p_success") or None
    profiles = []
    try:
        profiles = db.query_all(
            """
            SELECT profile_id, reseller_id, reseller_name,
                   profile_name, description, rate_limit,
                   is_isolation, monthly_price,
                   total_customers, enabled_customers
            FROM v_profiles
            WHERE reseller_id = %(rid)s
            ORDER BY profile_name
            """,
            {"rid": reseller["id"]},
        )
    except Exception as e:
        profile_error = f"Gagal mengambil data profil: {e}"


    # ------------------------------------------------------------------
    # HTML body untuk dashboard (tanpa menu cepat, profil di bawah summary)
    # ------------------------------------------------------------------
    body_html = """ 
<!-- PAGE HEADER -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-center md:justify-between">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <span class="text-slate-300">Dashboard</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>🏠</span>
      <span>Dashboard</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Ringkasan status router dan pelanggan PPP untuk reseller
      <span class="font-medium text-slate-200">{{ reseller_name }}</span>.
    </p>
  </div>

  <!-- Action buttons -->
  <div class="flex flex-wrap gap-2">
    <button
      type="button"
      onclick="fetchStats()"
      class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
      🔄 <span>Refresh status</span>
    </button>
    <a href="{{ url_for('reseller_settings.settings') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      ⚙️ <span>Settings</span>
    </a>
  </div>
</section>

{% if db_error %}
  <div class="mt-3 rounded-md border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ {{ db_error }}
  </div>
{% endif %}
{% if router_error %}
  <div class="mt-3 rounded-md border border-rose-500/60 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
    ⚠️ {{ router_error }}
  </div>
{% endif %}

<!-- GRID KARTU ATAS -->
<section class="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">

  <!-- CARD 1: ROUTER STATUS -->
  <div class="rounded-lg border border-slate-800 bg-slate-900/60 p-4 shadow-sm">
    <div class="flex items-center justify-between gap-2">
      <h2 class="text-sm font-semibold text-slate-200">📡 Router Status</h2>
      <span class="inline-flex items-center gap-1 rounded-full border border-emerald-500/60 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
        <span class="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
        Live
      </span>
    </div>
    <dl class="mt-3 space-y-1 text-xs text-slate-300">
      <div class="flex justify-between">
        <dt>IP</dt>
        <dd id="router-ip" class="font-mono text-emerald-300">{{ router_ip }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>Identity</dt>
        <dd id="router-name" class="font-mono text-slate-200">{{ router_name }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>Uptime</dt>
        <dd id="router-uptime" class="font-mono text-slate-200">{{ uptime }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>Active PPP</dt>
        <dd id="router-active-ppp" class="font-mono text-emerald-300">
          {% if active_ppp_count is not none %}
            {{ active_ppp_count }}
          {% else %}
            N/A
          {% endif %}
        </dd>
      </div>
    </dl>

    <div class="mt-4 space-y-3 text-xs">
      <div>
        <div class="flex justify-between">
          <span class="text-slate-400">CPU Load</span>
          <span id="cpu-value" class="font-mono text-emerald-300">
            {% if cpu_load == "N/A" %}N/A{% else %}{{ cpu_load }}%{% endif %}
          </span>
        </div>
        <div class="mt-1 h-1.5 overflow-hidden rounded-full border border-slate-700 bg-slate-950">
          <div id="cpu-meter-fill"
               class="h-full bg-emerald-500"
               style="width: {% if cpu_percent is not none %}{{ cpu_percent }}{% else %}0{% endif %}%;">
          </div>
        </div>
      </div>
      <div>
        <div class="flex justify-between">
          <span class="text-slate-400">Memory</span>
          <span id="mem-text" class="font-mono text-emerald-300">{{ mem_display }}</span>
        </div>
        <div class="mt-1 h-1.5 overflow-hidden rounded-full border border-slate-700 bg-slate-950">
          <div id="mem-meter-fill"
               class="h-full bg-emerald-500"
               style="width: {% if mem_used_pct is not none %}{{ mem_used_pct }}{% else %}0{% endif %}%;">
          </div>
        </div>
      </div>
    </div>

    <p class="mt-3 text-[11px] text-slate-500" id="last-update">
      live update...
    </p>
  </div>

  <!-- CARD 2: RESELLER INFO -->
  <div class="rounded-lg border border-slate-800 bg-slate-900/60 p-4 shadow-sm">
    <h2 class="text-sm font-semibold text-slate-200">🧩 Reseller Info</h2>
    <dl class="mt-3 space-y-1 text-xs text-slate-300">
      <div class="flex justify-between">
        <dt>Nama</dt>
        <dd class="font-medium text-slate-100">{{ reseller_name }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>WA</dt>
        <dd class="font-mono text-emerald-300">{{ reseller_wa or '-' }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>Email</dt>
        <dd class="text-emerald-300">{{ reseller_email or '-' }}</dd>
      </div>
      <div class="flex justify-between">
        <dt>Notif WA</dt>
        <dd>
          {% if use_notifications %}
            <span class="inline-flex items-center rounded-full border border-emerald-500/70 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
              ON · aktif
            </span>
          {% else %}
            <span class="inline-flex items-center rounded-full border border-slate-600 bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
              OFF · nonaktif
            </span>
          {% endif %}
        </dd>
      </div>
      <div class="flex justify-between">
        <dt>Auto Payment</dt>
        <dd>
          {% if use_auto_payment %}
            <span class="inline-flex items-center rounded-full border border-emerald-500/70 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">
              ON · aktif
            </span>
          {% else %}
            <span class="inline-flex items-center rounded-full border border-slate-600 bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
              OFF · nonaktif
            </span>
          {% endif %}
        </dd>
      </div>
    </dl>
    <p class="mt-3 text-xs text-slate-400">
      Unpaid bulan ini:
      <span class="font-mono text-amber-300">{{ unpaid_count }} user</span><br>
      <span class="text-amber-300">Rp {{ "{:,.0f}".format(unpaid_total) }}</span>
    </p>
  </div>

  <!-- CARD 3: USER SUMMARY -->
  <div class="rounded-lg border border-slate-800 bg-slate-900/60 p-4 shadow-sm">
    <h2 class="text-sm font-semibold text-slate-200">👤 User Summary</h2>
    <div class="mt-3 grid grid-cols-2 gap-2 text-center text-xs">
      <div class="rounded-md border border-slate-700 px-2 py-2">
        <div class="text-slate-400">Total</div>
        <div class="mt-1 text-lg font-semibold text-slate-100">{{ total_users }}</div>
      </div>
      <div class="rounded-md border border-emerald-500/70 px-2 py-2">
        <div class="text-slate-400">Paid</div>
        <div class="mt-1 text-lg font-semibold text-emerald-300">{{ paid_users }}</div>
      </div>
      <div class="rounded-md border border-amber-400/80 px-2 py-2">
        <div class="text-slate-400">Unpaid</div>
        <div class="mt-1 text-lg font-semibold text-amber-300">{{ unpaid_users }}</div>
      </div>
      <div class="rounded-md border border-rose-500/80 px-2 py-2">
        <div class="text-slate-400">Isolated</div>
        <div class="mt-1 text-lg font-semibold text-rose-300">{{ isolated_users }}</div>
      </div>
      <!--div class="col-span-2 rounded-md border border-slate-500 px-2 py-2">
        <div class="text-slate-400">Disabled</div>
        <div class="mt-1 text-lg font-semibold text-slate-200">{{ disabled_users }}</div>
      </div-->
    </div>
    <p class="mt-3 text-xs text-slate-400">
      Status berdasarkan periode pembayaran berjalan.
    </p>
  </div>
</section>

<!-- PPP PROFILES -->
<section class="mt-6 rounded-lg border border-slate-800 bg-slate-900/60 p-4 shadow-sm">
  <div class="mb-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
    <div>
      <h2 class="text-sm font-semibold text-slate-200">📡 PPP Profiles</h2>
      <p class="text-xs text-slate-400">
        Profil PPP dari router &amp; harga per bulan untuk billing.
      </p>
    </div>
    <form method="post" action="{{ url_for('main.sync_profiles_dashboard') }}">
      <button type="submit"
              class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
        🔄 <span>Sinkron Profil dari Router</span>
      </button>
    </form>
  </div>

  {% if profile_error %}
    <p class="mb-2 text-xs text-rose-300">⚠️ {{ profile_error }}</p>
  {% endif %}
  {% if profile_success %}
    <p class="mb-2 text-xs text-emerald-300">✅ {{ profile_success }}</p>
  {% endif %}

  {% if profiles %}
    <div class="overflow-x-auto">
      <table class="min-w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-slate-800 bg-slate-900">
            <th class="px-3 py-2 text-left font-medium text-slate-300">Nama Profil</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">Rate Limit</th>
            <th class="px-3 py-2 text-center font-medium text-slate-300">Isolation?</th>
            <th class="px-3 py-2 text-right font-medium text-slate-300">Harga /bulan</th>
            <th class="px-3 py-2 text-right font-medium text-slate-300">Total User</th>
            <th class="px-3 py-2 text-left font-medium text-slate-300">Aksi</th>
          </tr>
        </thead>
        <tbody>
          {% for p in profiles %}
            <tr class="border-b border-slate-800/70 hover:bg-slate-900/60">
              <form method="post" action="{{ url_for('main.update_profile_dashboard', profile_id=p.profile_id) }}">
                <td class="px-3 py-2 align-top">{{ p.profile_name }}</td>
                <td class="px-3 py-2 align-top font-mono text-[11px] text-slate-300">
                  {{ p.rate_limit or "-" }}
                </td>
                <td class="px-3 py-2 align-top text-center">
                  <input type="checkbox"
                         name="is_isolation"
                         {% if p.is_isolation %}checked{% endif %}
                         class="h-3 w-3 rounded border-slate-600 bg-slate-900" />
                </td>
                <td class="px-3 py-2 align-top text-right">
                  <input type="text"
                         name="monthly_price"
                         value="{{ p.monthly_price or 0 }}"
                         class="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-right font-mono text-[11px] text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0" />
                </td>
                <td class="px-3 py-2 align-top text-right">
                  {{ p.total_customers or 0 }}
                </td>
                <td class="px-3 py-2 align-top">
                  <button type="submit"
                          class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] font-medium text-slate-200 hover:border-emerald-500 hover:text-emerald-300">
                    💾 Simpan
                  </button>
                </td>
              </form>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="text-xs text-slate-400">
      Tidak ada profil di database. Coba klik <b>Sinkron Profil dari Router</b>.
    </p>
  {% endif %}

  <p class="mt-3 text-[11px] text-slate-500">
    Catatan: Sinkron profil akan membaca <code>/ppp/profile</code> di router, lalu menambah/update ke tabel
    <code>ppp_profiles</code>. Edit kolom "Isolation?" dan "Harga/bulan" hanya mengubah data billing di database,
    tidak mengubah setting router secara langsung.
  </p>
</section>

<!-- SCRIPT: LIVE STATS -->
<script>
  async function fetchStats() {
    try {
      const resp = await fetch("{{ url_for('main.dashboard_stats') }}", {
        cache: "no-store"
      });
      if (!resp.ok) {
        return;
      }
      const data = await resp.json();
      if (data.error) {
        return;
      }

      const ipEl = document.getElementById("router-ip");
      const nameEl = document.getElementById("router-name");
      const uptimeEl = document.getElementById("router-uptime");
      const activePppEl = document.getElementById("router-active-ppp");
      if (ipEl && data.router_ip !== undefined) ipEl.textContent = data.router_ip;
      if (nameEl && data.router_name !== undefined) nameEl.textContent = data.router_name;
      if (uptimeEl && data.uptime !== undefined) uptimeEl.textContent = data.uptime;
      if (activePppEl && data.active_ppp_count !== undefined && data.active_ppp_count !== null) {
        activePppEl.textContent = data.active_ppp_count;
      }

      const cpuValueEl = document.getElementById("cpu-value");
      const cpuBarEl = document.getElementById("cpu-meter-fill");
      if (cpuValueEl) {
        if (data.cpu_load === "N/A" || data.cpu_load === null) {
          cpuValueEl.textContent = "N/A";
        } else {
          cpuValueEl.textContent = data.cpu_load + "%";
        }
      }
      if (cpuBarEl) {
        let pct = parseInt(data.cpu_percent || data.cpu_load || 0, 10);
        if (isNaN(pct) || pct < 0) pct = 0;
        if (pct > 100) pct = 100;
        cpuBarEl.style.width = pct + "%";
      }

      const memTextEl = document.getElementById("mem-text");
      const memBarEl = document.getElementById("mem-meter-fill");
      if (memTextEl && data.mem_display !== undefined) {
        memTextEl.textContent = data.mem_display;
      }
      if (memBarEl) {
        let mpct = parseInt(data.mem_used_pct || 0, 10);
        if (isNaN(mpct) || mpct < 0) mpct = 0;
        if (mpct > 100) mpct = 100;
        memBarEl.style.width = mpct + "%";
      }

      const lastUpdateEl = document.getElementById("last-update");
      if (lastUpdateEl) {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        const ss = String(now.getSeconds()).padStart(2, "0");
        lastUpdateEl.textContent = "updated " + hh + ":" + mm + ":" + ss;
      }
    } catch (err) {
      // optional: console.log(err);
    }
  }

  window.addEventListener("load", function() {
    fetchStats();
    setInterval(fetchStats, 3000);
  });
</script>
    """


    return render_terminal_page(
        title="Dashboard",
        body_html=body_html,
        context={
            "reseller_name": reseller_name,
            "router_error": router_error,
            "db_error": db_error,
            "router_ip": router_ip,
            "router_name": router_name,
            "uptime": uptime,
            "cpu_load": cpu_load,
            "mem_display": mem_display,
            "active_ppp_count": active_ppp_count,
            "cpu_percent": cpu_percent,
            "mem_used_pct": mem_used_pct,
            "reseller_wa": reseller.get("wa_number"),
            "reseller_email": reseller.get("email"),
            "use_notifications": reseller.get("use_notifications", False),
            "use_auto_payment": reseller.get("use_auto_payment", False),
            "unpaid_count": unpaid_count,
            "unpaid_total": unpaid_total,
            "total_users": total_users,
            "paid_users": paid_users,
            "unpaid_users": unpaid_users,
            "isolated_users": isolated_users,
            "disabled_users": disabled_users,
            "profiles": profiles,
            "profile_error": profile_error,
            "profile_success": profile_success,
        },
    )


# ======================================================================
# AKSI PROFIL dari DASHBOARD
# ======================================================================
@bp.route("/dashboard/stats")
def dashboard_stats():
    """
    Endpoint ringan untuk mengembalikan status router dalam bentuk JSON.
    Dipakai oleh JavaScript di dashboard untuk update progress bar CPU/RAM.
    """
    reseller, router_ip = _get_logged_in_reseller()
    if reseller is None:
        return jsonify({"error": "not_logged_in"}), 401

    router_username = reseller["router_username"]
    router_password = reseller["router_password"]

    cpu_percent = None
    mem_used_pct = None
    cpu_load = "N/A"
    mem_display = "N/A"
    uptime = "N/A"
    active_ppp_count = None
    router_name = "N/A"
    router_error = None

    if router_ip != "-":
        try:
            identity = get_system_identity(router_ip, router_username, router_password)
            resource = get_system_resource(router_ip, router_username, router_password)
            active_list = get_ppp_active(router_ip, router_username, router_password)

            router_name = identity.get("name") or "N/A"
            uptime = resource.get("uptime") or "N/A"
            cpu_load = resource.get("cpu-load") or resource.get("cpu_load") or "N/A"

            # CPU %
            try:
                cpu_percent = int(str(cpu_load))
            except Exception:
                cpu_percent = None

            free_mem = resource.get("free-memory") or resource.get("free_memory")
            total_mem = resource.get("total-memory") or resource.get("total_memory")

            def _fmt_bytes(b):
                try:
                    b = int(b)
                except Exception:
                    return "N/A"
                mb = b / (1024 * 1024)
                return f"{mb:.0f} MB"

            if free_mem is not None and total_mem is not None:
                mem_display = f"{_fmt_bytes(total_mem)} total / {_fmt_bytes(free_mem)} free"
                try:
                    total_i = int(total_mem)
                    free_i = int(free_mem)
                    used_i = total_i - free_i
                    mem_used_pct = int(used_i * 100 / total_i)
                except Exception:
                    mem_used_pct = None
            else:
                mem_display = "N/A"

            active_ppp_count = len(active_list) if isinstance(active_list, list) else None

        except MikrotikError as e:
            router_error = str(e)
        except Exception as e:
            router_error = f"Error tidak terduga saat akses router: {e}"
    else:
        router_error = "Router IP tidak tersedia di session. Silakan login ulang."

    return jsonify({
        "router_ip": router_ip,
        "router_name": router_name,
        "uptime": uptime,
        "cpu_load": cpu_load,
        "cpu_percent": cpu_percent,
        "mem_display": mem_display,
        "mem_used_pct": mem_used_pct,
        "active_ppp_count": active_ppp_count,
        "router_error": router_error,
    })

@bp.route("/dashboard/profiles/sync", methods=["POST"]) 
def sync_profiles_dashboard():
    """
    Sinkron profil dari router reseller (dipanggil dari dashboard):
    - Ambil /ppp/profile dari Mikrotik reseller.
    - Untuk setiap profil:
        * kalau sudah ada (reseller_id + name) → UPDATE description & rate_limit
        * kalau belum ada → INSERT baru (is_isolation = FALSE, monthly_price = 0)
    """
    reseller, router_ip = _get_logged_in_reseller()
    if reseller is None:
        return redirect(url_for("auth_reseller.login"))

    if not router_ip or router_ip == "-":
        return redirect(
            url_for(
                "main.dashboard",
                p_error="Router IP tidak tersedia di session. Silakan login ulang.",
            )
        )

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    # 1. Ambil profil dari router
    try:
        mt_profiles = get_ppp_profiles(router_ip, api_user, api_pass)
    except MikrotikError as e:
        return redirect(
            url_for("main.dashboard", p_error=f"Gagal mengambil profil dari router: {e}")
        )
    except Exception as e:
        return redirect(
            url_for("main.dashboard", p_error=f"Error tidak terduga saat akses router: {e}")
        )

    if not mt_profiles:
        return redirect(
            url_for("main.dashboard", p_error="Router tidak mengembalikan data profil PPP.")
        )

    # 2. Loop dan merge ke ppp_profiles (tanpa ON CONFLICT)
    inserted = 0
    updated = 0

    for prof in mt_profiles:
        if not isinstance(prof, dict):
            continue

        name = prof.get("name")
        if not name:
            continue

        desc = prof.get("comment") or prof.get("description") or None
        rate_limit = prof.get("rate-limit") or prof.get("rate_limit") or None

        try:
            # cek apakah profile sudah ada untuk reseller ini
            existing = db.query_one(
                """
                SELECT id
                FROM ppp_profiles
                WHERE reseller_id = %(rid)s
                  AND name = %(name)s
                """,
                {"rid": reseller["id"], "name": name},
            )

            if existing:
                # sudah ada → update deskripsi & rate limit
                db.execute(
                    """
                    UPDATE ppp_profiles
                    SET description = %(desc)s,
                        rate_limit  = %(rate)s,
                        updated_at  = NOW()
                    WHERE id = %(id)s
                    """,
                    {
                        "id": existing["id"],
                        "desc": desc,
                        "rate": rate_limit,
                    },
                )
                updated += 1
            else:
                # belum ada → insert baru
                db.execute(
                    """
                    INSERT INTO ppp_profiles
                        (reseller_id, name, description, rate_limit,
                         is_isolation, monthly_price, created_at, updated_at)
                    VALUES
                        (%(rid)s, %(name)s, %(desc)s, %(rate)s,
                         FALSE, 0, NOW(), NOW())
                    """,
                    {
                        "rid": reseller["id"],
                        "name": name,
                        "desc": desc,
                        "rate": rate_limit,
                    },
                )
                inserted += 1

        except Exception as e:
            print(f"[sync_profiles_dashboard] gagal sinkron profile {name}: {e}")

    msg = f"Sinkron profil selesai. {inserted} profil baru, {updated} profil diperbarui."
    return redirect(url_for("main.dashboard", p_success=msg))



@bp.route("/dashboard/profiles/<int:profile_id>/update", methods=["POST"])
def update_profile_dashboard(profile_id: int):
    """
    Update simple profil dari dashboard:
    - monthly_price (int)
    - is_isolation (bool)
    """
    reseller, _ = _get_logged_in_reseller()
    if reseller is None:
        return redirect(url_for("auth_reseller.login"))

    # Ambil input form
    raw_price = (request.form.get("monthly_price") or "").replace(".", "").replace(",", "").strip()
    is_iso = request.form.get("is_isolation") == "on"

    try:
        price = int(raw_price) if raw_price else 0
    except ValueError:
        return redirect(url_for("main.dashboard", p_error="Harga profil harus berupa angka."))

    try:
        db.execute(
            """
            UPDATE ppp_profiles
            SET monthly_price = %(price)s,
                is_isolation  = %(iso)s,
                updated_at    = NOW()
            WHERE id = %(pid)s
              AND reseller_id = %(rid)s
            """,
            {
                "price": price,
                "iso": is_iso,
                "pid": profile_id,
                "rid": reseller["id"],
            },
        )
    except Exception as e:
        return redirect(url_for("main.dashboard", p_error=f"Gagal update profil: {e}"))

    return redirect(url_for("main.dashboard", p_success="Profil berhasil diperbarui."))
