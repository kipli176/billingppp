# blueprints/auth_reseller.py

from __future__ import annotations

import requests
from requests.auth import HTTPBasicAuth

from flask import (
    Blueprint,
    request,
    session,
    redirect,
    url_for,
    current_app,
)

import db
from app import render_terminal_page

bp = Blueprint("auth_reseller", __name__)


# ======================================================================
# Helper untuk komunikasi dengan Router Admin (router pusat)
# ======================================================================

def _router_admin_request(
    method: str,
    path: str,
    json_body: dict | None = None,
    timeout: int = 8,
) -> dict | str | None:
    """
    Helper panggilan REST ke Router Admin (router pusat).

    - Base URL diambil dari config.ROUTER_ADMIN_BASE_URL
      Contoh: "http://203.190.43.51/rest"
    - path harus mulai dengan "/ppp/..." misalnya:
      "/ppp/secret" atau "/ppp/secret/<name>"

    Auth pakai:
    - ROUTER_ADMIN_USER
    - ROUTER_ADMIN_PASSWORD

    Return:
    - dict (hasil JSON) kalau JSON valid
    - text string kalau bukan JSON
    - None kalau body kosong
    """
    base_url = current_app.config.get("ROUTER_ADMIN_BASE_URL")
    admin_user = current_app.config.get("ROUTER_ADMIN_USER")
    admin_pass = current_app.config.get("ROUTER_ADMIN_PASSWORD")

    if not base_url:
        raise RuntimeError("ROUTER_ADMIN_BASE_URL belum diset di Config.")
    if not admin_user or not admin_pass:
        raise RuntimeError("ROUTER_ADMIN_USER/ROUTER_ADMIN_PASSWORD belum diset di Config.")

    url = f"{base_url.rstrip('/')}{path}"

    resp = requests.request(
        method=method.upper(),
        url=url,
        auth=HTTPBasicAuth(admin_user, admin_pass),
        json=json_body,
        timeout=timeout,
    )

    if not resp.ok:
        raise RuntimeError(f"Router Admin error HTTP {resp.status_code}: {resp.text}")

    if resp.text.strip() == "":
        return None

    try:
        return resp.json()
    except Exception:
        return resp.text


def _create_l2tp_secret_for_reseller(username: str, password: str) -> None:
    """
    Membuat PPP secret (L2TP) di Router Admin untuk reseller baru.

    Body JSON dikirim ke:
      PUT /rest/ppp/secret

    Field penting:
    - name     : username reseller (L2TP)
    - password : password L2TP
    - service  : "l2tp"
    """
    body = {
        "name": username,
        "password": password,
        "service": "l2tp",
        "profile": "billing",
        "comment": "billing",
    }
    _router_admin_request("PUT", "/ppp/secret", json_body=body)



def _get_router_ip_for_reseller(username: str) -> str | None:
    """
    Ambil IP router reseller dari Router Admin berdasarkan PPP active (L2TP).

    Alur:
    - GET /rest/ppp/active  di Router Admin
    - Response biasanya list of dict:
        [
          {
            "name": "warganet",
            "address": "10.168.255.254",
            "caller-id": "203.190.46.183",
            ...
          },
          ...
        ]
    - Kita cari entry dengan name == username
    - Ambil field "address" sebagai IP router reseller (IP L2TP remote)
    """
    try:
        data = _router_admin_request("GET", "/ppp/active")
    except Exception as e:
        print(f"[RouterAdmin] gagal ambil /ppp/active: {e}")
        return None

    if data is None:
        return None

    # Jika RouterOS mengembalikan 1 object saja (tidak umum, tapi kita antisipasi)
    if isinstance(data, dict):
        data_list = [data]
    elif isinstance(data, list):
        data_list = data
    else:
        return None

    for row in data_list:
        if not isinstance(row, dict):
            continue
        if row.get("name") != username:
            continue
        addr = row.get("address")
        if isinstance(addr, str) and addr.strip():
            ip = addr.strip()
            # kalau formatnya "10.168.255.254/32" atau sejenis, ambil bagian depannya
            ip = ip.split()[0]
            ip = ip.split("/")[0]
            print(f"[RouterAdmin] IP router untuk {username} = {ip}")
            return ip

    # Tidak ketemu entry dengan name = username
    return None



