"""
wa_client.py
------------
Client sederhana untuk kirim WhatsApp melalui API yang sudah kamu sediakan.

- Jika dipanggil dari dalam Flask app:
    pakai current_app.config["WA_API_URL"]
- Jika dipanggil dari cron / script biasa (tanpa Flask context):
    pakai Config.WA_API_URL yang diambil dari .env / environment.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests
from flask import current_app, has_app_context

from config import Config


class WhatsAppError(Exception):
    """Kesalahan saat mengirim pesan WhatsApp."""


def _get_api_url(override_url: Optional[str] = None) -> str:
    """
    Tentukan URL API WhatsApp yang akan dipakai.

    Prioritas:
    1. override_url (kalau dikirim sebagai argumen)
    2. current_app.config["WA_API_URL"] (jika ada Flask app context)
    3. Config.WA_API_URL (dibaca dari environment/.env)
    """
    if override_url:
        return override_url

    url: Optional[str] = None

    # Kalau lagi di dalam konteks Flask, utamakan config dari app
    if has_app_context():
        url = current_app.config.get("WA_API_URL")

    # Fallback ke konfigurasi global (dari .env)
    if not url:
        url = getattr(Config, "WA_API_URL", None)

    if not url:
        raise WhatsAppError(
            "WA_API_URL belum di-set. "
            "Pastikan ada di environment atau file .env."
        )

    return url


def send_wa(
    to: str,
    message: str,
    *,
    api_url: Optional[str] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Kirim pesan WhatsApp.

    :param to: nomor tujuan (string, misal: '6281234567890')
    :param message: isi pesan
    :param api_url: override URL API (opsional)
    :param extra_payload: dict tambahan untuk payload (opsional)
    :return: dict respon dari server (atau {"raw": "<text>"} kalau bukan JSON)
    """
    if not to:
        raise ValueError("Nomor tujuan (to) wajib diisi")
    if not message:
        raise ValueError("Pesan WhatsApp (message) wajib diisi")

    url = _get_api_url(override_url=api_url)

    payload: Dict[str, Any] = {
        "number": to,
        "message": message,
    }

    if extra_payload:
        payload.update(extra_payload)

    try:
        resp = requests.post(url, json=payload, timeout=10)
    except Exception as e:
        raise WhatsAppError(f"Gagal menghubungi WA API: {e}") from e

    if not resp.ok:
        raise WhatsAppError(f"WA API error HTTP {resp.status_code}: {resp.text}")

    try:
        return resp.json()
    except ValueError:
        # Kalau bukan JSON, tetap kembalikan text mentah.
        return {"raw": resp.text}
