# blueprints/petugas.py

from __future__ import annotations

import datetime
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

from flask import (
    Blueprint,
    request,
    session,
    redirect,
    url_for,
    render_template_string,
)

import db
from cron_jobs.notify_unpaid_users import format_rupiah
from mikrotik_client import get_ppp_active, MikrotikError 
bp = Blueprint("petugas", __name__)


# ======================================================================
# Helper umum
# ======================================================================

def _clear_petugas_session() -> None:
    """
    Bersihkan semua info terkait sesi petugas (tanpa mengganggu session reseller utama).
    """
    for k in (
        "petugas_slug",
        "petugas_name",
        "petugas_reseller_id",
    ):
        session.pop(k, None)


def _redirect_back_with_message(
    success: str | None = None,
    error: str | None = None,
    default_endpoint: str = "petugas.list_petugas_customers",
    default_kwargs: dict | None = None,
):
    """
    Redirect ke referrer kalau ada, kalau tidak ke endpoint default + ?success= / ?error=
    """
    prev_url = request.referrer

    if not prev_url:
        if default_kwargs is None:
            default_kwargs = {}
        base = url_for(default_endpoint, **default_kwargs)
        msg = {"success": success} if success else {"error": error}
        query = urlencode(msg)
        connector = "?" if "?" not in base else "&"
        return redirect(f"{base}{connector}{query}")

    msg = {"success": success} if success else {"error": error}
    query = urlencode(msg)
    connector = "&" if "?" in prev_url else "?"
    return redirect(f"{prev_url}{connector}{query}")


def _render_simple_page(
    title: str,
    body_html: str,
    context: Dict[str, Any] | None = None,
):
    """
    Layout HTML sederhana (tanpa navbar utama app.py).
    body_html dirender sebagai template jinja dan disisipkan ke base_template.
    """
    if context is None:
        context = {}

    body_rendered = render_template_string(body_html, **context)

    base_template = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            brand: {
              500: '#22c55e',
              600: '#16a34a',
            },
          },
        },
      },
    };
  </script>
</head>
<body class="min-h-screen bg-slate-950 text-slate-100 antialiased">
  <div class="flex min-h-screen flex-col">
    <main class="flex-1 overflow-y-auto">
      <div class="mx-auto max-w-6xl px-4 py-4 lg:px-6 lg:py-6">
        {{ body|safe }}
      </div>
    </main>
    <footer class="border-t border-slate-800 bg-slate-900/80 text-[11px] text-slate-500">
      <div class="mx-auto max-w-6xl px-4 py-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>PPP Petugas Panel</div>
        <div>Mode terbatas (petugas)</div>
      </div>
    </footer>
  </div>
  <!-- KONFIRMASI AKSI -->
<dialog id="confirmDialog" class="rounded-md border border-slate-700 bg-slate-900/95 p-4 text-slate-100 shadow-xl backdrop:bg-slate-950/80 w-72">
  <form method="dialog" class="space-y-3 text-center">
    <div id="confirmMessage" class="text-sm"></div>
    <div class="flex justify-center gap-2 text-[13px]">
      <button value="cancel" class="rounded-md border border-slate-700 bg-slate-800 px-3 py-1 hover:bg-slate-700">Batal</button>
      <button value="ok" class="rounded-md border border-emerald-500/70 bg-emerald-500/20 px-3 py-1 text-emerald-300 hover:bg-emerald-500/30">Ya</button>
    </div>
  </form>
</dialog>

<script>
document.addEventListener('DOMContentLoaded', function() {
  const dialog = document.getElementById('confirmDialog');
  const msgBox = document.getElementById('confirmMessage');

  // fungsi konfirmasi umum
  async function confirmAction(message) {
    msgBox.textContent = message;
    dialog.showModal();
    return new Promise(resolve => {
      dialog.addEventListener('close', () => {
        resolve(dialog.returnValue === 'ok');
      }, { once: true });
    });
  }

  // intercept semua form dengan data-confirm
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const ok = await confirmAction(form.dataset.confirm);
      if (ok) form.submit();
    });
  });

  // intercept semua link dengan data-confirm (contoh: tombol print)
  document.querySelectorAll('a[data-confirm]').forEach(link => {
    link.addEventListener('click', async (e) => {
      e.preventDefault();
      const ok = await confirmAction(link.dataset.confirm);
      if (ok) window.location.href = link.href;
    });
  });
});
</script>

