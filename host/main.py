#!/usr/bin/env python3
"""
Backend API — Resolución Caso Armin
Servidor Python que maneja Discord OAuth y firmas anónimas.

Las firmas son ANÓNIMAS: solo se guarda el discord_id para evitar
duplicados y la fecha. No se almacenan nombres, avatares ni datos
personales.

Flujo de firma (el usuario NUNCA visita ngrok):
  1. Frontend (GitHub Pages) manda al usuario a Discord OAuth
  2. Discord redirige de vuelta a GitHub Pages con ?code=xxx
  3. El JS de GitHub Pages manda ese código al host por POST /api/firmar
  4. El host intercambia el código por token, obtiene discord_id, guarda firma
  5. El host devuelve JSON con acción y mensaje (sin datos personales)

Endpoints:
  GET  /api/config            → config pública para el frontend
  GET  /api/firmas/count      → devuelve cantidad de firmas
  POST /api/firmar             → recibe { code } de Discord, devuelve JSON
  GET  /api/health            → estado del servidor

Variables de entorno (.env o sistema):
  DISCORD_CLIENT_ID=
  DISCORD_CLIENT_SECRET=
  DISCORD_REDIRECT_URI=https://arminbaneado.com
  GITHUB_PAGES_URL=https://arminbaneado.com
  HOST_URL=https://TU_NGROK.ngrok-free.app
  PORT=3000
  DATA_DIR=.
"""

import os
import json
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import uvicorn

# ─── Config desde variables de entorno ───────────────────────
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "").rstrip("/")
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "").rstrip("/")
HOST_URL = os.environ.get("HOST_URL", "").rstrip("/")
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
PORT = int(os.environ.get("PORT", "3000"))

# ─── Paths ───────────────────────────────────────────────────
FIRMAS_PATH = DATA_DIR / "firmas.json"
FIRMAS_PATH.parent.mkdir(parents=True, exist_ok=True)

# ─── Rate Limiting (en memoria) ──────────────────────────────
_rate_limits: dict[str, float] = {}
RATE_LIMIT_SECONDS = 5


def check_rate_limit(key: str) -> bool:
    """Devuelve True si pasó el cooldown, False si está en rate limit."""
    now = time.time()
    last = _rate_limits.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limits[key] = now
    cutoff = now - 3600
    _rate_limits.update({k: v for k, v in _rate_limits.items() if v > cutoff})
    return True


# ─── Pydantic Models ─────────────────────────────────────────
class FirmarRequest(BaseModel):
    code: str


# ─── Helpers ─────────────────────────────────────────────────
def read_firmas() -> list:
    """
    Lee la lista de firmas. Cada firma es un dict con solo:
      { "discord_id": "...", "fecha": "..." }
    """
    try:
        return json.loads(FIRMAS_PATH.read_text("utf-8"))
    except Exception:
        return []


def write_firmas(data: list):
    FIRMAS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), "utf-8"
    )


async def process_discord_code(code: str) -> dict:
    """
    Intercambia un código de Discord OAuth por el discord_id del usuario,
    guarda la firma anónima y devuelve el resultado.
    No se almacena nombre, avatar ni ningún dato personal.
    """
    # ── Intercambiar código por token ──
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_resp.json()

    if "access_token" not in token_data:
        error = token_data.get("error", "unknown")
        print(f"⚠️  Token exchange failed: {error}")
        return {"error": "no_token", "message": f"No se pudo obtener el token de Discord ({error})."}

    # ── Obtener discord_id del usuario ──
    async with httpx.AsyncClient(timeout=10) as client:
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user_data = user_resp.json()

    if "id" not in user_data:
        return {"error": "no_user", "message": "No se pudieron obtener tus datos de Discord."}

    discord_id = user_data["id"]

    # ── Procesar firma anónima ──
    firmas = read_firmas()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    existing = next(
        (f for f in firmas if f.get("discord_id") == discord_id),
        None,
    )

    if existing:
        action = "ya_firmada"
        message = "Ya habías firmado esta resolución. Tu firma anónima ya estaba registrada."
    else:
        firmas.append(
            {
                "discord_id": discord_id,
                "fecha": now_iso,
            }
        )
        action = "nueva"
        message = "¡Gracias por firmar!"

    write_firmas(firmas)
    print(f"✅ Firma {action}: discord_id={discord_id}")

    return {
        "action": action,
        "message": message,
    }


# ─── FastAPI App ─────────────────────────────────────────────
app = FastAPI(title="Caso Armin API", docs_url=None, redoc_url=None)

# CORS — permitir cualquier origen (el frontend está en GitHub Pages y
# puede cambiar de dominio). La seguridad está en Discord OAuth, no en CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["ngrok-skip-browser-warning"],
)


# ─── GET /api/config ─────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    """Config pública para el frontend (sin secrets)."""
    return {
        "discord_client_id": DISCORD_CLIENT_ID,
        "host_url": HOST_URL,
        "configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
    }


# ─── GET /api/firmas/count ───────────────────────────────────
@app.get("/api/firmas/count")
async def get_firmas_count():
    """Devuelve la cantidad de firmas anónimas registradas."""
    return {"count": len(read_firmas())}


# ─── GET /api/firmas (compatibilidad) ────────────────────────
@app.get("/api/firmas")
async def get_firmas():
    """Devuelve cantidad de firmas (formato compatible)."""
    return {"firmas": read_firmas(), "count": len(read_firmas())}


# ─── POST /api/firmar — FLUJO PRINCIPAL ──────────────────────
@app.post("/api/firmar")
async def firmar_post(request: Request, body: FirmarRequest):
    """
    Recibe { code: "xxx" } del frontend (GitHub Pages).
    Intercambia el código por token, obtiene discord_id,
    guarda firma anónima y devuelve JSON.

    El usuario NUNCA navega a ngrok — todo es por fetch desde GitHub Pages.
    """
    # Rate limit por IP
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    if not check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit", "message": "Estás firmando muy rápido. Espera unos segundos."},
        )

    if not body.code:
        return JSONResponse(
            status_code=400,
            content={"error": "no_code", "message": "No se recibió código de Discord."},
        )

    result = await process_discord_code(body.code)

    if "error" in result:
        return JSONResponse(status_code=400, content=result)

    return result


# ─── GET /api/health ─────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "configured": bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET),
        "firmas_count": len(read_firmas()),
        "host_url": HOST_URL,
        "github_pages_url": GITHUB_PAGES_URL,
    }


# ─── GET / (Servir index.html) ──────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = Path("index.html")
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(content="<h1>Resolución Caso Armin</h1>", status_code=404)


# ─── GET /config.json ────────────────────────────────────────
@app.get("/config.json")
async def serve_config():
    return {
        "host_url": HOST_URL,
        "discord_client_id": DISCORD_CLIENT_ID,
    }


# ─── Iniciar servidor ────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("═══════════════════════════════════════════")
    print("  Resolución Caso Armin — API Server")
    print("  (Firmas anónimas — solo discord_id)")
    print("═══════════════════════════════════════════")
    print(f"  🌐  Host:        http://0.0.0.0:{PORT}")
    print(f"  📄  Frontend:    {GITHUB_PAGES_URL or '(no configurado)'}")
    print(f"  🔑  Discord:     {'✓ Configurado' if DISCORD_CLIENT_ID else '✗ NO configurado'}")
    print(f"  🔁  Redirect:    {DISCORD_REDIRECT_URI or '(no configurado)'}")
    print(f"  📁  Datos:       {DATA_DIR.resolve()}")
    print(f"  📊  Firmas:      {len(read_firmas())}")
    print("═══════════════════════════════════════════")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
