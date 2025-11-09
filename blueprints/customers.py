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
<h1>👤 PPP Customers</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

{% if router_error %}
  <p style="color:#ff5555;">⚠️ Router: {{ router_error }}</p>
{% endif %}
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
  <form method="post" action="{{ url_for('customers.sync_customers') }}" style="display:inline-block; margin-right:8px;">
    <button type="submit"
            style="padding:4px 10px; background:#001a00; color:#0f0;
                   border:1px solid #0f0; border-radius:4px; cursor:pointer;">
      🔄 Sinkron Customers
    </button>
  </form>

  <a href="{{ url_for('customers.create_customer') }}" class="btn" style="margin-right:4px;">➕ Tambah Customer</a> 
</div>

<div style="border:1px solid #0f0; padding:6px; margin-bottom:8px;">
  <form method="get" action="{{ url_for('customers.list_customers') }}" style="font-size:12px;">
    <span>Status:</span>
    <select name="status"
            style="background:#000; color:#0f0; border:1px solid #0f0; margin-right:6px;">
      <option value="all" {% if status_filter=='all' %}selected{% endif %}>All</option>
      <option value="paid" {% if status_filter=='paid' %}selected{% endif %}>Paid</option>
      <option value="unpaid" {% if status_filter=='unpaid' %}selected{% endif %}>Unpaid</option>
      <option value="isolated" {% if status_filter=='isolated' %}selected{% endif %}>Isolated</option>
      <option value="disabled" {% if status_filter=='disabled' %}selected{% endif %}>Disabled</option>
    </select>

    <span>Petugas:</span>
    <input type="text" name="petugas" value="{{ petugas_q or '' }}"
           placeholder="nama petugas"
           style="background:#000; color:#0f0; border:1px solid #0f0; padding:2px; width:140px; margin-right:6px;">

    <span>Cari:</span>
    <input type="text" name="q" value="{{ q or '' }}"
           placeholder="username / nama / WA"
           style="background:#000; color:#0f0; border:1px solid #0f0; padding:2px; width:180px; margin-right:6px;">

    <span>Per halaman:</span>
    <input type="text" name="per_page" value="{{ per_page }}"
           style="background:#000; color:#0f0; border:1px solid #0f0; padding:2px; width:40px; margin-right:6px;">

    <button type="submit"
            style="padding:2px 8px; background:#001a00; color:#0f0;
                   border:1px solid #0f0; border-radius:4px; cursor:pointer;">
      🔍 Tampilkan
    </button>
  </form>
</div>

<div style="font-size:12px; margin-bottom:4px;">
  Total pelanggan (sesuai filter): <b>{{ total_rows }}</b> |
  Ditampilkan halaman ini: <b>{{ customers|length }}</b> |
  Online: <b>{{ online_count }}</b> |
  Offline: <b>{{ offline_count }}</b><br>
  Lunas: <b>{{ paid_count }}</b>
  (Rp {{ '{:,.0f}'.format(paid_total or 0) }}) |
  Unpaid: <b>{{ unpaid_count }}</b>
  (Rp {{ '{:,.0f}'.format(unpaid_total or 0) }})<br>
  Halaman {{ page }} dari {{ total_pages }}.
</div>


<div style="margin-bottom:6px;">
  {% if page > 1 %}
    <a href="{{ url_for('customers.list_customers', status=status_filter, q=q, petugas=petugas_q, per_page=per_page, page=page-1) }}" class="btn">⬅️ Prev</a>
  {% endif %}
  {% if page < total_pages %}
    <a href="{{ url_for('customers.list_customers', status=status_filter, q=q, petugas=petugas_q, per_page=per_page, page=page+1) }}" class="btn">Next ➡️</a>
  {% endif %}
</div>

