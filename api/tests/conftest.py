import math
import random
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from config import Settings  # noqa: E402


def human_strokes(seed: int = 1, shift: float = 0.0) -> list:
    """Una firma sintética con la irregularidad de una mano: velocidad e intervalos variables."""
    rng = random.Random(seed)
    strokes, t = [], 0.0
    for s in range(3):
        stroke = []
        for i in range(40):
            a = i / 39 * math.pi * 2
            x = 0.15 + shift + s * 0.22 + 0.1 * math.cos(a * (1 + s * 0.3)) + i * 0.003
            y = 0.5 + 0.25 * math.sin(a * 1.5 + s) * (0.6 + rng.random() * 0.4)
            t += rng.uniform(8, 26)
            stroke.append([round(min(max(x, 0), 1), 4), round(min(max(y, 0), 1), 4), round(t), round(rng.uniform(0.3, 0.8), 2)])
        t += rng.uniform(120, 300)
        strokes.append(stroke)
    return strokes


class FakeTurnstile:
    """Sustituye la llamada a Cloudflare. Por defecto responde como un token válido."""

    def __init__(self):
        self.result = None  # None = válido y coherente con el nonce

    async def __call__(self, client, *, secret, token, remote_ip):
        if self.result is not None:
            return self.result
        return {"success": True, "hostname": "arminbaneado.com", "action": "firmar",
                "cdata": token.removeprefix("ok:"), "challenge_ts": None}


@pytest.fixture
def settings(tmp_path):
    return Settings(env="test", db_path=tmp_path / "t.db", turnstile_secret="real-secret",
                    ip_salt="s", nonce_secret="n", trusted_proxy_hops=1, legacy_offset=100,
                    milestones=[{"count": 150, "label": "x"}])


@pytest.fixture
def fake_turnstile():
    return FakeTurnstile()


@pytest.fixture
def client(settings, fake_turnstile):
    app = main.create_app(settings)
    app.state.verify_turnstile = fake_turnstile
    with TestClient(app) as c:
        yield c


def sign(client, *, ip="203.0.113.7", device="device-0001", strokes=None, public=False, token_override=None):
    nonce = client.get("/api/desafio", headers={"x-forwarded-for": ip}).json()["nonce"]
    body = {
        "strokes": strokes or human_strokes(),
        # El token fake lleva el nonce para que FakeTurnstile lo devuelva como cdata.
        "turnstile_token": token_override or f"ok:{nonce}",
        "nonce": nonce,
        "device_id": device,
        "public_wall": public,
    }
    return client.post("/api/firmar", json=body, headers={"x-forwarded-for": f"1.1.1.1, {ip}"})
