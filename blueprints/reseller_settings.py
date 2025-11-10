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
<section class="flex flex-col gap-3 border-b border-slate-800 pb-4">
  <div>
    <div class="flex items-center gap-2 text-xs text-slate-500">
      <span>Home</span>
      <span>›</span>
      <span class="text-slate-300">Settings</span>
    </div>
    <h1 class="mt-1 flex items-center gap-2 text-xl font-semibold tracking-tight">
      <span>⚙️</span>
      <span>Pengaturan Reseller</span>
    </h1>
    <p class="mt-1 text-sm text-slate-400">
      Atur profil reseller, kontak, dan fitur otomatis untuk notifikasi &amp; pembayaran.
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

<!-- INFO ROUTER -->
<section class="mt-4 rounded-lg border border-slate-800 bg-slate-900/70 p-4 max-w-xl">
  <h3 class="mb-2 text-sm font-semibold text-slate-200">📡 Info Router</h3>
  <dl class="space-y-1 text-xs text-slate-300">
    <div class="flex justify-between gap-2">
      <dt class="text-slate-400">Router Username</dt>
      <dd class="font-mono text-slate-100">{{ router_username }}</dd>
    </div>
    <div class="flex justify-between gap-2">
      <dt class="text-slate-400">Router IP (L2TP address)</dt>
      <dd class="font-mono text-emerald-300">{{ router_ip or '-' }}</dd>
    </div>
  </dl>
</section>

<!-- FORM PENGATURAN -->
<form method="post" class="mt-4 space-y-4 max-w-xl">

  <!-- Profil Reseller -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">🧩 Profil Reseller</h3>

    <div class="space-y-3 text-sm">
      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          🏷️ Nama Reseller
        </label>
        <input
          type="text"
          name="display_name"
          value="{{ display_name or '' }}"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0"
          placeholder="misal: Warga NET"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          📱 WA Number
        </label>
        <input
          type="text"
          name="wa_number"
          value="{{ wa_number or '' }}"
          placeholder="6285xxxx"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0"
        >
      </div>

      <div class="space-y-1">
        <label class="block text-xs font-medium text-slate-300">
          📧 Email
        </label>
        <input
          type="email"
          name="email"
          value="{{ email or '' }}"
          placeholder="email@example.com"
          class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0"
        >
      </div>
    </div>
  </section>

  <!-- Fitur Otomatis -->
  <section class="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
    <h3 class="mb-3 text-sm font-semibold text-slate-200">🔔 Fitur Otomatis</h3>

    <div class="space-y-2 text-sm">
      <label class="flex items-start gap-2 text-xs text-slate-200">
        <input
          type="checkbox"
          name="use_notifications"
          {% if use_notifications %}checked{% endif %}
          class="mt-0.5 h-3 w-3 rounded border-slate-600 bg-slate-900"
        >
        <span>Aktifkan notifikasi WA ke pelanggan</span>
      </label>

      <label class="flex items-start gap-2 text-xs text-slate-200">
        <input disabled
          type="checkbox"
          name="use_auto_payment"
          {% if use_auto_payment %}checked{% endif %}
          class="mt-0.5 h-3 w-3 rounded border-slate-600 bg-slate-900"
        >
        <span>
          Aktifkan integrasi pembayaran otomatis (duitku)
          <span class="text-slate-400">(opsional / nanti)</span>
        </span>
      </label>
    </div>

    <p class="mt-3 text-[11px] text-slate-500 leading-relaxed">
      Catatan:<br>
      - Jika notifikasi WA diaktifkan, cron akan mengirim pesan otomatis untuk tagihan jatuh tempo.<br>
      - Jika auto payment diaktifkan, invoice bisa memiliki link pembayaran online (fitur menyusul).
    </p>
  </section>

  <!-- Tombol Aksi -->
  <div class="flex flex-wrap items-center gap-2 pt-1">
    <button
      type="submit"
      class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-4 py-2 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
      💾 <span>Simpan Pengaturan</span>
    </button>

    <a href="{{ url_for('main.dashboard') }}"
       class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
      🏠 <span>Kembali ke Dashboard</span>
    </a>
  </div>
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