<div style="border:1px solid #0f0; padding:8px; margin-top:4px; max-height:1310px; overflow:auto;">
  <h3>Daftar Pelanggan PPP</h3>

  {% if customers %}
    <table>
      <tr>
        <th>Aksi</th>
        <th>Nama</th>
        <th>Username</th>
        <th>Alamat</th>
        <th>Harga</th>
        <th>Status</th>
        <th>Online</th>
        <th>Petugas</th>
      </tr>
      {% for c in customers %}
      <tr>
        <!-- Aksi -->
        <td style="white-space:nowrap;">

          <!-- Edit -->
          <a href="{{ url_for('customers.edit_customer', customer_id=c.customer_id) }}"
             class="btn btn-info" style="font-size:11px; padding:2px 4px;">
            ✏️
          </a>

          <!-- Terminate -->
          <form method="post"
                action="{{ url_for('customers.terminate_customer', customer_id=c.customer_id) }}"
                style="display:inline;"
                onsubmit="return confirm('Terminate session PPP {{ c.ppp_username }} sekarang?');">
            <button type="submit" class="btn btn-danger" style="font-size:11px; padding:2px 4px;color:#0f0;">
              ⏹ Kill
            </button>
          </form>

          <!-- Toggle Enabled -->
          <form method="post"
                action="{{ url_for('customers.toggle_enable_customer', customer_id=c.customer_id) }}"
                style="display:inline;"
                onsubmit="return confirm('Ubah status enable/disable user {{ c.ppp_username }}?');">
            <button type="submit" class="btn" style="font-size:11px; padding:2px 4px;color:#0f0;">
              {% if c.is_enabled %}🚫 Disable{% else %}✅ Enable{% endif %}
            </button>
          </form>

          <!-- Suspend / Unsuspend -->
          {% if not c.is_isolated %}
            <form method="post"
                  action="{{ url_for('customers.isolate_customer', customer_id=c.customer_id) }}"
                  style="display:inline;"
                  onsubmit="return confirm('Suspend (isolate) user {{ c.ppp_username }} ke profil isolasi?');">
              <button type="submit" class="btn" style="font-size:11px; padding:2px 4px;color:#0f0;">
                🧊 Suspend
              </button>
            </form>
          {% else %}
            <form method="post"
                  action="{{ url_for('customers.unisolate_customer', customer_id=c.customer_id) }}"
                  style="display:inline;"
                  onsubmit="return confirm('Unsuspend (kembalikan) user {{ c.ppp_username }} ke profil normal?');">
              <button type="submit" class="btn btn-danger" style="font-size:11px; padding:2px 4px;">
                ⬅️ Unsuspend
              </button>
            </form>
          {% endif %}

          <!-- Bayar / Batalkan bayar 1 bulan -->
          {% if c.has_paid_current_period %}
            <form method="post"
                  action="{{ url_for('customers.cancel_pay_customer', customer_id=c.customer_id) }}"
                  style="display:inline;"
                  onsubmit="return confirm('Batalkan 1 bulan pembayaran terakhir untuk {{ c.ppp_username }}?');">
              <input type="hidden" name="months" value="1">
              <button type="submit" class="btn btn-danger" style="font-size:11px; padding:2px 4px;color:#0f0;">
                ↩️ Unpaid
              </button>
            </form>
          {% else %}
            <form method="post"
                  action="{{ url_for('customers.pay_customer', customer_id=c.customer_id) }}"
                  style="display:inline;"
                  onsubmit="return confirm('Catat pembayaran 1 bulan untuk {{ c.ppp_username }}?');">
              <input type="hidden" name="months" value="1">
              <button type="submit" class="btn btn-warning" style="font-size:11px; padding:2px 4px;color:#0f0;">
                💰 Paid
              </button>
            </form>
          {% endif %}

          <!-- Delete -->
          <form method="post"
                action="{{ url_for('customers.delete_customer', customer_id=c.customer_id) }}"
                style="display:inline;"
                onsubmit="return confirm('Yakin hapus user {{ c.ppp_username }} dari DB dan Mikrotik?');">
            <button type="submit" class="btn btn-danger" style="font-size:11px; padding:2px 4px;">
              🗑 Del
            </button>
          </form>
        </td>
        <!-- Nama -->
        <td style="text-transform: uppercase;">{{ c.full_name or '-' }}</td>

        <!-- Username -->
        <td>{{ c.ppp_username }}</td>

        <!-- Alamat -->
        <td style="text-transform: uppercase;">{{ c.address or '-' }}</td>

        <!-- Harga -->
        <td>{{ '{:,.0f}'.format(c.monthly_price or 0) }}</td>

        <!-- Status singkat -->
        <td>
          {% if c.payment_status_text == 'paid_current_period' %}
            Lunas
          {% elif c.payment_status_text == 'unpaid_current_period' %}
            Unpaid
          {% elif c.payment_status_text == 'isolated' %}
            Iso
          {% elif c.payment_status_text == 'never_paid' %}
            Baru
          {% else %}
            {{ c.payment_status_text }}
          {% endif %}
        </td>

        <!-- Online -->
        <td>
          {% if c.is_online %}
            🟢 ON
          {% else %}
            🔴OFF
          {% endif %}
        </td>

        <!-- Petugas -->
        <td>{{ c.petugas_name or '-' }}</td>

      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Tidak ada customer di database. Coba klik "Sinkron Customers".</p>
  {% endif %}
</div>

<pre style="font-size:12px; opacity:0.8; margin-top:8px;">
Catatan singkat:
- Status: Lunas / Unpaid / Iso / Baru.
- Suspend/Unsuspend muncul isolir pada klien.
- Online diambil dari /ppp/active.
</pre>
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
<h1>➕ Tambah Customer PPP</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}
{% if success %}
  <p style="color:#00ff00;">✅ {{ success }}</p>
{% endif %}

