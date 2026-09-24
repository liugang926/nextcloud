#!/usr/bin/env python3
"""Check that source-side diagnostics are useful only to a Nextcloud admin."""

import json
import os
import secrets
import urllib.request

from publication_http_smoke import check, load_env, login, request, run_occ


def main():
    values = load_env()
    base = f"http://127.0.0.1:{values.get('NEXTCLOUD_HTTP_PORT', '18082')}"
    url = f"{base}/index.php/apps/integration_weknora/api/v1/admin/diagnostics"

    anonymous = urllib.request.build_opener()
    status, _ = request(anonymous, url)
    check(status, 401, "anonymous diagnostics")
    status, _ = request(anonymous, url,
                        headers={"Authorization": f"Bearer {values['WEKNORA_SERVICE_TOKEN']}"})
    check(status, 401, "machine token cannot read diagnostics")

    admin, csrf = login(base, values["NEXTCLOUD_ADMIN_USER"], values["NEXTCLOUD_ADMIN_PASSWORD"])
    with admin.open(f"{base}/index.php/settings/admin/integration_weknora") as page:
        html = page.read().decode("utf-8")
        assert page.status == 200 and "Source diagnostics" in html
        assert "data-diagnostics-url" in html
    status, body = request(admin, url, headers={"requesttoken": csrf})
    check(status, 200, "administrator diagnostics")
    data = json.loads(body)
    assert data["binding_count"] >= 1, data
    assert data["binding_roots_available"] is True, data
    assert data["retained_change_hints"] >= 0, data
    assert data["explicit_withdrawal_count"] >= 0, data
    assert isinstance(data["consumer_acknowledgement_available"], bool), data
    assert isinstance(data["checked_at"], int) and data["checked_at"] > 0, data
    assert isinstance(data["outbox_pending_delivery_hints"], int), data
    assert data["outbox_pending_delivery_hints"] >= 0, data
    assert isinstance(data["event_connections"], list), data
    assert sum(row["outbox_pending_delivery_hints"] for row in data["event_connections"]) == \
        data["outbox_pending_delivery_hints"], data
    ages = [row["oldest_outbox_pending_delivery_age_seconds"]
            for row in data["event_connections"]
            if row["oldest_outbox_pending_delivery_age_seconds"] is not None]
    if data["outbox_pending_delivery_hints"]:
        assert data["oldest_outbox_pending_delivery_age_seconds"] == max(ages), data
    else:
        assert data["oldest_outbox_pending_delivery_age_seconds"] is None, data
    safe_fields = {"binding_id", "status", "received_through_event_id",
                   "applied_through_event_id", "applied_checked_at", "applied_error_code",
                   "attempt_count", "next_attempt_at", "last_error_code",
                   "outbox_pending_delivery_hints",
                   "oldest_outbox_pending_delivery_age_seconds"}
    for row in data["event_connections"]:
        assert set(row) == safe_fields, row
        assert row["received_through_event_id"].isdigit(), row
        assert row["applied_through_event_id"].isdigit(), row
        if row["outbox_pending_delivery_hints"]:
            assert row["oldest_outbox_pending_delivery_age_seconds"] >= 0, row
        else:
            assert row["oldest_outbox_pending_delivery_age_seconds"] is None, row
    if data["retained_change_hints"]:
        assert data["newest_change_hint_id"] > 0, data
        assert data["oldest_retained_hint_age_seconds"] >= 0, data

    guest_uid = f"weknora_diagnostics_{secrets.token_hex(5)}"
    guest_password = secrets.token_urlsafe(24)
    created = False
    try:
        run_occ("user:add", "--password-from-env", "--no-interaction", guest_uid,
                env=dict(os.environ, NC_PASS=guest_password))
        created = True
        guest, guest_csrf = login(base, guest_uid, guest_password)
        status, _ = request(guest, url, headers={"requesttoken": guest_csrf})
        check(status, 403, "non-admin diagnostics")
    finally:
        if created:
            run_occ("user:delete", guest_uid)
    print("administrator diagnostics HTTP smoke passed")


if __name__ == "__main__":
    main()
