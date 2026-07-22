from __future__ import annotations

import os


os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-32-chars-long!!!!!")

import mobiliti_saas.api.index as api_index


def test_catalog_reservation_accepts_700_unique_lines_above_old_limit(monkeypatch):
    lines = [
        {
            "internal_id": f"alma:large:{position + 1}",
            "sku": f"LARGE-{position + 1}",
            "quantity": "1.000000",
            "stock": "5.000000",
        }
        for position in range(700)
    ]
    captured = {}

    def fake_supabase_request(method, path, params=None, json_data=None):
        captured.update(
            method=method,
            path=path,
            params=params,
            json_data=json_data,
        )
        return [
            {
                "internal_id": line["internal_id"],
                "reserved_before": "0.000000",
                "available_before": "5.000000",
                "insufficient": False,
                "reserved_by_others": False,
            }
            for line in json_data["p_lines"]
        ]

    monkeypatch.setattr(api_index, "DEV_MODE", False)
    monkeypatch.setattr(api_index, "_require_catalog_service_backend", lambda: None)
    monkeypatch.setattr(api_index, "_use_postgres", lambda: False)
    monkeypatch.setattr(api_index, "_supabase_req", fake_supabase_request)

    snapshot = api_index.db_reserve_catalog_items(
        7,
        "11111111-1111-4111-8111-111111111111",
        "alma",
        lines,
    )

    assert len(snapshot) == 700
    assert captured["path"] == "/rpc/saas_reserve_catalog_items"
    assert len(captured["json_data"]["p_lines"]) == 700
