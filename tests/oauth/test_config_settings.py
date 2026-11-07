import pytest


def test_oauth_defaults_and_ranges(tmp_path):
    from doc4ai.config import ConfigError, load_config
    p = tmp_path / "doc4ai.toml"
    p.write_text("[server]\nport = 1\n", encoding="utf-8")
    o = load_config(p).oauth
    assert (o.enabled, o.allow_cimd, o.allow_dcr) == (True, True, True)
    assert (o.access_ttl_min, o.refresh_ttl_days, o.refresh_max_days, o.code_ttl_s, o.consent_ttl_s,
            o.dcr_per_hour_per_ip) == (60, 30, 90, 300, 600, 20)
    for bad in ("access_ttl_min = 4", "access_ttl_min = 1441", "code_ttl_s = 29", "code_ttl_s = 601", "refresh_ttl_days = 0",
                "refresh_ttl_days = 366", "refresh_max_days = 731", "consent_ttl_s = 59", "consent_ttl_s = 1801",
                "refresh_ttl_days = 60\nrefresh_max_days = 30", "dcr_per_hour_per_ip = 0", "dcr_per_hour_per_ip = 1001",
                'enabled = "yes"', "access_ttl_min = true"):
        p.write_text(f"[oauth]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="oauth\\."):
            load_config(p)
    p.write_text("[oauth]\nenabled = false\nallow_dcr = false\naccess_ttl_min = 5\nrefresh_ttl_days = 1\n"
                 "refresh_max_days = 1\n", encoding="utf-8")
    o = load_config(p).oauth
    assert (o.enabled, o.allow_dcr, o.access_ttl_min, o.refresh_max_days) == (False, False, 5, 1)


def test_settings_api_oauth(client):
    assert client.get("/api/settings").json()["settings"]["oauth"]["access_ttl_min"] == 60
    r = client.put("/api/settings", json={"oauth": {"access_ttl_min": 15, "allow_dcr": False}})
    assert r.status_code == 200, r.text
    s = client.get("/api/settings").json()["settings"]["oauth"]
    assert s["access_ttl_min"] == 15 and s["allow_dcr"] is False
    assert client.put("/api/settings", json={"oauth": {"access_ttl_min": 2}}).status_code == 422
    assert client.put("/api/settings", json={"oauth": {"access_ttl_min": 1441}}).status_code == 422
    assert client.put("/api/settings", json={"oauth": {"refresh_max_days": 10}}).status_code == 422    # < refresh_ttl_days
    assert client.put("/api/settings", json={"oauth": {"refresh_ttl_days": 100}}).status_code == 422   # > refresh_max_days
    assert client.put("/api/settings", json={"oauth": {"refresh_ttl_days": 100, "refresh_max_days": 200}}).status_code == 200
    assert client.put("/api/settings", json={"oauth": {"enabled": "no"}}).status_code == 422
    assert client.put("/api/settings", json={"oauth": {"bogus": 1}}).status_code == 422
