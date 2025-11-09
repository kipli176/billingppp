"""
wa_client.py
------------
Client sederhana untuk kirim WhatsApp melalui API yang sudah kamu sediakan.

URL default diambil dari app.config["WA_API_URL"] (di Config).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests
from flask import current_app


class WhatsAppError(Exception):
    """Kesalahan waktu mengirim WA."""
    pass


def send_wa(
    number: str,
    message: str,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Kirim pesan WA ke satu nomor.

    - number  : nomor WA, misal "62856xxxxx"
    - message : isi pesan teks
    - extra_payload : dict tambahan kalau API-mu butuh field lain

    Mengembalikan dict hasil response (kalau JSON), atau raise WhatsAppError kalau gagal.
    """
    api_url = current_app.config.get("WA_API_URL")
    if not api_url:
        raise WhatsAppError("WA_API_URL belum dikonfigurasi di Config/app.config.")

    payload: Dict[str, Any] = {
        "number": number,
        "message": message,
    }
    if extra_payload:
        payload.update(extra_payload)

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
    except Exception as e:
        raise WhatsAppError(f"Gagal menghubungi WA API: {e}") from e

    if not resp.ok:
        raise WhatsAppError(f"WA API error HTTP {resp.status_code}: {resp.text}")

    try:
        return resp.json()
    except ValueError:
        # Kalau bukan JSON, tetap kembalikan text mentah.
        return {"raw": resp.text}
