"""Cloudflare Turnstile: nonce de un solo uso + verificacion en el servidor.

El nonce se pasa al widget como cData. Cloudflare lo devuelve firmado dentro del
resultado de siteverify, asi que un token solo vale para el nonce con el que se emitio.
cData solo admite [A-Za-z0-9_-], por eso el formato es hex_decimal_hex.
"""

import hashlib
import hmac
import secrets
import time
import uuid

import httpx

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
NONCE_TTL = 300
TOKEN_MAX_AGE = 300


class TurnstileError(Exception):
    """Verificacion fallida. El mensaje es apto para mostrar al usuario."""


def issue_nonce(secret: str, now: int | None = None) -> tuple[str, int]:
    exp = int(now or time.time()) + NONCE_TTL
    rand = secrets.token_hex(8)
    sig = hmac.new(secret.encode(), f"{rand}_{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{rand}_{exp}_{sig}", exp


def check_nonce(nonce: str, secret: str, now: int | None = None) -> int:
    """Valida formato, firma y caducidad. Devuelve la expiracion."""
    parts = nonce.split("_")
    if len(parts) != 3 or not parts[1].isdigit():
        raise TurnstileError("La verificación no es válida. Recarga la página.")
    rand, exp_s, sig = parts
    expected = hmac.new(secret.encode(), f"{rand}_{exp_s}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise TurnstileError("La verificación no es válida. Recarga la página.")
    exp = int(exp_s)
    if exp < int(now or time.time()):
        raise TurnstileError("La verificación caducó. Marca otra vez la casilla.")
    return exp


async def siteverify(client: httpx.AsyncClient, *, secret: str, token: str, remote_ip: str) -> dict:
    try:
        resp = await client.post(
            SITEVERIFY_URL,
            data={"secret": secret, "response": token, "remoteip": remote_ip, "idempotency_key": str(uuid.uuid4())},
            timeout=10,
        )
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TurnstileError("No pudimos verificar que eres humano. Inténtalo de nuevo.") from exc


def check_result(result: dict, *, nonce: str, hostnames: list[str], action: str, relaxed: bool, now: float | None = None):
    if not result.get("success"):
        raise TurnstileError("No pudimos verificar que eres humano. Inténtalo de nuevo.")
    if relaxed:
        return
    if result.get("hostname") not in hostnames:
        raise TurnstileError("La verificación no corresponde a este sitio.")
    if result.get("action") != action:
        raise TurnstileError("La verificación no corresponde a esta acción.")
    if result.get("cdata") != nonce:
        raise TurnstileError("La verificación no corresponde a esta firma. Marca otra vez la casilla.")
    ts = result.get("challenge_ts")
    if ts:
        from datetime import datetime
        try:
            issued = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            issued = 0
        if (now or time.time()) - issued > TOKEN_MAX_AGE:
            raise TurnstileError("La verificación caducó. Marca otra vez la casilla.")
