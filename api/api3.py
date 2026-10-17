"""Punto de entrada para el servidor:

    uvicorn api3:app --host '::' --port $PORT --proxy-headers --forwarded-allow-ips "::1"

Con --proxy-headers el proxy (::1) entrega la IP real del cliente en X-Forwarded-For,
y TRUSTED_PROXY_HOPS=1 hace que la API la use para los límites.
"""

from main import create_app

app = create_app()