</body>
</html>
"""
    return render_template_string(
        base_template,
        title=title,
        body=body_rendered,
        **context,
    )


# ======================================================================
# Helper login petugas
# ======================================================================

def _require_petugas_login(
    petugas_slug: str,
) -> Tuple[dict | None, str | None, str | None, Any]:
    """
    Pastikan petugas sudah login untuk slug tertentu.
    Return (reseller_row, petugas_name, router_ip, redirect_response).
    router_ip diambil dari session.get("router_ip") (kalau ada), TIDAK dari input form.
    """
    saved_slug = session.get("petugas_slug")
    petugas_reseller_id = session.get("petugas_reseller_id")
    petugas_name = session.get("petugas_name")

    if not saved_slug or not petugas_reseller_id or saved_slug != petugas_slug:
        return None, None, None, redirect(
            url_for("petugas.login_petugas", petugas_slug=petugas_slug)
        )

    # Ambil reseller dari DB
    reseller = db.query_one(
        """
        SELECT id,
               display_name,
               router_username,
               router_password,
               wa_number,
               email,
               use_notifications,
               use_auto_payment,
               is_active
        FROM resellers
        WHERE id = %(rid)s
        """,
        {"rid": petugas_reseller_id},
    )

    if reseller is None or not reseller["is_active"]:
        _clear_petugas_session()
        return None, None, None, redirect(
            url_for("petugas.login_petugas", petugas_slug=petugas_slug)
        )

    # router_ip diambil dari session (di-set oleh sisi reseller)
    router_ip = session.get("router_ip") or "-"

    return reseller, petugas_name, router_ip, None


# ======================================================================
# ROUTE: Login / Logout Petugas
# ======================================================================
@bp.route("/petugas/<petugas_slug>/login", methods=["GET", "POST"])
def login_petugas(petugas_slug: str):
    """
    Login petugas:
    - Validasi username/password reseller.
    - Ambil router_ip via Router Admin seperti login reseller.
    - Simpan session (petugas_slug, reseller_id, router_ip, dll).
    """
    error = None
    router_ip = None

    if request.method == "POST":
        router_username = (request.form.get("router_username") or "").strip()
        router_password = (request.form.get("router_password") or "").strip()

        if not router_username or not router_password:
            error = "Router username & password wajib diisi."
        else:
            # Cek data reseller
            reseller = db.query_one(
                """
                SELECT id, router_username, router_password, display_name, is_active
                FROM resellers
                WHERE router_username = %(u)s
                """,
                {"u": router_username},
            )

            if reseller is None:
                error = "Reseller tidak ditemukan."
            elif reseller["router_password"] != router_password:
                error = "Password salah."
            elif not reseller["is_active"]:
                error = "Akun reseller non-aktif."
            else:
                try:
                    # Ambil router IP seperti di auth_reseller.py
                    from blueprints.auth_reseller import _get_router_ip_for_reseller
                    router_ip = _get_router_ip_for_reseller(router_username)
                    print(f"[petugas.login_petugas] Router IP dari Router Admin = {router_ip}")

                    if not router_ip:
                        error = "Pastikan router reseller sudah terkoneksi ke Router Utama via L2TP."
                    else:
                        session.clear()
                        session["petugas_slug"] = petugas_slug
                        session["petugas_name"] = petugas_slug
                        session["petugas_reseller_id"] = reseller["id"]
                        session["router_username"] = router_username
                        session["router_ip"] = router_ip
                        print(f"[petugas.login_petugas] ✅ Login petugas sukses, router_ip={router_ip}")

                        return redirect(
                            url_for("petugas.list_petugas_customers", petugas_slug=petugas_slug)
                        )
                except Exception as e:
                    print(f"[petugas.login_petugas] ❌ Gagal ambil router_ip: {e}")
                    error = "Gagal mendapatkan Router IP. Periksa koneksi router reseller."

    # tampilkan form login
    body_html = """
<section class="mx-auto max-w-md">
  <h1 class="mb-4 flex items-center gap-2 text-xl font-semibold tracking-tight">
    <span>🔐</span>
    <span>Login Petugas</span>
  </h1>
  <p class="mb-4 text-sm text-slate-400">
    Petugas: <span class="font-mono text-emerald-300">/{{ petugas_slug }}</span><br>
    Masuk menggunakan <b>router_username</b> dan <b>router_password</b> milik reseller.
  </p>

  {% if error %}
    <div class="mb-4 rounded border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
      ⚠️ {{ error }}
    </div>
  {% endif %}

  <form method="post" class="space-y-4 text-sm">
    <div>
      <label class="block text-xs font-medium text-slate-200">Router Username</label>
      <input type="text" name="router_username"
        class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        autocomplete="off" required>
    </div>
    <div>
      <label class="block text-xs font-medium text-slate-200">Router Password</label>
      <input type="password" name="router_password"
        class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        autocomplete="off" required>
    </div>
    <button type="submit"
      class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-4 py-2 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
      🔑 <span>Login</span>
    </button>
  </form>
