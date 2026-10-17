import json
import time

import httpx
import pytest

import main
import turnstile
from conftest import human_strokes, sign


def count(client):
    return client.get("/api/contador").json()["count"]


def test_counter_includes_legacy_offset_and_milestones(client):
    body = client.get("/api/contador").json()
    assert body["count"] == 100
    assert body["milestones"] == [{"count": 150, "label": "x"}]


def test_valid_signature_counts(client):
    r = sign(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "accepted", "count": 101, "signature_no": 101}
    assert count(client) == 101


def test_signature_png_is_queued_for_webhook(client):
    sign(client)
    rows = client.app.state.db.pending_webhook("accepted")
    assert len(rows) == 1 and rows[0]["png"].startswith(b"\x89PNG")


@pytest.mark.parametrize("result,msg", [
    ({"success": False}, "humano"),
    ({"success": True, "hostname": "evil.com", "action": "firmar"}, "sitio"),
    ({"success": True, "hostname": "arminbaneado.com", "action": "otra"}, "acción"),
    ({"success": True, "hostname": "arminbaneado.com", "action": "firmar", "cdata": "otro"}, "esta firma"),
])
def test_turnstile_failures_are_403_and_do_not_count(client, fake_turnstile, result, msg):
    fake_turnstile.result = result
    r = sign(client)
    assert r.status_code == 403 and msg in r.json()["detail"]
    assert count(client) == 100


def test_old_token_rejected(client, fake_turnstile):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    fake_turnstile.result = {"success": True, "hostname": "arminbaneado.com", "action": "firmar", "challenge_ts": old}
    # cdata se rellena en el fake solo si result es None, así que fijamos uno coherente:
    nonce = client.get("/api/desafio").json()["nonce"]
    fake_turnstile.result["cdata"] = nonce
    r = client.post("/api/firmar", json={"strokes": human_strokes(), "turnstile_token": "t", "nonce": nonce,
                                         "device_id": "device-0001"})
    assert r.status_code == 403 and "caducó" in r.json()["detail"]


def test_nonce_cannot_be_reused(client):
    nonce = client.get("/api/desafio").json()["nonce"]
    body = {"strokes": human_strokes(), "turnstile_token": f"ok:{nonce}", "nonce": nonce, "device_id": "device-0001"}
    assert client.post("/api/firmar", json=body).status_code == 200
    body["device_id"] = "device-0002"
    r = client.post("/api/firmar", json=body)
    assert r.status_code == 403 and "ya se usó" in r.json()["detail"]


def test_forged_and_expired_nonce(client):
    r = client.post("/api/firmar", json={"strokes": human_strokes(), "turnstile_token": "ok:x",
                                         "nonce": "abcd_9999999999_deadbeef", "device_id": "device-0001"})
    assert r.status_code == 403
    nonce, _ = turnstile.issue_nonce("n", now=int(time.time()) - 1000)
    r = client.post("/api/firmar", json={"strokes": human_strokes(), "turnstile_token": f"ok:{nonce}",
                                         "nonce": nonce, "device_id": "device-0001"})
    assert r.status_code == 403 and "caducó" in r.json()["detail"]


def test_old_api_payload_rejected(client):
    r = client.post("/api/firmar", json={"firma": "data:image/png;base64,iVBORw0KGgo="})
    assert r.status_code == 422
    assert count(client) == 100


