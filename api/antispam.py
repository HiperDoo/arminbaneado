"""IP real, identificadores anonimizados y limites de frecuencia."""

import hashlib
import hmac
import ipaddress
import time
from collections import deque

from fastapi import Request


def client_ip(request: Request, trusted_hops: int) -> str:
    """El proxy de confianza ANADE la IP que ve al final de X-Forwarded-For.
    Lo que el cliente escriba al principio se ignora, porque lo puede falsificar."""
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if trusted_hops > 0 and len(forwarded) >= trusted_hops:
        candidate = forwarded[-trusted_hops]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def network_of(ip: str) -> str:
    """/24 en IPv4, /64 en IPv6: agrupa redes compartidas (CGNAT, universidades)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def anon(value: str, salt: str) -> str:
    """Nunca se guarda la IP en claro: solo un HMAC que no se puede revertir sin el salt."""
    return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


class RateLimiter:
    """Ventana deslizante en memoria. Se reinicia con la app, y esta bien:
    los limites diarios importantes se comprueban contra la base de datos."""

    def __init__(self):
        self._hits: dict[str, deque] = {}
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: float) -> float:
        """Registra un intento. Devuelve 0 si se permite, o los segundos que faltan si no."""
        now = time.monotonic()
        self._sweep(now)
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return max(q[0] + window - now, 1)
        q.append(now)
        return 0

    def _sweep(self, now: float):
        if now - self._last_sweep < 300:
            return
        self._last_sweep = now
        for key in [k for k, q in self._hits.items() if not q or q[-1] < now - 3600]:
            del self._hits[key]