</section>
    """

    return _render_simple_page(
        title=f"Login Petugas /{petugas_slug}",
        body_html=body_html,
        context={"error": error, "petugas_slug": petugas_slug},
    )


@bp.route("/petugas/<petugas_slug>/logout")
def logout_petugas(petugas_slug: str):
    _clear_petugas_session()
    return redirect(url_for("petugas.login_petugas", petugas_slug=petugas_slug))


# ======================================================================
# LIST CUSTOMER UNTUK PETUGAS
# ======================================================================

@bp.route("/petugas/<petugas_slug>", methods=["GET"])
def list_petugas_customers(petugas_slug: str):
    """
    Tampilkan daftar customer yang dimiliki petugas tertentu.
    Filter:
      - reseller_id = petugas_reseller_id
      - LOWER(petugas_name) = petugas_slug.lower()
    """
    reseller, petugas_name, router_ip, redirect_resp = _require_petugas_login(
        petugas_slug
    )
    if redirect_resp is not None:
        return redirect_resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None
    status_filter = (request.args.get("status") or "all").strip()
    q = (request.args.get("q") or "").strip()

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

    where_clauses = [
        "reseller_id = %(rid)s",
        "LOWER(petugas_name) = %(ptg)s",
    ]
    params: Dict[str, Any] = {
        "rid": reseller["id"],
        "ptg": petugas_slug.lower(),
    }

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

    if q:
        where_clauses.append(
            """
            (
              ppp_username ILIKE %(q)s OR
              full_name    ILIKE %(q)s OR
              address      ILIKE %(q)s
            )
            """
        )
        params["q"] = f"%{q}%"

    where_sql = " AND ".join(where_clauses)

    customers: list[dict] = []
    db_error = None
    router_error = None
    online_names: set[str] = set()
    total_rows = 0
    total_pages = 1

    paid_count = 0
    paid_total = 0
    unpaid_count = 0
    unpaid_total = 0

    # 1) ringkasan
    try:
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

    # 2) list customers
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

    # 3) PPP active
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
        router_error = "Router IP tidak tersedia di session. Aksi router dilewati."

    for c in customers:
        c["is_online"] = c["ppp_username"] in online_names

    online_count = sum(1 for c in customers if c.get("is_online"))
    offline_count = len(customers) - online_count
    body_html = """
<!-- HEADER -->
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4 md:flex-row md:items-center md:justify-between">
  <div class="space-y-1">
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Petugas</span>
      <span>›</span>
      <span class="text-slate-300 uppercase">{{ petugas_slug }}</span>
    </div>
    <h1 class="flex items-center gap-2 text-lg font-semibold tracking-tight md:text-xl">
      <span>👤</span>
      <span>Daftar Pelanggan</span>
    </h1>
    <p class="text-[12px] leading-snug text-slate-400 md:text-sm">
      Reseller: <span class="font-medium text-slate-200">{{ reseller_name }}</span><br>
      Petugas: <span class="font-medium text-emerald-300 uppercase">{{ petugas_slug }}</span>
    </p>
  </div>

  <!-- FILTER & LOGOUT -->
  <div class="flex flex-wrap items-center justify-start gap-2 text-xs md:justify-end">
    <form method="get" class="flex flex-wrap items-center gap-2">
      <input type="hidden" name="status" value="{{ status_filter }}">
      <input type="text"
             name="q"
             value="{{ q }}"
             placeholder="Cari pelanggan..."
             class="w-40 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] text-slate-100 focus:border-emerald-500 focus:outline-none md:w-52 md:text-xs">
      <button type="submit"
              class="rounded-md border border-slate-700 bg-slate-900 px-3 py-1 text-[11px] font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
        🔍 Cari
      </button>
    </form>

    <a href="{{ url_for('petugas.logout_petugas', petugas_slug=petugas_slug) }}"
       class="inline-flex items-center gap-1 rounded-md border border-rose-500/70 bg-rose-500/10 px-3 py-1 text-[11px] font-medium text-rose-200 hover:bg-rose-500/20">
      🚪 Logout
    </a>
  </div>
</section>

<!-- ALERTS -->
{% for name, msg, color in [
  ('router_error', router_error, 'amber'),
  ('error', error, 'rose'),
  ('db_error', db_error, 'amber'),
  ('success', success, 'emerald')
] if msg %}
  <div class="mt-3 rounded-md border border-{{ color }}-500/70 bg-{{ color }}-500/10 px-3 py-2 text-[11px] text-{{ color }}-100">
    ⚠️ {{ msg }}
  </div>
{% endfor %}

