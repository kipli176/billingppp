"""
mikrotik_client.py
------------------
Fungsi helper untuk call REST API RouterOS (MikroTik).

Semua fungsi di sini mengharapkan:
- router_host      : IP atau host router (tanpa /rest)
- api_user, api_pass : user/password REST di router tersebut

HTTP client pakai requests (basic auth).

Contoh base URL yang dihasilkan:
- http://192.168.88.1/rest/system/resource
- atau kalau pakai use_https=True -> https://192.168.88.1/rest/system/resource
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth


class MikrotikError(Exception):
    """Kesalahan komunikasi dengan Mikrotik REST API."""
    pass


def _build_url(router_host: str, path: str, use_https: bool = False) -> str:
    """
    Build URL lengkap ke REST API.

    router_host: "192.168.88.1" atau "203.190.43.51:81"
    path: "/system/resource" atau "/ppp/secret"
          (boleh diawali "rest/..." atau kita tambahkan "rest" otomatis)
    """
    scheme = "https" if use_https else "http"

    path = path.strip()
    # Pastikan selalu ada "/rest/..." di depan
    if path.startswith("/rest/"):
        final_path = path
    elif path.startswith("rest/"):
        final_path = "/" + path
    elif path.startswith("/"):
        final_path = "/rest" + path
    else:
        final_path = "/rest/" + path

    return f"{scheme}://{router_host}{final_path}"


def _request(
    method: str,
    router_host: str,
    path: str,
    api_user: str,
    api_pass: str,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    use_https: bool = False,
) -> Any:
    """
    Helper umum untuk call REST API RouterOS.

    path: misal "/system/resource" atau "/ppp/secret"
    """
    url = _build_url(router_host, path, use_https=use_https)

    try:
        resp = requests.request(
            method=method.upper(),
            url=url,
            auth=HTTPBasicAuth(api_user, api_pass),
            json=json_body,
            timeout=timeout,
            # untuk HTTP biasa, verify tidak kepakai; untuk HTTPS self-signed, bisa diset False
            verify=False if use_https else True,
        )
    except Exception as e:
        raise MikrotikError(f"Gagal koneksi ke router {router_host}: {e}") from e

    if not resp.ok:
        try:
            data = resp.json()
            msg = data.get("detail") or data
        except Exception:
            msg = resp.text
        raise MikrotikError(f"HTTP {resp.status_code} {url}: {msg}")

    if resp.text.strip() == "":
        return None

    try:
        return resp.json()
    except Exception:
        return resp.text


# -----------------------------------------------------------------------------
# Fungsi publik
# -----------------------------------------------------------------------------

def get_system_resource(
    router_host: str,
    api_user: str,
    api_pass: str,
    use_https: bool = False,
) -> Dict[str, Any]:
    """
    Ambil info resource router:
    - CPU load
    - free memory
    - uptime
    dsb (tergantung versi RouterOS).
    """
    data = _request("GET", router_host, "/system/resource", api_user, api_pass, use_https=use_https)
    return data or {}


def get_system_identity(
    router_host: str,
    api_user: str,
    api_pass: str,
    use_https: bool = False,
) -> Dict[str, Any]:
    """
    Ambil identity/router name.
    """
    data = _request("GET", router_host, "/system/identity", api_user, api_pass, use_https=use_https)
    return data or {}


def get_ppp_profiles(
    router_host: str,
    api_user: str,
    api_pass: str,
    use_https: bool = False,
) -> List[Dict[str, Any]]:
    """
    Ambil daftar PPP profile dari router.
    """
    data = _request("GET", router_host, "/ppp/profile", api_user, api_pass, use_https=use_https)
    if isinstance(data, list):
        return data
    return []


def get_ppp_secrets(
    router_host: str,
    api_user: str,
    api_pass: str,
    use_https: bool = False,
) -> List[Dict[str, Any]]:
    """
    Ambil daftar PPP secret (user PPP) dari router.
    """
    data = _request("GET", router_host, "/ppp/secret", api_user, api_pass, use_https=use_https)
    if isinstance(data, list):
        return data
    return []


def create_ppp_secret(
    router_host: str,
    api_user: str,
    api_pass: str,
    secret_name: str,
    secret_password: str,
    profile: Optional[str] = None,
    use_https: bool = False,
) -> Dict[str, Any]:
    """
    Membuat PPP secret baru di router.
    """
    body: Dict[str, Any] = {
        "name": secret_name,
        "password": secret_password,
    }
    if profile:
        body["profile"] = profile

    data = _request("PUT", router_host, "/ppp/secret", api_user, api_pass, json_body=body, use_https=use_https)
    return data or {}


def update_ppp_secret(
    router_host: str,
    api_user: str,
    api_pass: str,
    secret_name: str,
    updates: Dict[str, Any],
    use_https: bool = False,
) -> Dict[str, Any]:
    """
    Update PPP secret (mis: ganti profile, disable/enable, ganti password).

    Contoh updates:
      {"profile": "PAKET10M"}
      {"disabled": "yes"}
    """
    path = f"/ppp/secret/{secret_name}"
    data = _request("PATCH", router_host, path, api_user, api_pass, json_body=updates, use_https=use_https)
    return data or {}


def delete_ppp_secret(
    router_host: str,
    api_user: str,
    api_pass: str,
    secret_name: str,
    use_https: bool = False,
) -> None:
    """
    Hapus PPP secret dari router.
    """
    path = f"/ppp/secret/{secret_name}"
    _request("DELETE", router_host, path, api_user, api_pass, use_https=use_https)


def get_ppp_active(
    router_host: str,
    api_user: str,
    api_pass: str,
    use_https: bool = False,
) -> List[Dict[str, Any]]:
    """
    Ambil daftar PPP active (session yang sedang online).
    """
    data = _request("GET", router_host, "/ppp/active", api_user, api_pass, use_https=use_https)
    if isinstance(data, list):
        return data
    return []


def terminate_ppp_active_by_name(
    router_host: str,
    api_user: str,
    api_pass: str,
    secret_name: str,
    use_https: bool = False,
) -> bool:
    """
    Terminate PPP session berdasarkan name (ppp_username).

    Alur:
    - Ambil /ppp/active
    - Cari yang name == secret_name
    - DELETE /rest/ppp/active/<.id>

    Return True kalau ada sesi yang di-terminate, False kalau tidak ketemu.
    """
    sessions = get_ppp_active(router_host, api_user, api_pass, use_https=use_https)
    for sess in sessions:
        if sess.get("name") == secret_name:
            active_id = sess.get(".id")
            if not active_id:
                continue
            path = f"/ppp/active/{active_id}"
            _request("DELETE", router_host, path, api_user, api_pass, use_https=use_https)
            return True
    return False
