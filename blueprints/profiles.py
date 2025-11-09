# blueprints/profiles.py

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
from mikrotik_client import get_ppp_profiles, MikrotikError

bp = Blueprint("profiles", __name__)


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

    return reseller, router_ip, None


@bp.route("/profiles", methods=["GET"])
def list_profiles():
    """
    Tampilkan daftar PPP profiles untuk reseller yang login
    berdasarkan view v_profiles.
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = request.args.get("error") or None
    success = request.args.get("success") or None

    profiles = []
    db_error = None

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
        db_error = f"Gagal mengambil data profil: {e}"

    body_html = """
<h1>📡 PPP Profiles</h1>
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
  <form method="post" action="{{ url_for('profiles.sync_profiles') }}" style="display:inline;">
    <button type="submit"
            style="padding:4px 10px; background:#001a00; color:#0f0;
                   border:1px solid #0f0; border-radius:4px; cursor:pointer;">
      🔄 Sinkron dari Router
    </button>
  </form>

  <a href="{{ url_for('main.dashboard') }}" class="btn" style="margin-left:8px;">🏠 Kembali Dashboard</a>
</div>

<div style="border:1px solid #0f0; padding:8px; margin-top:4px;">
  <h3>Daftar Profil</h3>

  {% if profiles %}
    <table>
      <tr>
        <th>Nama Profil</th>
        <th>Deskripsi</th>
        <th>Rate Limit</th>
        <th>Isolation?</th>
        <th>Harga /bulan</th>
        <th>Total User</th>
        <th>Enabled</th>
      </tr>
      {% for p in profiles %}
      <tr>
        <td>{{ p.profile_name }}</td>
        <td>{{ p.description or '-' }}</td>
        <td>{{ p.rate_limit or '-' }}</td>
        <td>{{ 'YES' if p.is_isolation else 'NO' }}</td>
        <td>Rp {{ '{:,.0f}'.format(p.monthly_price or 0) }}</td>
        <td>{{ p.total_customers }}</td>
        <td>{{ p.enabled_customers }}</td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>Tidak ada profil di database. Coba klik "Sinkron dari Router".</p>
  {% endif %}
</div>

<pre style="font-size:12px; opacity:0.8; margin-top:8px;">
Catatan:
- "Sinkron dari Router" akan membaca /ppp/profile di router reseller dan
  menyimpan/merge ke tabel ppp_profiles (per reseller).
- "Isolation?" bisa kamu ubah langsung di database dulu,
  nanti kita buat halaman edit profil terpisah.
</pre>
    """

    return render_terminal_page(
        title="PPP Profiles",
        body_html=body_html,
        context={
            "reseller_name": reseller["display_name"] or reseller["router_username"],
            "profiles": profiles,
            "error": error,
            "success": success,
            "db_error": db_error,
        },
    )


@bp.route("/profiles/sync", methods=["POST"])
def sync_profiles():
    """
    Sinkron profil dari router reseller:
    - Ambil /ppp/profile dari Mikrotik reseller
    - Untuk setiap profile:
        INSERT INTO ppp_profiles (reseller_id, name, description, rate_limit)
        ON CONFLICT (reseller_id, name) DO UPDATE
    - monthly_price dan is_isolation TIDAK diubah (biar tetap sesuai setting billing).
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error = None
    success = None

    if not router_ip:
        error = "Router IP tidak tersedia di session. Silakan login ulang."
        return redirect(url_for("profiles.list_profiles", error=error))

    api_user = reseller["router_username"]
    api_pass = reseller["router_password"]

    # 1. Ambil profil dari router
    try:
        mt_profiles = get_ppp_profiles(router_ip, api_user, api_pass)
    except MikrotikError as e:
        error = f"Gagal mengambil profil dari router: {e}"
        return redirect(url_for("profiles.list_profiles", error=error))
    except Exception as e:
        error = f"Error tidak terduga saat akses router: {e}"
        return redirect(url_for("profiles.list_profiles", error=error))

    if not mt_profiles:
        error = "Router tidak mengembalikan data profil PPP."
        return redirect(url_for("profiles.list_profiles", error=error))

    # 2. Upsert ke ppp_profiles
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
            row = db.query_one(
                """
                INSERT INTO ppp_profiles
                    (reseller_id, name, description, rate_limit,
                     is_isolation, monthly_price, created_at, updated_at)
                VALUES
                    (%(rid)s, %(name)s, %(desc)s, %(rate)s,
                     FALSE, 0, NOW(), NOW())
                ON CONFLICT (reseller_id, name)
                DO UPDATE
                    SET description = EXCLUDED.description,
                        rate_limit  = EXCLUDED.rate_limit,
                        updated_at  = NOW()
                RETURNING xmax = 0 AS inserted
                """,
                {
                    "rid": reseller["id"],
                    "name": name,
                    "desc": desc,
                    "rate": rate_limit,
                },
            )
            if row and row["inserted"]:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            # kalau satu profil error, kita skip tapi tulis ke log
            print(f"[sync_profiles] gagal upsert profile {name}: {e}")

    success = f"Sinkron selesai. {inserted} profil baru, {updated} profil diperbarui."
    return redirect(url_for("profiles.list_profiles", success=success))
