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

from flask import Flask, redirect, url_for, render_template_string, session

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
        Untuk saat ini:
        - jika sudah login (nanti pakai session['reseller_id']) → redirect ke dashboard
        - kalau belum → sementara tampil halaman sederhana (tes template)
        """
        if session.get("reseller_id"):
            # nanti kita arahkan ke main.dashboard
            try:
                return redirect(url_for("main.dashboard"))
            except Exception:
                # Kalau blueprint main belum ada, tampilkan pesan sederhana
                pass

        # Halaman test awal
        body_html = """
<h1>🖥️ PPP Billing & Monitoring</h1>
<p>Ini hanya halaman awal sementara.</p>
<p>Nanti di sini akan diarahkan ke halaman <b>login reseller</b>.</p>

<hr>

<pre>
Database test:
  {{ db_status }}
</pre>
        """

        # Tes koneksi DB singkat
        try:
            row = db.query_one("SELECT NOW() AS now;")
            db_status = f"Connected (NOW = {row['now']})"
        except Exception as e:
            db_status = f"ERROR: {e}"

        return render_terminal_page(
            title="Welcome",
            body_html=body_html,
            context={"db_status": db_status},
        )

    return app


def render_terminal_page(title: str, body_html: str, context: Dict[str, Any] | None = None) -> str:
    """
    Helper untuk merender HTML dengan tema "terminal hijau-hitam".

    Langkah:
    1. body_html dirender dulu sebagai template Jinja (supaya {{ var }} dan {% if %} jalan).
    2. Hasilnya disisipkan ke template utama sebagai {{ body|safe }}.
    """
    if context is None:
        context = {}

    # 1) render isi body (inner template)
    body_rendered = render_template_string(body_html, **context)

    # 2) template utama (frame hijau-hitam)
    base_template = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg-color: #000000;
      --fg-color: #00ff00;
      --accent-color: #00cc66;
      --danger-color: #ff5555;
      --warning-color: #f1fa8c;
      --info-color: #8be9fd;
      --success-color: #50fa7b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      background-color: var(--bg-color);
      color: var(--fg-color);
      font-family: "Fira Code", "Consolas", monospace;
    }
    a {
      color: var(--accent-color);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    .wrapper {
      max-width: 1100px;
      margin: 0 auto;
      padding: 10px 16px 40px;
    }
    .window {
      border: 1px solid var(--fg-color);
      padding: 8px 10px 16px;
      box-shadow: 0 0 8px #008800;
      margin-top: 8px;
    }
    .window-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      border-bottom: 1px solid var(--fg-color);
      padding-bottom: 4px;
    }
    .title { font-weight: bold; }
    .window-controls span {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-left: 4px;
    }
    .dot-red   { background-color: #ff5555; }
    .dot-yellow{ background-color: #f1fa8c; }
    .dot-green { background-color: #50fa7b; }

    .menu {
      margin: 8px 0;
    }
    .btn {
      display: inline-block;
      padding: 4px 8px;
      margin: 2px 2px;
      border: 1px solid var(--fg-color);
      border-radius: 4px;
      background: rgba(0, 255, 0, 0.05);
      font-size: 14px;
    }
    .btn:hover {
      background: rgba(0, 255, 0, 0.15);
    }
    .btn-danger {
      border-color: var(--danger-color);
      color: var(--danger-color);
    }
    .btn-danger:hover {
      background: rgba(255, 85, 85, 0.15);
    }
    .btn-warning {
      border-color: var(--warning-color);
      color: var(--warning-color);
    }
    .btn-warning:hover {
      background: rgba(241, 250, 140, 0.15);
    }
    .btn-info {
      border-color: var(--info-color);
      color: var(--info-color);
    }
    .btn-info:hover {
      background: rgba(139, 233, 253, 0.15);
    }
    .btn-success {
      border-color: var(--success-color);
      color: var(--success-color);
    }
    .btn-success:hover {
      background: rgba(80, 250, 123, 0.15);
    }
    

    hr {
      border: none;
      border-top: 1px solid var(--fg-color);
      margin: 10px 0;
    }
    pre {
      white-space: pre-wrap;
      word-wrap: break-word;
      font-size: 13px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 6px 0;
      font-size: 13px;
    }
    th, td {
      border: 1px solid var(--fg-color);
      padding: 3px 5px;
      text-align: left;
    }
    th {
      background: rgba(0, 255, 0, 0.1);
    }
    .footer {
      margin-top: 14px;
      font-size: 12px;
      opacity: 0.7;
    }
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="window">
      <div class="window-header">
        <div class="title">🖥️ PPP Billing & Monitoring – {{ title }}</div>
        <div class="window-controls">
          <span class="dot-red"></span>
          <span class="dot-yellow"></span>
          <span class="dot-green"></span>
        </div>
      </div>

      <!-- Navbar sederhana (nanti kita isi link beneran setelah blueprint jadi) -->
<div class="menu">
  <a href="{{ url_for('main.dashboard') }}" class="btn">🏠 Dashboard</a> 
  <a href="{{ url_for('customers.list_customers') }}" class="btn">👤 Customers</a> 
  <a href="{{ url_for('reports.unpaid_users') }}" class="btn">📊 Reports</a>
  <a href="{{ url_for('auth_reseller.logout') }}" class="btn btn-danger">🚪 Logout</a>
</div>


      <hr>

      {{ body|safe }}

      <div class="footer">
        <hr>
        <div>Theme: Terminal Green on Black · Powered by Flask</div>
      </div>
    </div>
  </div>
</body>
</html>
"""
    return render_template_string(base_template, title=title, body=body_rendered)


# Jalankan langsung: python app.py
if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.config.get("DEBUG", True), host="0.0.0.0", port=5000)
