# cron_jobs/isolate_unpaid_users.py
import db

# sesuaikan dengan modul tempat fungsi2 ini berada
from mikrotik_client import update_ppp_secret, terminate_ppp_active_by_name, MikrotikError
from blueprints.auth_reseller import _get_router_ip_for_reseller  # <-- ganti nama modulnya sesuai project-mu


def isolate_unpaid_users():
    # ambil data reseller yang diperlukan
    resellers = db.query_all("""
        SELECT id, display_name, username, router_username, router_password
        FROM resellers
        WHERE is_active = TRUE
    """)

    for r in resellers:
        rid = r["id"]
        name = r["display_name"]

        # username ini diasumsikan sebagai PPP name di Router Admin (L2TP)
        reseller_ppp_name = r["username"]

        api_user = r.get("router_username")
        api_pass = r.get("router_password")

        if not api_user or not api_pass:
            print(f"⚠️ Reseller {name}: router_username/password kosong, skip.")
            continue

        # === 1. Ambil router_ip dari Router Admin ===
        router_ip = _get_router_ip_for_reseller(reseller_ppp_name)
        if not router_ip:
            print(f"⚠️ Reseller {name}: tidak dapat router_ip dari Router Admin, skip.")
            continue

        # === 2. Ambil profile isolasi (id + name) ===
        iso_prof = db.query_one("""
            SELECT id, name
            FROM ppp_profiles
            WHERE reseller_id = %(rid)s
              AND is_isolation = TRUE
            ORDER BY id
            LIMIT 1
        """, {"rid": rid})

        if not iso_prof:
            print(f"⚠️ {name} belum punya profile isolasi, skip.")
            continue

        iso_id = iso_prof["id"]
        iso_name = iso_prof["name"]

        # === 3. Ambil daftar customer yang unpaid + username PPP ===
        unpaid = db.query_all("""
            SELECT v.customer_id, c.ppp_username
            FROM v_unpaid_customers_current_period v
            JOIN ppp_customers c ON c.id = v.customer_id
            WHERE v.reseller_id = %(rid)s
        """, {"rid": rid})

        if not unpaid:
            continue

        print(f"Reseller {name}: isolir {len(unpaid)} pelanggan (router_ip={router_ip}).")

        # === 4. Proses setiap customer ===
        for u in unpaid:
            cid = u["customer_id"]
            username = u["ppp_username"]

            # 4a. Update DB
            try:
                db.execute("""
                    UPDATE ppp_customers
                    SET profile_id = %(pid)s,
                        is_isolated = TRUE,
                        updated_at = NOW()
                    WHERE id = %(cid)s
                """, {"pid": iso_id, "cid": cid})
            except Exception as e:
                print(f"❌ Gagal update DB isolasi id={cid} ({username}): {e}")
                continue

            # 4b. Update profile di MikroTik
            try:
                update_ppp_secret(
                    router_ip,
                    api_user,
                    api_pass,
                    secret_name=username,
                    updates={"profile": iso_name},
                )
            except MikrotikError as e:
                print(
                    f"⚠️ DB sudah isolate, tapi gagal ganti profile di router "
                    f"untuk {username} (reseller {name}): {e}"
                )
                continue
            except Exception as e:
                print(
                    f"⚠️ DB sudah isolate, tapi error lain saat ganti profile di router "
                    f"untuk {username} (reseller {name}): {e}"
                )
                continue

            # 4c. Kill session aktif PPP agar reconnect dengan profile isolasi
            try:
                terminate_ppp_active_by_name(router_ip, api_user, api_pass, username)
            except Exception as e:
                print(f"ℹ️ Gagal terminate session {username}: {e}")

            print(f"✅ {name}: user {username} di-isolate (profile '{iso_name}')")

if __name__ == "__main__":
    isolate_unpaid_users()