# ======================================================================
# Route: Registrasi Reseller
# ======================================================================

@bp.route("/register", methods=["GET", "POST"])
@bp.route("/register", methods=["GET", "POST"])
def register():
    """
    Registrasi reseller baru (tanpa input router IP).
    """
    error: str | None = None
    success: str | None = None

    username = ""
    display_name = ""
    wa_number = ""
    email = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        display_name = (request.form.get("display_name") or "").strip()
        wa_number = (request.form.get("wa_number") or "").strip()
        email = (request.form.get("email") or "").strip()

        # Validasi dasar
        if not username or not password:
            error = "Username dan password wajib diisi."
        else:
            # Cek apakah username sudah ada di resellers
            existing = db.query_one(
                "SELECT id FROM resellers WHERE router_username = %(u)s",
                {"u": username},
            )
            if existing:
                error = "Username sudah terdaftar sebagai reseller."
            else:
                # 3) Buat PPP secret di Router Admin
                try:
                    _create_l2tp_secret_for_reseller(username, password)
                except Exception as e:
                    error = f"Gagal membuat L2TP di Router Admin: {e}"
                else:
                    # 4) Simpan reseller di DB (pakai db.execute agar commit)
                    try:
                        db.execute(
                            """
                            INSERT INTO resellers
                                (router_username, router_password, display_name,
                                 wa_number, email, is_active, created_at, updated_at)
                            VALUES
                                (%(u)s, %(p)s, %(dn)s, %(wa)s, %(em)s, TRUE, NOW(), NOW())
                            """,
                            {
                                "u": username,
                                "p": password,
                                "dn": display_name or username,
                                "wa": wa_number or None,
                                "em": email or None,
                            },
                        )
                    except Exception as e:
                        error = f"Gagal menyimpan reseller ke database: {e}"
                    else:
                        # 5) Redirect ke login dengan pesan sukses
                        return redirect(url_for("auth_reseller.login") + "?registered=1")

    body_html = """
<h1>📝 Registrasi Reseller</h1>

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}

{% if success %}
  <p style="color:#00ff00;">✅ {{ success }}</p>
{% endif %}

<form method="post" style="margin-top:10px; max-width:420px;">

  <label>
    👤 Username (L2TP / PPP Name)<br>
    <input type="text" name="username" value="{{ username or '' }}"
           placeholder="misal: r1net"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    🔒 Password<br>
    <input type="password" name="password"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    🏷️ Nama Reseller (optional)<br>
    <input type="text" name="display_name" value="{{ display_name or '' }}"
           placeholder="misal: R1NET Fiber"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    📱 WA Number (optional)<br>
    <input type="text" name="wa_number" value="{{ wa_number or '' }}"
           placeholder="6285xxxx"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    📧 Email (optional)<br>
    <input type="email" name="email" value="{{ email or '' }}"
           placeholder="email@example.com"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    ✅ Daftar
  </button>

  <a href="{{ url_for('auth_reseller.login') }}" class="btn" style="margin-left:8px;">🔐 Sudah Punya Akun</a>
</form>
    """

    return render_terminal_page(
        title="Registrasi Reseller",
        body_html=body_html,
        context={
            "error": error,
            "success": success,
            "username": username,
            "display_name": display_name,
            "wa_number": wa_number,
            "email": email,
        },
    )


# ======================================================================
# Route: Login Reseller
# ======================================================================

@bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Login reseller.

    Form:
    - username = router_username
    - password = router_password

    Alur:
    1) Cek username/password ke tabel resellers.
    2) Jika cocok, minta Router Admin mengambil PPP secret:
       GET /rest/ppp/secret/<username>
    3) Ambil remote-address sebagai IP router reseller.
    4) Simpan reseller_id, router_username, router_ip ke session.
    5) Redirect ke halaman utama ("/").
    """
    if session.get("reseller_id"):
        return redirect(url_for("index"))

    error: str | None = None
    info: str | None = None
    username = ""
    l2tp_script: str | None = None  # <-- tambahan

    # pesan kecil kalau baru selesai registrasi
    if request.args.get("registered"):
        info = "Registrasi berhasil, silakan login dengan username & password yang tadi dibuat."

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not username or not password:
            error = "Username dan password wajib diisi."
        else:
            # 1) cek ke DB
            row = db.query_one(
                """
                SELECT *
                FROM resellers
                WHERE router_username = %(u)s
                """,
                {"u": username},
            )

            if row is None:
                error = "Reseller tidak ditemukan."
            elif row["router_password"] != password:
                error = "Password salah."
            elif not row["is_active"]:
                error = "Akun reseller sedang non-aktif. Hubungi admin."
            else:
                # 2) Ambil IP router dari Router Admin
                router_ip = _get_router_ip_for_reseller(username)
                if not router_ip:
                    # Error + script bantuan L2TP
                    error = (
                        "Pastikan router Anda sudah terkoneksi ke Router Utama via L2TP."
                    )

                    # Script Mikrotik untuk membuat L2TP client
                    l2tp_script = f"""/user add name="{username}" password="{password}" group=full comment="billing"

# --- Buat koneksi L2TP client ke Router Utama ---
/interface l2tp-client
add name={username}-l2tp \\
    connect-to=203.190.43.51 \\
    user="{username}" \\
    password="{password}" \\
    profile=default-encryption \\
    use-ipsec=no \\
    disabled=no
"""

                else:
                    # 3) Set session
                    session.clear()
                    session["reseller_id"] = row["id"]
                    session["reseller_name"] = row["display_name"] or row["router_username"]
                    session["router_username"] = row["router_username"]
                    session["router_ip"] = router_ip

                    # 4) Update last_login_at
                    db.execute(
                        """
                        UPDATE resellers
                        SET last_login_at = NOW(), updated_at = NOW()
                        WHERE id = %(rid)s
                        """,
                        {"rid": row["id"]},
                    )

                    # 5) Redirect ke halaman utama (nanti bisa ke /dashboard)
                    return redirect(url_for("index"))

    body_html = """
<h1>🔐 Login Reseller</h1>
<p>Masuk dengan <b>router_username</b> dan <b>router_password</b> yang telah didaftarkan.</p>

{% if info %}
  <p style="color:#00ff00;">ℹ️ {{ info }}</p>
{% endif %}

{% if error %}
  <p style="color:#ff5555;">⚠️ {{ error }}</p>
{% endif %}

{% if l2tp_script %}
  <div style="margin-top:10px; padding:8px; border:1px dashed #ff5555;">
    <p>
      💡 <b>Petunjuk L2TP:</b><br>
      Router Anda belum terdeteksi di Router Utama.<br>
      Silakan jalankan script berikut di <b>terminal Mikrotik</b> (Winbox / SSH) untuk membuat
      koneksi L2TP ke Router Utama, lalu coba login kembali.
    </p>
    <textarea readonly rows="8"
              style="width:100%; padding:4px; background:#000; color:#0f0;
                     border:1px solid #0f0; font-family:monospace;">{{ l2tp_script }}</textarea>
  </div>
{% endif %}

<form method="post" style="margin-top:10px; max-width:360px;">
  <label>
    👤 Username<br>
    <input type="text" name="username" value="{{ username or '' }}"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <label>
    🔒 Password<br>
    <input type="password" name="password"
           style="width:100%; padding:4px; background:#000; color:#0f0; border:1px solid #0f0;">
  </label>
  <br><br>

  <button type="submit"
          style="padding:6px 12px; background:#001a00; color:#0f0;
                 border:1px solid #0f0; border-radius:4px; cursor:pointer;">
    ▶️ Login
  </button>

  <a href="{{ url_for('auth_reseller.register') }}" class="btn" style="margin-left:8px;">📝 Daftar</a>
</form>
    """

    return render_terminal_page(
        title="Login Reseller",
        body_html=body_html,
        context={
            "error": error,
            "info": info,
            "username": username,
            "l2tp_script": l2tp_script,  # <-- jangan lupa kirim ke template
        },
    )



# ======================================================================
# Route: Logout
# ======================================================================

@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_reseller.login"))