<!-- RINGKASAN -->
<section class="mt-4 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4 md:gap-3">
  {% for label, value, color, extra in [
    ('Total Customer', total_rows, 'slate', None),
    ('Online', online_count, 'emerald', None),
    ('Paid Bulan Ini', paid_count, 'emerald', format_rupiah(paid_total)),
    ('Belum Bayar', unpaid_count, 'rose', format_rupiah(unpaid_total))
  ] %}
    <div class="rounded-lg border border-slate-800 bg-slate-900/70 p-2 sm:p-3">
      <div class="text-slate-400">{{ label }}</div>
      <div class="mt-1 text-base font-semibold text-{{ color }}-300 sm:text-lg">{{ value }}</div>
      {% if extra %}
        <div class="text-[10px] text-slate-500">{{ extra }}</div>
      {% endif %}
    </div>
  {% endfor %}
</section>

<!-- FILTER STATUS -->
<div class="mt-4 flex flex-wrap items-center gap-1 text-[10px] sm:text-[11px]">
  {% for label, val, color in [
    ('Semua', 'all', 'emerald'),
    ('Paid', 'paid', 'emerald'),
    ('Unpaid', 'unpaid', 'amber'),
    ('Isolasi', 'isolated', 'sky'),
    ('Disabled', 'disabled', 'rose')
  ] %}
    <a href="{{ url_for('petugas.list_petugas_customers', petugas_slug=petugas_slug, status=val, q=q) }}"
       class="rounded-full border px-2 py-0.5 sm:px-3 sm:py-1 {% if status_filter==val %}border-{{ color }}-500/80 bg-{{ color }}-500/10 text-{{ color }}-200{% else %}border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-500 hover:bg-slate-800{% endif %}">
      {{ label }}
    </a>
  {% endfor %}
</div>

<!-- TABEL CUSTOMER -->
<div class="mt-4 overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/50">
  <table class="min-w-full border-collapse text-[11px] sm:text-xs">
    <thead class="bg-slate-900/80 sticky top-0 z-10">
      <tr class="border-b border-slate-800 uppercase tracking-wide text-slate-400">
        <th class="px-2 py-2 text-left">Aksi</th>
        <th class="px-2 py-2 text-left">Status</th>
        <th class="px-2 py-2 text-left">Nama</th>
        <th class="px-2 py-2 text-left">User</th>
        <th class="px-2 py-2 text-left">Alamat</th>
        <th class="px-2 py-2 text-right">Harga</th>
        <th class="px-2 py-2 text-left">WA</th>
      </tr>
    </thead>
    <tbody>
      {% if not customers %}
        <tr>
          <td colspan="7" class="px-3 py-4 text-center text-slate-500 text-[12px]">
            Belum ada customer untuk petugas ini.
          </td>
        </tr>
      {% else %}
        {% for c in customers %}
          <tr class="border-b border-slate-800/80 hover:bg-slate-900/80">
            <td class="px-2 py-1 align-top">
              <div class="flex flex-col gap-1">
                {% if c.has_paid_current_period %}
                <form method="post"
                        action="{{ url_for('petugas.cancel_pay_customer', petugas_slug=petugas_slug, customer_id=c.customer_id) }}"
                        data-confirm="Batalkan pembayaran terakhir untuk {{ c.ppp_username }}?">
                    <button type="submit"
                            class="inline-flex items-center gap-1 rounded border border-rose-500/70 bg-rose-500/10 px-2 py-0.5 text-[10px] text-rose-100 hover:bg-rose-500/20">
                    ↩️ Unpaid
                    </button>
                    <a href="{{ url_for('petugas.petugas_print_customer', petugas_slug=petugas_slug, cid=c.customer_id) }}"
                    data-confirm="Cetak struk untuk {{ c.ppp_username }}?"
                    class="inline-flex items-center gap-1 rounded border border-slate-600 bg-slate-800/40 px-2 py-0.5 text-[10px] text-slate-100 hover:bg-slate-700/60">
                    🖨️ Print
                    </a>
                </form>
                {% else %}
                <form method="post"
                        action="{{ url_for('petugas.pay_customer', petugas_slug=petugas_slug, customer_id=c.customer_id) }}"
                        data-confirm="Catat pembayaran 1 bulan untuk {{ c.ppp_username }}?">
                    <input type="hidden" name="months" value="1">
                    <button type="submit"
                            class="inline-flex items-center gap-1 rounded border border-emerald-500/70 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-100 hover:bg-emerald-500/20">
                    💰 Paid
                    </button>
                </form>
                {% endif %}
                <a href="{{ url_for('petugas.edit_petugas_customer', petugas_slug=petugas_slug, cid=c.customer_id) }}"
                class="inline-flex items-center gap-1 rounded border border-sky-500/70 bg-sky-500/10 px-2 py-0.5 text-[10px] text-sky-200 hover:bg-sky-500/20"
                data-confirm="Edit data pelanggan {{ c.ppp_username }}?">
                ✏️ Edit
                </a>

              </div>
            </td>

            <td class="px-2 py-1 align-top">
              <div class="flex flex-col gap-0.5 text-[10px]">
                <div>
                  {% if c.is_online %}
                    <span class="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-emerald-200">🟢</span>
                  {% else %}
                    <span class="inline-flex items-center gap-1 rounded-full bg-slate-700/40 px-2 py-0.5 text-slate-300">⚫</span>
                  {% endif %}
                </div>
                <div>
                  {% if c.payment_status_text == 'paid_current_period' %}
                    <span class="text-emerald-300">Lunas</span>
                  {% elif c.payment_status_text in ['unpaid_current_period','never_paid'] %}
                    <span class="text-amber-300">Belum</span>
                  {% else %}
                    <span class="text-slate-300">{{ c.payment_status_text }}</span>
                  {% endif %}
                </div>
                {% if not c.is_enabled %}
                  <span class="text-rose-300">Disabled</span>
                {% elif c.is_isolated %}
                  <span class="text-amber-300">Isolasi</span>
                {% endif %}
              </div>
            </td>

            <td class="px-2 py-1 align-top uppercase text-slate-100 truncate max-w-[120px]">{{ c.full_name or '-' }}</td>
            <td class="px-2 py-1 align-top font-mono text-slate-100">{{ c.ppp_username }}</td>
            <td class="px-2 py-1 align-top uppercase text-slate-300 truncate max-w-[160px]">{{ c.address or '-' }}</td>
            <td class="px-2 py-1 align-top text-right text-slate-100">{{ format_rupiah(c.monthly_price or 0) }}</td>
            <td class="px-2 py-1 align-top text-slate-200 truncate max-w-[120px]">{{ c.wa_number or '-' }}</td>
          </tr>
        {% endfor %}
      {% endif %}
    </tbody>
  </table>
