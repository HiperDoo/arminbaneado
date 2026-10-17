"""IP real, identificadores anonimizados y limites de frecuencia."""

import hashlib
import hmac
import ipaddress
import time
from collections import deque

from fastapi import Request


# Redes que solo usan los proxies del hosting, nunca un visitante real.
_INTERNAL_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10",
)]


def _is_internal(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in _INTERNAL_NETS)


def client_ip(request: Request, trusted_hops: int = 1) -> str:
    """IP real del visitante detrás de los proxies del hosting.

    Cada proxy AÑADE a la derecha de X-Forwarded-For la IP que le habla. Se recorre la
    cadena de derecha a izquierda saltando las direcciones internas del hosting (alwaysdata
    usa varias, p. ej. fd00::/8) y se toma la primera externa: esa la añadió un proxy propio.
    Lo que el cliente escriba más a la izquierda se ignora, porque lo puede falsificar.
    `trusted_hops` se conserva por compatibilidad con la configuración anterior.
    """
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if request.client and request.client.host:
        chain.append(request.client.host)
    for raw in reversed(chain):
        try:
            addr = ipaddress.ip_address(raw.strip("[]"))
        except ValueError:
            continue
        if not _is_internal(addr):
            return str(addr.ipv4_mapped or addr) if isinstance(addr, ipaddress.IPv6Address) else str(addr)
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
