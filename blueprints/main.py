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

    if current_invoice and current_invoice["status"] != "paid" and today.day > 5:
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
<h1>📑 Invoice Bulan Ini (WAJIB DIBAYAR)</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

<p style="color:#ff5555; font-weight:bold;">
  ⚠️ Sistem dikunci karena invoice bulan ini belum dibayar.
  Silakan selesaikan pembayaran ke admin agar fitur lain aktif kembali.
</p>

{% if db_error %}
  <p style="color:#ff5555;">⚠️ DB info: {{ db_error }}</p>
{% endif %}

{% if invoice %}
  <div style="border:1px solid #0f0; padding:8px; max-width:720px;">
    <table>
      <tr><th>ID Invoice</th><td>{{ invoice.invoice_id }}</td></tr>
      <tr><th>Periode</th><td>{{ invoice.period_start }} s/d {{ invoice.period_end }}</td></tr>
      <tr><th>Status</th>
        <td>
          {% if invoice.status == 'paid' %}
            ✅ PAID
          {% elif invoice.status == 'overdue' %}
            ⏰ OVERDUE
          {% else %}
            ⏳ {{ invoice.status }}
          {% endif %}
        </td>
      </tr>
      <tr><th>Total Enabled Users</th><td>{{ invoice.total_enabled_users }}</td></tr>
      <tr><th>Tarif/User</th><td>Rp {{ '{:,.0f}'.format(invoice.price_per_user) }}</td></tr>
      <tr><th>Total Tagihan</th><td>Rp {{ '{:,.0f}'.format(invoice.total_amount) }}</td></tr>
      <tr><th>Jatuh Tempo</th><td>{{ invoice.due_date }}</td></tr>
      <tr><th>Link Pembayaran (WA admin)</th>
        <td>
          <a href="{{ wa_pay_url }}" class="btn" target="_blank">
            💬 Chat Admin untuk Bayar
          </a>
        </td>
      </tr>
    </table>
  </div>
{% else %}
  <p>Invoice bulan ini tidak ditemukan, hubungi admin.</p>
{% endif %}

<p style="margin-top:10px; font-size:12px; opacity:0.8;">
  Setelah pembayaran dikonfirmasi oleh admin,
  status invoice akan diubah menjadi <b>PAID</b> dan dashboard akan terbuka kembali.
</p>
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
<h1>🏠 Dashboard</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

<style>
  .dash-row {
    display:flex;
    flex-wrap:wrap;
    gap:12px;
    margin-top:8px;
  }
  .card {
    flex:1 1 260px;
    border:1px solid #0f0;
    padding:10px;
    box-sizing:border-box;
    background:#000;
  }
  .card h3 {
    margin:0 0 6px 0;
    font-size:14px;
  }
  .small-text {
    font-size:11px;
    opacity:0.8;
  }
  .metric-label {
    font-size:11px;
    opacity:0.8;
  }
  .metric-value {
    font-size:16px;
    font-weight:bold;
  }
  .meter {
    margin-top:3px;
    height:9px;
    border:1px solid #0f0;
    background:#001000;
    overflow:hidden;
  }
  .meter-fill {
    display:block;
    height:100%;
    width:0;
    background:#0f0;
    transition:width 0.4s ease-out;
  }
  .meter-fill.hot {
    background:#ff5555;
  }
  .badge-onoff {
    display:inline-block;
    padding:1px 6px;
    border-radius:8px;
    border:1px solid #0f0;
    font-size:10px;
    margin-left:4px;
  }
  .badge-off {
    opacity:0.6;
  }
  .kpi-row {
    display:flex;
    flex-wrap:wrap;
    gap:6px;
    margin-top:6px;
  }
  .kpi {
    flex:1 1 70px;
    border:1px solid #0f0;
    padding:4px;
    text-align:center;
  }
  .kpi-title {
    font-size:10px;
    opacity:0.8;
  }
  .kpi-number {
    font-size:16px;
    font-weight:bold;
  }
  .kpi-paid {
    border-color:#00ff00;
  }
  .kpi-unpaid {
    border-color:#ffb86c;
  }
  .kpi-isolated {
    border-color:#ff5555;
  }
  .kpi-disabled {
    border-color:#6272a4;
  }