<form method="post" style="max-width:520px; margin-top:10px;">
  <fieldset style="border:1px solid #0f0; padding:8px; margin-bottom:8px;">
    <legend style="font-size:12px;">PPP Secret</legend>

    <label>
      PPP Username<br>
      <input type="text" name="ppp_username"
             value="{{ ppp_username }}"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      PPP Password<br>
      <input type="password" name="ppp_password"
             value="{{ ppp_password }}"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Profile<br>
      <select name="profile_id"
              style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
        <option value="">-- pilih profile --</option>
        {% for p in profiles %}
          <option value="{{ p.id }}" {% if profile_id_raw|int == p.id %}selected{% endif %}>
            {{ p.name }}
          </option>
        {% endfor %}
      </select>
    </label>
    <br><br>

    <label>
      Status User<br>
      <input type="radio" name="is_enabled" value="1" {% if is_enabled_raw=='1' %}checked{% endif %}> Enable
      &nbsp;&nbsp;
      <input type="radio" name="is_enabled" value="0" {% if is_enabled_raw=='0' %}checked{% endif %}> Disable
    </label>
  </fieldset>

  <fieldset style="border:1px solid #0f0; padding:8px; margin-bottom:8px;">
    <legend style="font-size:12px;">Data Pelanggan</legend>

    <label>
      Nama Lengkap<br>
      <input type="text" name="full_name"
             value="{{ full_name }}"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Alamat<br>
      <textarea name="address"
                style="width:100%; height:60px; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">{{ address }}</textarea>
    </label>
    <br><br>

    <label>
      No. WhatsApp<br>
      <input type="text" name="wa_number"
             value="{{ wa_number }}"
             style="width:60%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Nama Petugas<br>
      <input type="text" name="petugas_name"
             value="{{ petugas_name }}"
             style="width:60%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
  Billing Start Date<br>
  <input type="date" name="billing_start_date"
         value="{{ today.strftime('%Y-%m-%d') }}"
         style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
</label>

  </fieldset>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    💾 Simpan Customer
  </button>

  <a href="{{ url_for('customers.list_customers') }}" class="btn" style="margin-left:8px;">⬅️ Kembali</a>
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
            SET profile_id = %(pid)s,
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
            SET profile_id = %(pid)s,
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
<h1>✏️ Edit Customer</h1>
<p>Reseller: <b>{{ reseller_name }}</b></p>
<p>User PPP: <b>{{ cust.ppp_username }}</b></p>

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}
{% if success %}
  <p style="color:#00ff00;">✅ {{ success }}</p>
{% endif %}

<form method="post" style="max-width:520px; margin-top:10px;">
  <fieldset style="border:1px solid #0f0; padding:8px; margin-bottom:8px;">
    <legend style="font-size:12px;">PPP Secret</legend>

        <label>
      PPP Username<br>
      <input type="text"
             value="{{ cust.ppp_username or '' }}"
             readonly
             style="width:100%; padding:4px; background:#222; color:#0f0; border:1px solid #0f0;">
    </label>

    <br><br>

    <label>
      PPP Password (kosongkan jika tidak diubah)<br>
      <input type="password" name="ppp_password"
             value=""
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Profile<br>
      <select name="profile_id"
              style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
        <option value="">-- tanpa profile (sesuai router) --</option>
        {% for p in profiles %}
          <option value="{{ p.id }}" {% if cust.profile_id == p.id %}selected{% endif %}>
            {{ p.name }}
          </option>
        {% endfor %}
      </select>
    </label>
    <br><br>

    <label>
      Status User<br>
      <input type="radio" name="is_enabled" value="1" {% if cust.is_enabled %}checked{% endif %}> Enable
      &nbsp;&nbsp;
      <input type="radio" name="is_enabled" value="0" {% if not cust.is_enabled %}checked{% endif %}> Disable
    </label>
  </fieldset>

  <fieldset style="border:1px solid #0f0; padding:8px; margin-bottom:8px;">
    <legend style="font-size:12px;">Data Pelanggan</legend>

    <label>
      Nama Lengkap<br>
      <input type="text" name="full_name"
             value="{{ cust.full_name or '' }}"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Alamat<br>
      <textarea name="address"
                style="width:100%; height:60px; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">{{ cust.address or '' }}</textarea>
    </label>
    <br><br>

    <label>
      No. WhatsApp<br>
      <input type="text" name="wa_number"
             value="{{ cust.wa_number or '' }}"
             style="width:60%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      Nama Petugas<br>
      <input type="text" name="petugas_name"
             value="{{ cust.petugas_name or '' }}"
             style="width:60%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
  Billing Start Date<br>
  <input type="date" name="billing_start_date"
         value="{% if cust.billing_start_date %}{{ cust.billing_start_date.strftime('%Y-%m-%d') }}{% endif %}"
         style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
</label>

    <br><br>

    <p style="font-size:12px; opacity:0.8;">
      last_paid_period saat ini: <b>{{ cust.last_paid_period or '-' }}</b><br>
      (Perubahan pembayaran dilakukan lewat tombol 💰 di halaman daftar customers.)
    </p>
  </fieldset>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    💾 Simpan Perubahan
  </button>

  <a href="{{ url_for('customers.list_customers') }}" class="btn" style="margin-left:8px;">⬅️ Kembali</a>
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
