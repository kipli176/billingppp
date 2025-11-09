# blueprints/reseller_settings.py

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

bp = Blueprint("reseller_settings", __name__)


def _require_login():
    """
    Helper sederhana: pastikan reseller sudah login.
    Return (reseller_row, router_ip) atau redirect ke login.
    """
    reseller_id = session.get("reseller_id")
    router_ip = session.get("router_ip")

    if not reseller_id:
        return None, None, redirect(url_for("auth_reseller.login"))

    reseller = db.query_one(
        """
        SELECT id, display_name, router_username,
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


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    """
    Halaman pengaturan reseller:
    - View + update display_name, WA, email
    - Toggle use_notifications & use_auto_payment
    """
    reseller, router_ip, redirect_resp = _require_login()
    if redirect_resp is not None:
        return redirect_resp

    error: str | None = None
    success: str | None = None

    # Nilai awal dari DB
    display_name = reseller["display_name"] or reseller["router_username"]
    wa_number = reseller.get("wa_number") or ""
    email = reseller.get("email") or ""
    use_notifications = bool(reseller.get("use_notifications", False))
    use_auto_payment = bool(reseller.get("use_auto_payment", False))

    if request.method == "POST":
        display_name = (request.form.get("display_name") or "").strip()
        wa_number = (request.form.get("wa_number") or "").strip()
        email = (request.form.get("email") or "").strip()
        use_notifications = request.form.get("use_notifications") == "on"
        use_auto_payment = request.form.get("use_auto_payment") == "on"

        if not display_name:
            error = "Nama reseller tidak boleh kosong."
        else:
            try:
                db.execute(
                    """
                    UPDATE resellers
                    SET display_name = %(dn)s,
                        wa_number = %(wa)s,
                        email = %(em)s,
                        use_notifications = %(un)s,
                        use_auto_payment = %(ua)s,
                        updated_at = NOW()
                    WHERE id = %(rid)s
                    """,
                    {
                        "dn": display_name,
                        "wa": wa_number or None,
                        "em": email or None,
                        "un": use_notifications,
                        "ua": use_auto_payment,
                        "rid": reseller["id"],
                    },
                )
                success = "Pengaturan berhasil disimpan."
            except Exception as e:
                error = f"Gagal menyimpan pengaturan: {e}"

    body_html = """
<h1>⚙️ Pengaturan Reseller</h1>

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}

{% if success %}
  <p style="color:#00ff00;">✅ {{ success }}</p>
{% endif %}

<div style="border:1px solid #0f0; padding:8px; margin-bottom:10px; max-width:600px;">
  <h3>📡 Info Router</h3>
  <table>
    <tr>
      <th>Router Username</th>
      <td>{{ router_username }}</td>
    </tr>
    <tr>
      <th>Router IP (L2TP address)</th>
      <td>{{ router_ip or '-' }}</td>
    </tr>
  </table>
</div>

<form method="post" style="max-width:600px;">

  <div style="border:1px solid #0f0; padding:8px; margin-bottom:10px;">
    <h3>🧩 Profil Reseller</h3>

    <label>
      🏷️ Nama Reseller<br>
      <input type="text" name="display_name" value="{{ display_name or '' }}"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      📱 WA Number<br>
      <input type="text" name="wa_number" value="{{ wa_number or '' }}"
             placeholder="6285xxxx"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
    <br><br>

    <label>
      📧 Email<br>
      <input type="email" name="email" value="{{ email or '' }}"
             placeholder="email@example.com"
             style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
    </label>
  </div>

  <div style="border:1px solid #0f0; padding:8px; margin-bottom:10px;">
    <h3>🔔 Fitur Otomatis</h3>

    <label style="display:block; margin-bottom:6px;">
      <input type="checkbox" name="use_notifications" {% if use_notifications %}checked{% endif %}
             style="margin-right:4px;">
      Aktifkan notifikasi WA ke pelanggan
    </label>

    <label style="display:block; margin-bottom:6px;">
      <input type="checkbox" name="use_auto_payment" {% if use_auto_payment %}checked{% endif %}
             style="margin-right:4px;">
      Aktifkan integrasi pembayaran otomatis (duitku) <span style="opacity:0.7;">(opsional / nanti)</span>
    </label>

    <p style="font-size:12px; opacity:0.8; margin-top:8px;">
      Catatan:<br>
      - Jika notifikasi WA diaktifkan, cron akan mengirim pesan otomatis untuk tagihan jatuh tempo.<br>
      - Jika auto payment diaktifkan, invoice bisa memiliki link pembayaran online (fitur menyusul).
    </p>
  </div>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    💾 Simpan Pengaturan
  </button>

  <a href="{{ url_for('main.dashboard') }}" class="btn" style="margin-left:8px;">🏠 Kembali ke Dashboard</a>
</form>
    """

    return render_terminal_page(
        title="Pengaturan Reseller",
        body_html=body_html,
        context={
            "error": error,
            "success": success,
            "router_ip": router_ip,
            "router_username": reseller["router_username"],
            "display_name": display_name,
            "wa_number": wa_number,
            "email": email,
            "use_notifications": use_notifications,
            "use_auto_payment": use_auto_payment,
        },
    )