</div>

<!-- PAGINASI -->
{% if total_pages > 1 %}
  <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-[10px] sm:text-[11px] text-slate-300">
    <div>Halaman {{ page }} / {{ total_pages }}</div>
    <div class="flex items-center gap-1">
      {% if page > 1 %}
        <a href="{{ url_for('petugas.list_petugas_customers', petugas_slug=petugas_slug, page=page-1, per_page=per_page, status=status_filter, q=q) }}"
           class="rounded border border-slate-700 px-2 py-0.5 hover:bg-slate-800">◀ Prev</a>
      {% endif %}
      {% if page < total_pages %}
        <a href="{{ url_for('petugas.list_petugas_customers', petugas_slug=petugas_slug, page=page+1, per_page=per_page, status=status_filter, q=q) }}"
           class="rounded border border-slate-700 px-2 py-0.5 hover:bg-slate-800">Next ▶</a>
      {% endif %}
    </div>
  </div>
{% endif %}

"""


    return _render_simple_page(
        title=f"Petugas {petugas_slug}",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "petugas_slug": petugas_slug,
            "customers": customers,
            "router_error": router_error,
            "error": error,
            "success": success,
            "db_error": db_error,
            "status_filter": status_filter,
            "q": q,
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
            "format_rupiah": format_rupiah,
        },
    )


# ======================================================================
# AKSI: Bayar & Cancel-Pay (khusus petugas)
# ======================================================================

@bp.route("/petugas/<petugas_slug>/customers/<int:customer_id>/pay", methods=["POST"])
def pay_customer(petugas_slug: str, customer_id: int):
    """
    Bayar 1 customer (versi petugas).
    """
    reseller, petugas_name, router_ip, redirect_resp = _require_petugas_login(
        petugas_slug
    )
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
        SELECT
          id,
          ppp_username,
          full_name,
          wa_number,
          is_isolated,
          profile_id
        FROM ppp_customers
        WHERE id = %(cid)s
          AND reseller_id = %(rid)s
          AND LOWER(petugas_name) = %(ptg)s
        """,
        {"cid": customer_id, "rid": reseller["id"], "ptg": petugas_slug.lower()},
    )
    if cust is None:
        return _redirect_back_with_message(
            error="Customer tidak ditemukan atau bukan milik petugas ini.",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    try:
        # import lokal untuk menghindari circular import
        from .customers import _do_pay_customer  # type: ignore

        msg = _do_pay_customer(reseller, cust, months, router_ip)
    except Exception as e:
        return _redirect_back_with_message(
            error=f"Gagal update pembayaran customer: {e}",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    return _redirect_back_with_message(
        success=msg,
        default_endpoint="petugas.list_petugas_customers",
        default_kwargs={"petugas_slug": petugas_slug},
    )


@bp.route(
    "/petugas/<petugas_slug>/customers/<int:customer_id>/cancel-pay",
    methods=["POST"],
)
def cancel_pay_customer(petugas_slug: str, customer_id: int):
    """
    Batalkan 1 pembayaran terakhir (versi petugas).
    """
    reseller, petugas_name, router_ip, redirect_resp = _require_petugas_login(
        petugas_slug
    )
    if redirect_resp is not None:
        return redirect_resp

    cust = db.query_one(
        """
        SELECT
          id,
          ppp_username,
          billing_start_date,
          last_paid_period,
          is_isolated,
          profile_id
        FROM ppp_customers
        WHERE id = %(cid)s
          AND reseller_id = %(rid)s
          AND LOWER(petugas_name) = %(ptg)s
        """,
        {"cid": customer_id, "rid": reseller["id"], "ptg": petugas_slug.lower()},
    )
    if cust is None:
        return _redirect_back_with_message(
            error="Customer tidak ditemukan atau bukan milik petugas ini.",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    payment = db.query_one(
        """
        SELECT
          id,
          months,
          old_last_period,
          new_last_period,
          old_is_isolated,
          new_is_isolated
        FROM customer_payments
        WHERE customer_id = %(cid)s
          AND reseller_id = %(rid)s
          AND reversed_by_id IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"cid": customer_id, "rid": reseller["id"]},
    )

    if payment is None:
        return _redirect_back_with_message(
            error=f"Tidak ada pembayaran yang bisa dibatalkan untuk user {cust['ppp_username']}.",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    old_last_period = payment["old_last_period"]
    new_last_period = payment["new_last_period"]
    old_iso = payment["old_is_isolated"]
    new_iso = payment["new_is_isolated"]

    try:
        # 1) rollback last_paid_period + is_isolated
        db.execute(
            """
            UPDATE ppp_customers
            SET last_paid_period = %(old_last)s,
                is_isolated      = COALESCE(%(old_iso)s, is_isolated),
                updated_at       = NOW()
            WHERE id = %(cid)s
              AND reseller_id = %(rid)s
            """,
            {
                "old_last": old_last_period,
                "old_iso": old_iso,
                "cid": customer_id,
                "rid": reseller["id"],
            },
        )

        # 2) catat rollback
        rollback = db.query_one(
            """
            INSERT INTO customer_payments (
                customer_id,
                reseller_id,
                months,
                old_last_period,
                new_last_period,
                old_is_isolated,
                new_is_isolated,
                source,
                note
            ) VALUES (
                %(cid)s,
                %(rid)s,
                %(months)s,
                %(old_last)s,
                %(new_last)s,
                %(old_iso)s,
                %(new_iso)s,
                %(source)s,
                %(note)s
            )
            RETURNING id
            """,
            {
                "cid": customer_id,
                "rid": reseller["id"],
                "months": -payment["months"],
                "old_last": new_last_period,
                "new_last": old_last_period,
                "old_iso": new_iso,
                "new_iso": old_iso,
                "source": "cancel_pay_petugas",
                "note": f"Undo payment {payment['id']} via petugas",
            },
        )
        rollback_id = rollback["id"]

        # 3) tandai payment lama sudah di-undo
        db.execute(
            """
            UPDATE customer_payments
            SET reversed_by_id = %(rbid)s
            WHERE id = %(pid)s
            """,
            {"rbid": rollback_id, "pid": payment["id"]},
        )
    except Exception as e:
        return _redirect_back_with_message(
            error=f"Gagal membatalkan pembayaran customer: {e}",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    # 4) opsional: penyesuaian router (isolasi lagi jika perlu)
    try:
        if (
            router_ip
            and router_ip != "-"
            and old_iso is not None
            and new_iso is not None
        ):
            if old_iso is True and new_iso is False:
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
                if iso_profile:
                    from mikrotik_client import (
                        update_ppp_secret,
                        terminate_ppp_active_by_name,
                    )

                    api_user = reseller["router_username"]
                    api_pass = reseller["router_password"]

                    try:
                        update_ppp_secret(
                            router_ip,
                            api_user,
                            api_pass,
                            secret_name=cust["ppp_username"],
                            updates={"profile": iso_profile["name"]},
                        )
                        try:
                            terminate_ppp_active_by_name(
                                router_ip,
                                api_user,
                                api_pass,
                                cust["ppp_username"],
                            )
                        except Exception as e:
                            print(
                                f"[petugas.cancel_pay_customer] gagal terminate session {cust['ppp_username']}: {e}"
                            )
                    except MikrotikError as e:
                        print(
                            f"[petugas.cancel_pay_customer] DB rollback OK, tapi gagal set profile isolasi di router: {e}"
                        )
    except Exception as e:
        print(f"[petugas.cancel_pay_customer] error saat penyesuaian Mikrotik: {e}")

    msg = (
        f"Pembayaran terakhir (ID {payment['id']}) dibatalkan untuk user "
        f"{cust['ppp_username']}."
    )
    return _redirect_back_with_message(
        success=msg,
        default_endpoint="petugas.list_petugas_customers",
        default_kwargs={"petugas_slug": petugas_slug},
    )

@bp.route("/petugas/<petugas_slug>/customer/<int:cid>/print")
def petugas_print_customer(petugas_slug: str, cid: int):
    """
    Fitur print struk pembayaran pelanggan dari panel petugas.
    Menggunakan RawBT (Bluetooth printer) via Intent Android.
    """
    # Ambil context login petugas
    reseller, petugas_name, router_ip, resp = _require_petugas_login(petugas_slug)
    if resp is not None:
        return resp

    # Ambil data pelanggan + profil
    c = db.query_one(
        """
        SELECT c.*, p.name AS profile_name, p.monthly_price
        FROM ppp_customers c
        LEFT JOIN ppp_profiles p ON c.profile_id = p.id
        WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s
        """,
        {"cid": cid, "rid": reseller["id"]},
    )

    if c is None:
        return "Customer tidak ditemukan", 404

    now = datetime.datetime.now()

    # Helper untuk merapikan label
    def pad(label, value):
        return f"{label:<12}: {value}"

    month_name = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
        5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
        9: "September", 10: "Oktober", 11: "November", 12: "Desember",
    }

    # Periode terakhir dibayar
    period_label = "-"
    if c.get("last_paid_period"):
        dt = c["last_paid_period"]
        period_label = f"{month_name.get(dt.month, dt.month)} {dt.year}"

    price = c.get("monthly_price") or 0
    price_str = f"Rp {price:,.0f}".replace(",", ".")

    # Susun isi struk
    lines = []
    lines.append("      " + (reseller["display_name"] or reseller["router_username"]).upper())
    if reseller.get("wa_number"):
        lines.append("      WA: " + reseller["wa_number"])

    lines.append(pad("Petugas", petugas_name or "-"))
    lines.append("----------------------------------------")

    lines.append(pad("Pelanggan", c.get("full_name") or c["ppp_username"]))
    if c.get("address"):
        lines.append(pad("Alamat", c["address"]))
    if c.get("wa_number"):
        lines.append(pad("Whatsapp", c["wa_number"]))

    lines.append("----------------------------------------")
    lines.append(pad("Bulan/Tahun", period_label))
    lines.append(pad("Harga", price_str))
    lines.append("----------------------------------------")
    lines.append(pad("Tanggal", now.strftime("%Y-%m-%d %H:%M:%S")))
    lines.append("----------------------------------------")
    lines.append("       Terima kasih 🙏")
    lines.append("")

    escpos_text = "\n".join(lines)

    # HTML minimal untuk RawBT
    html = """
<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <title>RawBT Print</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <script>
    function BtPrint(prn){
      var S = "#Intent;scheme=rawbt;";
      var P = "package=ru.a402d.rawbtprinter;end;";
      var textEncoded = encodeURI(prn);
      window.location.href = "intent:" + textEncoded + S + P;
    }

    window.addEventListener('load', function () {
      var prn = {{ escpos_text | tojson }};
      BtPrint(prn);
    });
  </script>
</head>
<body>
  <p>Memanggil RawBT...</p>
  <p>Jika tidak otomatis, tap tombol berikut:</p>
  <button onclick="BtPrint({{ escpos_text | tojson }})">
    🖨️ Print via RawBT
  </button>
</body>
</html>
    """

    return render_template_string(html, escpos_text=escpos_text)

@bp.route("/petugas/<petugas_slug>/customer/<int:cid>/edit", methods=["GET", "POST"])
def edit_petugas_customer(petugas_slug: str, cid: int):
    """
    Edit data customer (versi petugas) — tetap di halaman edit, tampil alert hasil.
    """
    reseller, petugas_name, router_ip, resp = _require_petugas_login(petugas_slug)
    if resp is not None:
        return resp

    # Ambil data customer
    c = db.query_one(
        """
        SELECT c.*, p.name AS profile_name
        FROM ppp_customers c
        LEFT JOIN ppp_profiles p ON c.profile_id = p.id
        WHERE c.id = %(cid)s AND c.reseller_id = %(rid)s AND LOWER(c.petugas_name) = %(ptg)s
        """,
        {"cid": cid, "rid": reseller["id"], "ptg": petugas_slug.lower()},
    )
    if not c:
        return _redirect_back_with_message(
            error="Customer tidak ditemukan atau bukan milik petugas ini.",
            default_endpoint="petugas.list_petugas_customers",
            default_kwargs={"petugas_slug": petugas_slug},
        )

    # Ambil daftar profil untuk dropdown
    profiles = db.query_all(
        """
        SELECT id, name FROM ppp_profiles
        WHERE reseller_id = %(rid)s
        ORDER BY name
        """,
        {"rid": reseller["id"]},
    )

    error = None
    success = None

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        address = (request.form.get("address") or "").strip()
        wa_number = (request.form.get("wa_number") or "").strip()
        profile_id = request.form.get("profile_id")

        if not full_name:
            error = "Nama pelanggan wajib diisi."
        else:
            try:
                db.execute(
                    """
                    UPDATE ppp_customers
                    SET full_name = %(fn)s,
                        address = %(addr)s,
                        wa_number = %(wa)s,
                        profile_id = %(pid)s,
                        updated_at = NOW()
                    WHERE id = %(cid)s AND reseller_id = %(rid)s
                    """,
                    {
                        "fn": full_name,
                        "addr": address,
                        "wa": wa_number,
                        "pid": profile_id,
                        "cid": cid,
                        "rid": reseller["id"],
                    },
                )
                success = f"✅ Data pelanggan <b>{c['ppp_username']}</b> berhasil diperbarui."
                # Ambil ulang data agar form menampilkan nilai terbaru
                c = db.query_one(
                    """
                    SELECT c.*, p.name AS profile_name
                    FROM ppp_customers c
                    LEFT JOIN ppp_profiles p ON c.profile_id = p.id
                    WHERE c.id = %(cid)s
                    """,
                    {"cid": cid},
                )
            except Exception as e:
                error = f"Gagal memperbarui data pelanggan: {e}"

    # Tampilkan form edit + alert hasil
    body_html = """
<section class="max-w-md mx-auto">
  <h1 class="mb-4 flex items-center gap-2 text-lg font-semibold tracking-tight">
    ✏️ Edit Pelanggan
  </h1>

  {% if success %}
    <div class="mb-3 rounded border border-emerald-500/70 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
      {{ success|safe }}
    </div>
  {% endif %}
  {% if error %}
    <div class="mb-3 rounded border border-rose-500/70 bg-rose-500/10 px-3 py-2 text-xs text-rose-100">
      ⚠️ {{ error }}
    </div>
  {% endif %}

  <form method="post" class="space-y-4 text-sm">
    <div>
      <label class="block text-xs font-medium text-slate-300">Nama Lengkap</label>
      <input type="text" name="full_name" value="{{ c.full_name or '' }}"
             class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100">
    </div>

    <div>
      <label class="block text-xs font-medium text-slate-300">Alamat</label>
      <textarea name="address" rows="2"
                class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100">{{ c.address or '' }}</textarea>
    </div>

    <div>
      <label class="block text-xs font-medium text-slate-300">Nomor WA</label>
      <input type="text" name="wa_number" value="{{ c.wa_number or '' }}"
             class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100">
    </div>

    <div>
      <label class="block text-xs font-medium text-slate-300">Profil PPP</label>
      <select name="profile_id" class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100">
        {% for p in profiles %}
          <option value="{{ p.id }}" {% if p.id == c.profile_id %}selected{% endif %}>
            {{ p.name }}
          </option>
        {% endfor %}
      </select>
    </div>

    <div class="flex justify-between items-center mt-4">
      <a href="{{ url_for('petugas.list_petugas_customers', petugas_slug=petugas_slug) }}"
         class="rounded-md border border-slate-700 bg-slate-900 px-4 py-2 text-xs text-slate-200 hover:bg-slate-800">
        ← Kembali
      </a>
      <button type="submit"
              class="rounded-md border border-emerald-500/70 bg-emerald-500/10 px-4 py-2 text-xs text-emerald-200 hover:bg-emerald-500/20">
        💾 Simpan
      </button>
    </div>
  </form>
</section>
    """

    return _render_simple_page(
        title=f"Edit Pelanggan - {c['ppp_username']}",
        body_html=body_html,
        context={
            "c": c,
            "profiles": profiles,
            "error": error,
            "success": success,
            "petugas_slug": petugas_slug,
        },
    )
