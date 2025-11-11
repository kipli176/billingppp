"""
app.py
------
Entry point Flask.

- Membuat Flask app
- Load Config
- Inisialisasi koneksi DB
- (Nanti) register blueprint auth_reseller, main, dll
- Menyediakan helper render_terminal_page() untuk tema hijau-hitam
"""

from __future__ import annotations

from typing import Dict, Any

from flask import Flask, app, redirect, url_for, render_template_string, session, request
import datetime
from config import Config
import db


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Inisialisasi koneksi Postgres
    db.init_app(app)

    # Nanti di sini kita register blueprint:
    from blueprints import (
        auth_reseller,
        main,
        reseller_settings,
        profiles,
        customers,
        invoices,
        reports,
        admin,
    )

    app.register_blueprint(auth_reseller.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(reseller_settings.bp)
    app.register_blueprint(profiles.bp)
    app.register_blueprint(customers.bp)
    app.register_blueprint(invoices.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(admin.bp)

    @app.route("/")
    def index():
        """
        Halaman utama aplikasi.
        - Jika reseller sudah login → redirect ke dashboard utama.
        - Jika belum login → tampilkan halaman login (Tailwind).
        """
        # Jika reseller sudah login, langsung ke dashboard
        if session.get("reseller_id"):
            try:
                return redirect(url_for("main.dashboard"))
            except Exception:
                # Jika blueprint main belum terdaftar (mis. saat dev), tampil fallback
                return render_terminal_page(
                    title="Dashboard",
                    body_html="<p>Blueprint <b>main</b> belum aktif.</p>",
                    context={},
                )

        # Jika belum login, tampilkan halaman login langsung di sini
        body_html = """
    <div class="flex min-h-[60vh] items-center justify-center">
    <div class="w-full max-w-md space-y-5 rounded-xl border border-slate-800 bg-slate-900/80 p-6 shadow-lg">
        <div class="space-y-1 text-center">
        <h1 class="flex items-center justify-center gap-2 text-lg font-semibold">
            <span>🔐</span>
            <span>Login Reseller</span>
        </h1>
        <p class="text-xs text-slate-400">
            Silakan masuk menggunakan <span class="font-mono">router_username</span> dan <span class="font-mono">router_password</span>.
        </p>
        </div>

        <form method="post" action="{{ url_for('auth_reseller.login') }}" class="space-y-4">
        <div class="space-y-1 text-sm">
            <label class="block text-xs font-medium text-slate-300">👤 Username</label>
            <input type="text" name="username"
                class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0"
                placeholder="router_username" required>
        </div>

        <div class="space-y-1 text-sm">
            <label class="block text-xs font-medium text-slate-300">🔒 Password</label>
            <input type="password" name="password"
                class="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-emerald-500 focus:outline-none focus:ring-0"
                placeholder="router_password" required>
        </div>

        <div class="flex items-center justify-between pt-2">
            <button type="submit"
                    class="inline-flex items-center gap-1 rounded-md border border-brand-500 bg-brand-500/10 px-4 py-2 text-xs font-medium text-emerald-300 hover:bg-brand-500/20">
            ▶️ <span>Login</span>
            </button>

            <a href="{{ url_for('auth_reseller.register') }}"
            class="inline-flex items-center gap-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-200 hover:border-slate-500 hover:bg-slate-800">
            📝 <span>Daftar reseller baru</span>
            </a>
        </div>
        </form>
    </div>
    </div>
        """

        # Tes koneksi DB (optional, seperti sebelumnya)
        try:
            row = db.query_one("SELECT NOW() AS now;")
            db_status = f"Connected (NOW = {row['now']})"
        except Exception as e:
            db_status = f"ERROR: {e}"

        return render_terminal_page(
            title="Login Reseller",
            body_html=body_html,
            context={"db_status": db_status},
        )

    # =============================
    # GLOBAL LOCK INVOICE
    # =============================
    @app.before_request
    def check_invoice_lock_global():
        """
        Lock global:
        Jika invoice reseller bulan ini belum dibayar dan sudah lewat tanggal batas,
        cegah semua route (GET/POST) dan redirect ke dashboard.
        """

        # endpoint bisa None (misal untuk static/error)
        if request.endpoint is None:
            return

        # Bebaskan beberapa endpoint penting:
        # - auth_reseller.* -> login/logout
        # - static -> file statis
        # - main.dashboard -> halaman invoice
        if request.endpoint.startswith("auth_reseller."):
            return
        if request.endpoint == "static":
            return
        if request.endpoint == "main.dashboard":
            return

        # Kalau belum login reseller, biarkan flow normal
        reseller_id = session.get("reseller_id")
        if not reseller_id:
            return

        # Cek invoice bulan ini
        today = datetime.date.today()
        current_period_start = today.replace(day=1)

        try:
            invoice = db.query_one(
                """
                SELECT *
                FROM v_reseller_invoices
                WHERE reseller_id = %(rid)s
                  AND period_start = %(ps)s
                ORDER BY period_start DESC
                LIMIT 1
                """,
                {"rid": reseller_id, "ps": current_period_start},
            )
        except Exception as e:
            print(f"[check_invoice_lock_global] gagal ambil invoice: {e}")
            return

        # Kondisi lock: belum bayar & lewat tanggal 10
        if invoice and invoice["status"] != "paid" and today.day > 10:
            return redirect(url_for("main.dashboard"))

        # kalau tidak locked, lanjut normal
        return

    # ⚠️ Jangan hapus ini — ini adalah akhir dari create_app()
    return app



def render_terminal_page(title: str, body_html: str, context: Dict[str, Any] | None = None) -> str:
    """
    Helper untuk merender HTML dengan tema baru (dark + Tailwind).
    body_html tetap di-render sebagai template Jinja, lalu disisipkan
    ke dalam base layout sebagai {{ body|safe }}.
    """
    from flask import render_template_string, url_for, request, session  # pastikan import ini ada / sesuai

    if context is None:
        context = {}

    # 1) render isi body (inner template)
    body_rendered = render_template_string(body_html, **context)

    # 2) template utama (frame Tailwind, navbar di atas)
    base_template = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <!-- Tailwind CDN -->
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
{% if session.get('reseller_id') %}
  <!-- NAVBAR ATAS -->
  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur supports-[backdrop-filter]:bg-slate-900/60 sticky top-0 z-20">
    <div class="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
      <!-- Brand -->
      <div class="flex items-center gap-2">
        <div class="flex h-8 w-8 items-center justify-center rounded-lg border border-brand-500/40 bg-brand-500/10">
          <span class="text-lg">🖥️</span>
        </div>
        <div>
          <div class="text-sm font-semibold tracking-tight text-emerald-400">
            PPP Billing &amp; Monitoring
          </div>
          <div class="text-xs text-slate-400">
            {{ title }}
          </div>
        </div>
      </div>

      <!-- Menu desktop -->
      <nav class="hidden items-center gap-4 text-sm md:flex">
        <a href="{{ url_for('main.dashboard') }}"
           class="flex items-center gap-1 {% if request.endpoint == 'main.dashboard' %}text-emerald-400{% else %}text-slate-300 hover:text-emerald-300{% endif %}">
          🏠 <span>Dashboard</span>
        </a>
        <a href="{{ url_for('customers.list_customers') }}"
           class="flex items-center gap-1 {% if request.endpoint and request.endpoint.startswith('customers.') %}text-emerald-400{% else %}text-slate-300 hover:text-emerald-300{% endif %}">
          👤 <span>Customers</span>
        </a>
        <a href="{{ url_for('reports.unpaid_users') }}"
           class="flex items-center gap-1 {% if request.endpoint and request.endpoint.startswith('reports.') %}text-emerald-400{% else %}text-slate-300 hover:text-emerald-300{% endif %}">
          📊 <span>Reports</span>
        </a>
        <a href="{{ url_for('reseller_settings.settings') }}"
           class="flex items-center gap-1 {% if request.endpoint and request.endpoint.startswith('reseller_settings.') %}text-emerald-400{% else %}text-slate-300 hover:text-emerald-300{% endif %}">
          ⚙️ <span>Settings</span>
        </a>
        <a href="{{ url_for('auth_reseller.logout') }}"
           class="flex items-center gap-1 text-rose-400 hover:text-rose-300">
          🚪 <span>Logout</span>
        </a>
      </nav>

      <!-- Tombol menu mobile -->
      <button id="menu-btn"
              class="inline-flex items-center justify-center rounded border border-slate-700 px-2 py-1 text-slate-300 hover:bg-slate-800 md:hidden">
        ☰
      </button>
    </div>

    <!-- Menu mobile -->
    <nav id="mobile-menu" class="hidden flex-col border-t border-slate-800 bg-slate-900/90 px-4 py-2 text-sm md:hidden">
      <a href="{{ url_for('main.dashboard') }}" class="block py-1 text-emerald-400">🏠 Dashboard</a>
      <a href="{{ url_for('customers.list_customers') }}" class="block py-1 text-slate-300">👤 Customers</a>
      <a href="{{ url_for('reports.unpaid_users') }}" class="block py-1 text-slate-300">📊 Reports</a>
      <a href="{{ url_for('reseller_settings.settings') }}" class="block py-1 text-slate-300">⚙️ Settings</a>
      <a href="{{ url_for('auth_reseller.logout') }}" class="block py-1 text-rose-400">🚪 Logout</a>
    </nav>
  </header>
{% endif %}


    <!-- KONTEN -->
    <main class="flex-1 overflow-y-auto">
      <div class="mx-auto max-w-6xl px-4 py-4 lg:px-6 lg:py-6">
        {{ body|safe }}
      </div>
    </main>

    <!-- FOOTER -->
    <footer class="border-t border-slate-800 bg-slate-900/80 text-[11px] text-slate-500">
      <div class="mx-auto max-w-6xl px-4 py-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>PPP Billing &amp; Monitoring · Flask</div>
        <div>Theme: Dark Admin · Tailwind</div>
      </div>
    </footer>
  </div>

  <!-- Script: mobile menu toggle -->
  <script>
    const menuBtn = document.getElementById("menu-btn");
    const mobileMenu = document.getElementById("mobile-menu");
    if (menuBtn && mobileMenu) {
      menuBtn.addEventListener("click", () => {
        mobileMenu.classList.toggle("hidden");
      });
    }
  </script>
</body>
</html>
""" 
    return render_template_string(base_template, title=title, body=body_rendered, **context)


# Jalankan langsung: python app.py
if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.config.get("DEBUG", True), host="0.0.0.0", port=5000)