</style>

{% if router_error %}
  <p style="color:#ff5555;">⚠️ Router error: {{ router_error }}</p>
{% endif %}

{% if db_error %}
  <p style="color:#ff5555;">⚠️ DB info: {{ db_error }}</p>
{% endif %}

<div class="dash-row">

    <!-- CARD 1: ROUTER STATUS -->
  <div class="card">
    <h3>📡 Router Status</h3>
    <div class="small-text">
      <div>IP: <b id="router-ip">{{ router_ip }}</b></div>
      <div>Identity: <b id="router-name">{{ router_name }}</b></div>
      <div>Uptime: <span id="router-uptime">{{ uptime }}</span></div>
      <div>Active PPP: <span id="router-active-ppp">{{ active_ppp_count if active_ppp_count is not none else 'N/A' }}</span></div>
    </div>

    <div style="margin-top:8px;">
      <div class="metric-label">CPU Load</div>
      <div class="metric-value" id="cpu-value">
        {{ cpu_load }}{% if cpu_load != 'N/A' %}%{% endif %}
      </div>
      <div class="meter">
        <span class="meter-fill {% if cpu_percent and cpu_percent >= 80 %}hot{% endif %}"
              id="cpu-meter-fill"
              style="width: {{ cpu_percent or 0 }}%;"></span>
      </div>
    </div>

    <div style="margin-top:8px;">
      <div class="metric-label">Memory</div>
      <div class="small-text" id="mem-text">{{ mem_display }}</div>
      <div class="meter" style="margin-top:3px;">
        <span class="meter-fill"
              id="mem-meter-fill"
              style="width: {{ mem_used_pct or 0 }}%;"></span>
      </div>
    </div>

    <p style="margin-top:8px;">
      <a href="{{ url_for('main.dashboard') }}" class="btn">🔄 Refresh</a>
      <span class="small-text" id="last-update" style="margin-left:6px; opacity:0.7;">
        live update...
      </span>
    </p>
  </div>


  <!-- CARD 2: RESELLER INFO -->
  <div class="card">
    <h3>🧩 Reseller Info</h3>
    <table>
      <tr><th style="padding-right:8px;">Nama</th><td>{{ reseller_name }}</td></tr>
      <tr><th>WA</th><td>{{ reseller_wa or '-' }}</td></tr>
      <tr><th>Email</th><td>{{ reseller_email or '-' }}</td></tr>
      <tr>
        <th>Notif WA</th>
        <td>
          {% if use_notifications %}
            ON<span class="badge-onoff">aktif</span>
          {% else %}
            OFF<span class="badge-onoff badge-off">nonaktif</span>
          {% endif %}
        </td>
      </tr>
      <tr>
        <th>Auto Payment</th>
        <td>
          {% if use_auto_payment %}
            ON<span class="badge-onoff">aktif</span>
          {% else %}
            OFF<span class="badge-onoff badge-off">nonaktif</span>
          {% endif %}
        </td>
      </tr>
      <tr>
        <th>Unpaid (bulan ini)</th>
        <td>{{ unpaid_count }} user<br>
          <span class="small-text">Total: Rp {{ '{:,.0f}'.format(unpaid_total) }}</span>
        </td>
      </tr>
    </table>

    <p style="margin-top:8px;">
      <a href="{{ url_for('reseller_settings.settings') }}" class="btn">⚙️ Settings</a>
    </p>
  </div>

  <!-- CARD 3: USER SUMMARY -->
  <div class="card">
    <h3>👤 User Summary</h3>

    <div class="kpi-row">
      <div class="kpi">
        <div class="kpi-title">Total</div>
        <div class="kpi-number">{{ total_users }}</div>
      </div>
      <div class="kpi kpi-paid">
        <div class="kpi-title">Paid</div>
        <div class="kpi-number">{{ paid_users }}</div>
      </div>
      <div class="kpi kpi-unpaid">
        <div class="kpi-title">Unpaid</div>
        <div class="kpi-number">{{ unpaid_users }}</div>
      </div>
      <div class="kpi kpi-isolated">
        <div class="kpi-title">Isolated</div>
        <div class="kpi-number">{{ isolated_users }}</div>
      </div>
      <div class="kpi kpi-disabled">
        <div class="kpi-title">Disabled</div>
        <div class="kpi-number">{{ disabled_users }}</div>
      </div>
    </div>

    <p class="small-text" style="margin-top:6px;">
      Ringkasan status user untuk periode berjalan.
    </p>
  </div>