def test_body_too_large(client):
    r = client.post("/api/firmar", content=b"{" + b" " * 70000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_invalid_strokes_do_not_burn_nonce(client):
    nonce = client.get("/api/desafio").json()["nonce"]
    bad = {"strokes": [[[0.1, 0.1, 0, 0.5]] * 25], "turnstile_token": f"ok:{nonce}", "nonce": nonce, "device_id": "device-0001"}
    assert client.post("/api/firmar", json=bad).status_code == 422
    good = dict(bad, strokes=human_strokes())
    assert client.post("/api/firmar", json=good).status_code == 200


def test_device_limit(client):
    assert sign(client, ip="203.0.113.1").status_code == 200
    client.app.state.limiter = type(client.app.state.limiter)()  # saltar la ventana de 30 s
    r = sign(client, ip="203.0.113.2", strokes=human_strokes(seed=9, shift=0.05))
    assert r.status_code == 429 and "dispositivo" in r.json()["detail"]


def test_burst_limit_after_verification(client):
    assert sign(client, device="device-a001").status_code == 200
    r = sign(client, device="device-a002", strokes=human_strokes(seed=7, shift=0.05))
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_ip_daily_limit(client):
    for i in range(3):
        client.app.state.limiter = type(client.app.state.limiter)()
        assert sign(client, device=f"device-b{i:03}", strokes=human_strokes(seed=20 + i * 7, shift=i * 0.08)).status_code in (200, 202)
    client.app.state.limiter = type(client.app.state.limiter)()
    r = sign(client, device="device-b999", strokes=human_strokes(seed=99))
    assert r.status_code == 429 and "conexión" in r.json()["detail"]


def test_near_duplicate_goes_to_review_without_counting(client):
    assert sign(client, ip="203.0.113.5", device="device-c001").status_code == 200
    client.app.state.limiter = type(client.app.state.limiter)()
    r = sign(client, ip="203.0.113.6", device="device-c002", strokes=human_strokes(seed=1, shift=0.003))
    assert r.status_code == 202 and r.json()["status"] == "review"
    assert count(client) == 101


def test_spoofed_forwarded_for_is_ignored(client):
    """El cliente pone IPs falsas al principio; solo cuenta la que añade el proxy (la última)."""
    assert sign(client, ip="198.51.100.1", device="device-d001").status_code == 200
    nonce = client.get("/api/desafio").json()["nonce"]
    body = {"strokes": human_strokes(seed=33, shift=0.05), "turnstile_token": f"ok:{nonce}", "nonce": nonce, "device_id": "device-d002"}
    r = client.post("/api/firmar", json=body, headers={"x-forwarded-for": "9.9.9.9, 198.51.100.1"})
    assert r.status_code == 429  # misma IP real: ráfaga


def test_public_wall_only_shows_opt_in(client):
    sign(client, ip="203.0.113.10", device="device-e001", public=True)
    client.app.state.limiter = type(client.app.state.limiter)()
    sign(client, ip="203.0.114.10", device="device-e002", strokes=human_strokes(seed=44, shift=0.06))
    items = client.get("/api/muro").json()["items"]
    assert len(items) == 1 and len(items[0]["strokes"]) == 3


def test_banned_network(client):
    import antispam
    net = antispam.anon(antispam.network_of("203.0.113.50"), "s")
    client.app.state.db.ban_net(net)
    r = sign(client, ip="203.0.113.50")
    assert r.status_code == 403


def test_profile_unavailable_without_bot(client):
    assert client.get("/api/perfil/armin").status_code == 503


def test_cors_only_allows_site(client):
    ok = client.options("/api/contador", headers={"Origin": "https://arminbaneado.com", "Access-Control-Request-Method": "GET"})
    bad = client.options("/api/contador", headers={"Origin": "https://evil.com", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://arminbaneado.com"
    assert "access-control-allow-origin" not in bad.headers


# ---------- perfil de Discord con API mockeada ----------

def discord_transport(fail=False):
    def handler(request: httpx.Request):
        if fail:
            return httpx.Response(500)
        path = request.url.path
        if path.endswith("/roles"):
            return httpx.Response(200, json=[
                {"id": "1", "name": "@everyone", "color": 0, "position": 0},
                {"id": "10", "name": "Moderador", "color": 0x3498DB, "position": 5, "icon": "ic"},
                {"id": "11", "name": "Miembro", "color": 0, "position": 1, "unicode_emoji": "🍁"},
            ])
        if "/members/" in path:
            return httpx.Response(200, json={"nick": None, "roles": ["11", "10"], "joined_at": "2024-03-02T10:00:00+00:00", "avatar": None})
        if "/users/" in path:
            return httpx.Response(200, json={"id": "123", "username": "armin", "global_name": "Armin", "avatar": "abc",
                                             "banner": None, "accent_color": 0xDA373C, "banner_color": "#da373c"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_profile_normalized_and_cached():
    from discord_profile import ProfileService
    async with httpx.AsyncClient(transport=discord_transport()) as c:
        svc = ProfileService(c, bot_token="t", guild_id="1", profiles={"armin": "123"})
        data = await svc.get("armin")
        assert data["display_name"] == "Armin" and data["username"] == "armin"
        assert data["avatar_url"] == "https://cdn.discordapp.com/avatars/123/abc.png?size=256"
        assert data["roles"] == [{"name": "Moderador", "color": "#3498db", "icon": "https://cdn.discordapp.com/role-icons/10/ic.png?size=32", "emoji": None},
                                 {"name": "Miembro", "color": None, "icon": None, "emoji": "🍁"}]
        assert data["banner_color"] == "#da373c"
        assert set(data) == {"display_name", "username", "avatar_url", "avatar_decoration_url", "banner_url",
                             "banner_color", "roles", "member_since", "discord_since", "status", "bio"}
        # Discord se cae: devuelve la última respuesta buena
        svc.client = httpx.AsyncClient(transport=discord_transport(fail=True))
        svc._cache["armin"] = (0, data)
        assert await svc.get("armin") == data


@pytest.mark.anyio
async def test_profile_down_without_cache():
    from discord_profile import ProfileService, ProfileUnavailable
    async with httpx.AsyncClient(transport=discord_transport(fail=True)) as c:
        svc = ProfileService(c, bot_token="t", guild_id="1", profiles={"armin": "123"})
        with pytest.raises(ProfileUnavailable):
            await svc.get("armin")


# ---------- webhook ----------

@pytest.mark.anyio
async def test_webhook_batches_and_respects_429(tmp_path):
    from db import Database
    from webhook import WebhookWorker
    db = Database(tmp_path / "w.db")
    for i in range(12):
        db.insert_signature(ip_hash=f"i{i}", net_hash="n", device_hash=f"d{i}", phash="0" * 16, stroke_stats="{}",
                            strokes_json=None, public_wall=False, status="accepted", png=b"\x89PNG")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 1.5})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        w = WebhookWorker(db, c, main_url="https://discord.test/hook")
        assert await w.flush("accepted", "https://discord.test/hook", "x") == 2.0
        assert len(db.pending_webhook("accepted", 50)) == 12
        await w.flush("accepted", "https://discord.test/hook", "x")
        assert len(db.pending_webhook("accepted", 50)) == 2
        body = calls[1].content
        assert b"payload_json" in body and body.count(b"image/png") == 10
        await w.flush("accepted", "https://discord.test/hook", "x")
        assert db.pending_webhook("accepted", 50) == []


# ---------- reacciones 🔥 ----------

def react(client, mid="m1", device="device-r001", on=True, ip="203.0.113.70"):
    return client.post("/api/reacciones", json={"message_id": mid, "device_id": device, "on": on},
                       headers={"x-forwarded-for": ip})


def test_reaction_toggle_and_counts(client):
    assert react(client).json() == {"message_id": "m1", "on": True, "count": 1}
    assert react(client).json()["count"] == 1  # mismo dispositivo: no duplica
    assert react(client, device="device-r002").json()["count"] == 2
    assert client.get("/api/reacciones").json() == {"emoji": "🔥", "counts": {"m1": 2}}
    assert react(client, on=False).json()["count"] == 1


def test_reaction_invalid_message_id(client):
    assert react(client, mid="../etc").status_code == 422
    assert react(client, mid="m12345").status_code == 422


def test_reaction_cap_per_ip_and_message(client):
    for i in range(5):
        assert react(client, device=f"device-s{i:03}").status_code == 200
    r = react(client, device="device-s999")
    assert r.status_code == 429
    # otra conexión sí puede
    assert react(client, device="device-s999", ip="198.51.100.9").status_code == 200
    # quitar la propia siempre se puede
    assert react(client, device="device-s000", on=False).status_code == 200


def test_embed_json_has_live_count_and_valid_buttons(client):
    sign(client)
    body = client.get("/api/embed.json").json()
    comp = body["component"]
    assert comp["type"] == 17
    assert "**101** personas ya han firmado" in comp["components"][2]["content"]
    button = comp["components"][3]["components"][0]
    assert set(button) <= {"id", "type", "url", "style", "label", "emoji", "disabled"} and button["style"] == 5
    assert len(json.dumps(body, ensure_ascii=False).encode()) < 3000


# ---------- IP real detrás de los proxies del hosting ----------

def _ip_for(headers, client_host="::1"):
    from types import SimpleNamespace
    import antispam
    req = SimpleNamespace(headers=headers, client=SimpleNamespace(host=client_host))
    return antispam.client_ip(req)


def test_client_ip_skips_internal_proxy_hops():
    # alwaysdata: cliente -> proxy interno (fd00::) -> alproxy (::1)
    assert _ip_for({"x-forwarded-for": "203.0.113.9, fd00::7:5f56"}) == "203.0.113.9"
    assert _ip_for({"x-forwarded-for": "203.0.113.9, 10.0.0.5, 172.16.3.4"}) == "203.0.113.9"
    assert _ip_for({"x-forwarded-for": "2001:db8::1"}) == "2001:db8::1"
    assert _ip_for({"x-forwarded-for": "::ffff:198.51.100.4"}) == "198.51.100.4"


def test_client_ip_ignores_spoofed_left_entries():
    assert _ip_for({"x-forwarded-for": "9.9.9.9, 198.51.100.1, fd00::1"}) == "198.51.100.1"


def test_client_ip_without_header_uses_socket():
    assert _ip_for({}, client_host="198.51.100.7") == "198.51.100.7"


def test_ip_endpoint_distinguishes_clients(client):
    a = client.get("/api/ip", headers={"x-forwarded-for": "203.0.113.1, fd00::7:5f56"}).json()["id"]
    b = client.get("/api/ip", headers={"x-forwarded-for": "203.0.113.2, fd00::7:5f56"}).json()["id"]
    assert a != b and len(a) == 8
