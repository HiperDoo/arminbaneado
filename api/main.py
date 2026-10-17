"""
Caso Armin: API de firmas.

Una firma cuenta solo si:
  1. Pasa Cloudflare Turnstile (token fresco, de este sitio, ligado a un nonce de un solo uso).
  2. Respeta los límites por IP, red y dispositivo.
  3. Sus trazos parecen dibujados por una persona.
  4. No es casi idéntica a otra reciente de la misma red.

Ejecutar: uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import contextlib
import json
import logging
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import antispam
import strokes as strokes_mod
import turnstile
from config import Settings, load_settings
from db import DAY, Database
from discord_profile import ProfileService, ProfileUnavailable
from webhook import WebhookWorker

log = logging.getLogger("armin")

# Límites
BURST_WINDOW = 30            # 1 intento de firma cada 30 s por IP
PER_IP_DAY = 3               # firmas aceptadas por IP en 24 h
PER_NET_DAY = 10             # por red /24 o /64
PER_DEVICE_DAY = 1           # por dispositivo
NEAR_DUP_DISTANCE = 6        # bits de diferencia del dHash
NEAR_DUP_WINDOW = 7 * DAY
GLOBAL_PER_MINUTE = 60       # por encima, las firmas nuevas quedan en revisión
COUNTER_PER_MINUTE = 60
CHALLENGE_PER_MINUTE = 20
PROFILE_PER_MINUTE = 30
REACT_PER_MINUTE = 30        # cambios de reacción por IP
REACT_PER_IP_MESSAGE = 5     # 🔥 activos desde una misma conexión en un mensaje (frena device_id inventados)


class SignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strokes: strokes_mod.Strokes
    turnstile_token: str = Field(min_length=1, max_length=4096)
    nonce: str = Field(min_length=1, max_length=255)
    device_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    public_wall: bool = False


class ReactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(pattern=r"^m[0-9]{1,3}$")
    device_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    on: bool


def embed_payload(count_line: str) -> dict:
    """Contenedor de la vista previa. Botones solo de enlace y sin custom_id (Discord lo rechaza)."""
    return {"component": {"type": 17, "accent_color": 16581379, "components": [
        {"type": 10, "content": "# Firmas para banear a @Armin\n**Motivo:** nose"},
        {"type": 14},
        {"type": 10, "content": f"{count_line}\n**¿Qué esperas?**"},
        {"type": 1, "components": [{"type": 2, "style": 5, "label": "arminbaneado.com", "url": "https://arminbaneado.com/"}]},
        {"type": 10, "content": "-# para el que sea nuevo, esto es joda, te amamos armin"},
    ]}}


def _error(status: int, message: str, headers: dict | None = None):
    raise HTTPException(status_code=status, detail=message, headers=headers)


def create_app(settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or load_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        client = http_client or httpx.AsyncClient()
        app.state.http = client
        app.state.profiles = ProfileService(client, bot_token=settings.discord_bot_token,
                                            guild_id=settings.discord_guild_id, profiles=settings.profiles)
        worker = WebhookWorker(app.state.db, client, main_url=settings.discord_webhook_url,
                               review_url=settings.discord_review_webhook_url)
        tasks = [asyncio.create_task(worker.run()), asyncio.create_task(_purge_nonces(app.state.db))]
        try:
            yield
        finally:
            worker.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if http_client is None:
                await client.aclose()

    app = FastAPI(
        title="Caso Armin API",
        lifespan=lifespan,
        docs_url="/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_dev else None,
    )
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.limiter = antispam.RateLimiter()
    # Se puede sustituir en tests para no llamar a Cloudflare.
    app.state.verify_turnstile = turnstile.siteverify

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )

    def ip_of(request: Request) -> str:
        return antispam.client_ip(request, settings.trusted_proxy_hops)

    def throttle(key: str, max_hits: int, window: float, message: str):
        wait = app.state.limiter.hit(key, max_hits, window)
        if wait:
            _error(429, message, {"Retry-After": str(int(wait))})

    def total_count() -> int:
        db: Database = app.state.db
        offset = db.get_meta("legacy_offset")
        return (int(offset) if offset is not None else settings.legacy_offset) + db.count_accepted()

    # ---------- endpoints ----------

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/debug/ip")
    async def debug_ip(request: Request):
        """TEMPORAL: muestra cómo llegan las cabeceras de IP (todas las apariciones) para
        ajustar la detección detrás del proxy del hosting. Cada uno solo ve lo suyo."""
        throttle(f"ipdebug:{ip_of(request)}", 10, 60, "Demasiadas consultas. Espera un momento.")
        names = {b"x-forwarded-for", b"x-real-ip", b"forwarded", b"via", b"x-client-ip",
                 b"x-forwarded", b"forwarded-for", b"true-client-ip", b"cf-connecting-ip", b"x-cluster-client-ip"}
        seen: dict[str, list[str]] = {}
        for k, v in request.scope.get("headers", []):
            if k.lower() in names:
                seen.setdefault(k.decode("latin-1").lower(), []).append(v.decode("latin-1"))
        client = request.scope.get("client")
        return JSONResponse({"client": client[0] if client else None, "headers": seen, "resolved_id": antispam.anon(ip_of(request), settings.ip_salt)[:8]},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/ip")
    async def mi_ip(request: Request):
        """Diagnóstico: un identificador corto y anónimo de cómo te ve la API.
        Dos personas en conexiones distintas deben ver valores distintos."""
        throttle(f"ipcheck:{ip_of(request)}", COUNTER_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        return JSONResponse({"id": antispam.anon(ip_of(request), settings.ip_salt)[:8]}, headers={"Cache-Control": "no-store"})

    @app.get("/api/contador")
    async def contador(request: Request):
        throttle(f"count:{ip_of(request)}", COUNTER_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        db: Database = app.state.db
        body = {"count": total_count(), "last_signed_at": db.last_signed_at(), "milestones": settings.milestones}
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=15"})

    @app.get("/api/desafio")
    async def desafio(request: Request):
        throttle(f"nonce:{ip_of(request)}", CHALLENGE_PER_MINUTE, 60, "Demasiados intentos. Espera un minuto.")
        nonce, exp = turnstile.issue_nonce(settings.nonce_secret)
        return JSONResponse({"nonce": nonce, "expires_at": exp}, headers={"Cache-Control": "no-store"})

    @app.post("/api/firmar")
    async def firmar(request: Request):
        db: Database = app.state.db

        # 1. Tamaño y forma del cuerpo, antes de gastar nada más.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            _error(413, "La firma es demasiado grande.")
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > settings.max_body_bytes:
                _error(413, "La firma es demasiado grande.")
        try:
            data = SignRequest.model_validate_json(raw)
        except ValidationError:
            _error(422, "La firma no tiene un formato válido. Recarga la página.")

        # ¿La dibujó una persona? Se comprueba antes de gastar el nonce.
        try:
            stats = strokes_mod.validate_human(data.strokes)
        except strokes_mod.InvalidSignature as exc:
            _error(422, str(exc))

        ip = ip_of(request)
        ip_hash = antispam.anon(ip, settings.ip_salt)
        net_hash = antispam.anon(antispam.network_of(ip), settings.ip_salt)
        device_hash = antispam.anon(data.device_id, settings.ip_salt)

        # 2. Intentos por minuto (incluye los que fallan la verificación).
        throttle(f"try:{ip_hash}", 10, 60, "Demasiados intentos. Espera un minuto.")
        if db.is_banned(net_hash):
            _error(403, "No se aceptan firmas desde esta red.")

        # 3. Turnstile: nonce válido y no usado + token verificado por Cloudflare.
        try:
            exp = turnstile.check_nonce(data.nonce, settings.nonce_secret)
            if not db.use_nonce(data.nonce, exp):
                raise turnstile.TurnstileError("Esta verificación ya se usó. Marca otra vez la casilla.")
            result = await app.state.verify_turnstile(app.state.http, secret=settings.turnstile_secret,
                                                      token=data.turnstile_token, remote_ip=ip)
            turnstile.check_result(result, nonce=data.nonce, hostnames=settings.turnstile_hostnames,
                                   action=settings.turnstile_action, relaxed=settings.turnstile_relaxed)
        except turnstile.TurnstileError as exc:
            _error(403, str(exc))

        # 4. Ráfagas de firmas ya verificadas y límites diarios contra la base de datos.
        throttle(f"sign:{ip_hash}", 1, BURST_WINDOW, "Espera unos segundos antes de volver a firmar.")
        since = int(time.time()) - DAY
        if db.count_by("device_hash", device_hash, since) >= PER_DEVICE_DAY:
            _error(429, "Ya firmaste desde este dispositivo. Gracias por tu apoyo.")
        if db.count_by("ip_hash", ip_hash, since) >= PER_IP_DAY:
            _error(429, "Ya se registraron varias firmas desde tu conexión hoy. Vuelve mañana.")
        if db.count_by("net_hash", net_hash, since) >= PER_NET_DAY:
            _error(429, "Tu red alcanzó el límite de firmas de hoy. Vuelve mañana.")

        # 5. Rasterizado en el servidor: Discord recibe siempre un PNG 756x280 generado aquí.
        img = strokes_mod.rasterize(data.strokes)
        phash = strokes_mod.dhash(img)

        status = "accepted"
        recent = db.recent_phashes(ip_hash, net_hash, int(time.time()) - NEAR_DUP_WINDOW)
        if any(strokes_mod.hamming(phash, h) <= NEAR_DUP_DISTANCE for h in recent):
            status = "flagged"
        elif db.accepted_since(int(time.time()) - 60) >= GLOBAL_PER_MINUTE:
            status = "flagged"

        db.insert_signature(
            ip_hash=ip_hash, net_hash=net_hash, device_hash=device_hash, phash=phash,
            stroke_stats=json.dumps(stats.as_dict()),
            strokes_json=json.dumps(data.strokes, separators=(",", ":")) if data.public_wall else None,
            public_wall=data.public_wall, status=status, png=strokes_mod.to_png(img),
        )
        count = total_count()
        if status == "flagged":
            return JSONResponse({"status": "review", "count": count}, status_code=202)
        return {"status": "accepted", "count": count, "signature_no": count}

    @app.get("/api/muro")
    async def muro(request: Request, limit: int = 12):
        throttle(f"wall:{ip_of(request)}", COUNTER_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        rows = app.state.db.public_wall(max(1, min(limit, 24)))
        items = [{"id": r["id"], "created_at": r["created_at"], "strokes": json.loads(r["strokes_json"])} for r in rows]
        return JSONResponse({"items": items}, headers={"Cache-Control": "public, max-age=60"})

    @app.get("/api/reacciones")
    async def reacciones(request: Request):
        throttle(f"rcount:{ip_of(request)}", COUNTER_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        return JSONResponse({"emoji": "🔥", "counts": app.state.db.reaction_counts()}, headers={"Cache-Control": "public, max-age=10"})

    @app.post("/api/reacciones")
    async def reaccionar(data: ReactRequest, request: Request):
        """Solo existe la reacción 🔥. Una por dispositivo y mensaje; se puede quitar."""
        db: Database = app.state.db
        ip = ip_of(request)
        ip_hash = antispam.anon(ip, settings.ip_salt)
        device_hash = antispam.anon(data.device_id, settings.ip_salt)
        throttle(f"react:{ip_hash}", REACT_PER_MINUTE, 60, "Vas muy rápido con las reacciones. Espera un momento.")
        if db.is_banned(antispam.anon(antispam.network_of(ip), settings.ip_salt)):
            _error(403, "No se aceptan reacciones desde esta red.")
        if data.on and not db.has_reaction(data.message_id, device_hash) \
                and db.reactions_from_ip(data.message_id, ip_hash) >= REACT_PER_IP_MESSAGE:
            _error(429, "Ya hay demasiadas reacciones desde tu conexión en este mensaje.")
        db.set_reaction(data.message_id, device_hash, ip_hash, data.on)
        return {"message_id": data.message_id, "on": data.on, "count": db.reaction_count(data.message_id)}

    @app.get("/api/embed.json")
    async def embed_json(request: Request):
        """Vista previa de Discord (Component Embed) con el número de firmas en vivo.
        Discord solo la lee si el JSON está en el mismo dominio que la página o en un subdominio
        (p. ej. api.arminbaneado.com) y la cachea unos 30 minutos."""
        throttle(f"embed:{ip_of(request)}", COUNTER_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        n = total_count()
        line = f"**{n}** {'persona ya ha firmado' if n == 1 else 'personas ya han firmado'}"
        return JSONResponse(embed_payload(line), headers={"Cache-Control": "public, max-age=300"})

    @app.get("/api/perfil/{slug}")
    async def perfil(slug: str, request: Request):
        throttle(f"profile:{ip_of(request)}", PROFILE_PER_MINUTE, 60, "Demasiadas consultas. Espera un momento.")
        try:
            data = await app.state.profiles.get(slug.lower())
        except ProfileUnavailable:
            _error(503, "Perfil no disponible.")
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})

    return app


async def _purge_nonces(db: Database):
    while True:
        db.purge_nonces(int(time.time()))
        await asyncio.sleep(3600)


def __getattr__(name: str):
    # `uvicorn main:app` crea la app al pedirla, para que importar el módulo en tests no exija variables de entorno.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