</div>

<hr>

<div style="border:1px solid #0f0; padding:8px; margin-top:6px;">
  <h3>📡 PPP Profiles</h3>

  {% if profile_error %}
    <p style="color:#ff5555;">⚠️ {{ profile_error }}</p>
  {% endif %}
  {% if profile_success %}
    <p style="color:#00ff00;">✅ {{ profile_success }}</p>
  {% endif %}

  <form method="post" action="{{ url_for('main.sync_profiles_dashboard') }}" style="margin-bottom:8px;">
    <button type="submit"
            style="padding:4px 10px; background:#001a00; color:#0f0;
                   border:1px solid #0f0; border-radius:4px; cursor:pointer;">
      🔄 Sinkron Profil dari Router
    </button>
  </form>

  {% if profiles %}
    <table>
      <tr>
        <th>Nama Profil</th>
        <th>Rate Limit</th>
        <th>Isolation?</th>
        <th>Harga /bulan</th>
        <th>Total User</th>
        <th>Aksi</th>
      </tr>
      {% for p in profiles %}
      <tr>
        <form method="post" action="{{ url_for('main.update_profile_dashboard', profile_id=p.profile_id) }}">
          <td>{{ p.profile_name }}</td>
          <td>{{ p.rate_limit or '-' }}</td>
          <td>
            <input type="checkbox" name="is_isolation"
                   {% if p.is_isolation %}checked{% endif %}>
          </td>
          <td>
            <input type="text" name="monthly_price"
                   value="{{ p.monthly_price or 0 }}"
                   style="width:90px; padding:2px; background:#000; color:#0f0; border:1px solid #0f0; text-align:right;">
          </td>
          <td>{{ p.total_customers }}</td>
          <td style="white-space:nowrap;">
            <button type="submit" class="btn" style="font-size:11px; padding:2px 4px;color:#0f0;">💾 Simpan</button>
          </td>
        </form>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Tidak ada profil di database. Coba klik "Sinkron Profil dari Router".</p>
  {% endif %}
</div>

<pre style="font-size:12px; opacity:0.8; margin-top:8px;">
Catatan:
- Sinkron profil akan membaca /ppp/profile di router, lalu menambah/update ke tabel ppp_profiles.
- Edit di kolom "Isolation?" dan "Harga/bulan" hanya mengubah data billing di database,
  tidak mengubah setting router secara langsung.
</pre>
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

      // Update teks dasar
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

      // CPU value + bar
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

        // toggle warna merah kalau panas
        if (pct >= 80) {
          cpuBarEl.classList.add("hot");
        } else {
          cpuBarEl.classList.remove("hot");
        }
      }

      // Memory text + bar
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

      // Info waktu update
      const lastUpdateEl = document.getElementById("last-update");
      if (lastUpdateEl) {
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        const ss = String(now.getSeconds()).padStart(2, "0");
        lastUpdateEl.textContent = "updated " + hh + ":" + mm + ":" + ss;
      }

    } catch (err) {
      // boleh diabaikan atau console.log(err)
      // console.log(err);
    }
  }

  // pertama kali saat halaman load
  window.addEventListener("load", function() {
    fetchStats();
    // interval tiap 3 detik, silakan diubah
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
