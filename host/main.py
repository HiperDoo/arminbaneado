#!/usr/bin/env python3
"""
Backend API — Resolución Caso Armin
Servidor Python que maneja Discord OAuth y firmas.

Flujo de firma (el usuario NUNCA visita ngrok):
  1. Frontend (GitHub Pages) manda al usuario a Discord OAuth
  2. Discord redirige de vuelta a GitHub Pages con ?code=xxx
  3. El JS de GitHub Pages manda ese código al host por POST /api/firmar
  4. El host intercambia el código por token, obtiene datos, guarda firma
  5. El host devuelve JSON con los datos del usuario

Endpoints:
  GET  /api/config           → config pública para el frontend
  GET  /api/firmas           → devuelve firmas dinámicas
  POST /api/firmar            → recibe { code } de Discord, devuelve JSON (FLUJO PRINCIPAL)
  GET  /api/firmar/callback  → callback de Discord OAuth (redirige, flujo alternativo)
  GET  /api/health           → estado del servidor

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
from urllib.parse import urlencode
from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import uvicorn

# ─── Config desde variables de entorno ───────────────────────
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
# redirect_uri ahora es GitHub Pages (el usuario regresa ahí)
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "").rstrip("/")
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "").rstrip("/")
HOST_URL = os.environ.get("HOST_URL", "").rstrip("/")
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
PORT = int(os.environ.get("PORT", "3000"))

# ─── Paths ───────────────────────────────────────────────────
FIRMAS_PATH = DATA_DIR / "firmas.json"
AVATAR_DIR = DATA_DIR / "assets" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

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
    try:
        return json.loads(FIRMAS_PATH.read_text("utf-8"))
    except Exception:
        return []


def write_firmas(data: list):
    FIRMAS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), "utf-8"
    )


async def download_avatar(discord_id: str, avatar_hash: str, username: str) -> str:
    """Descarga avatar de Discord y lo guarda localmente. Devuelve URL local."""
    if not discord_id or not avatar_hash:
        return f"{HOST_URL}/assets/avatars/default.png"

    avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
    filename = f"{username}.png"
    filepath = AVATAR_DIR / filename
    web_url = f"{HOST_URL}/assets/avatars/{filename}"

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(avatar_url)
            if resp.status_code == 200 and len(resp.content) > 1024:
                filepath.write_bytes(resp.content)
                print(f"🖼️  Avatar saved: {filepath} ({len(resp.content)//1024}KB)")
                return web_url
    except Exception as e:
        print(f"⚠️  Avatar download error for {username}: {e}")

    # Fallback: usar URL de Discord CDN directamente
    return avatar_url.split("?")[0]


async def process_discord_code(code: str) -> dict:
    """
    Intercambia un código de Discord OAuth por datos del usuario,
    guarda la firma y devuelve el resultado.
    Usado tanto por POST /api/firmar como por GET /api/firmar/callback.
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

    # ── Obtener datos del usuario ──
    async with httpx.AsyncClient(timeout=10) as client:
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user_data = user_resp.json()

    if "id" not in user_data:
        return {"error": "no_user", "message": "No se pudieron obtener tus datos de Discord."}

    # ── Descargar avatar ──
    avatar_path = await download_avatar(
        user_data["id"],
        user_data.get("avatar", ""),
        user_data["username"],
    )

    # ── Procesar firma ──
    firmas = read_firmas()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    existing = next(
        (f for f in firmas if f.get("discord_id") == user_data["id"]),
        None,
    )

    if existing:
        existing["fecha"] = now_iso
        existing["avatar"] = avatar_path
        action = "actualizada"
        message = "¡Gracias por firmar otra vez! Se actualizó tu firma."
    else:
        firmas.append(
            {
                "nombre": user_data.get("global_name") or user_data["username"],
                "username": user_data["username"],
                "discord_id": user_data["id"],
                "avatar": avatar_path,
                "fecha": now_iso,
                "rol": "Miembro",
            }
        )
        action = "nueva"
        message = "¡Gracias por firmar!"

    write_firmas(firmas)
    print(f"✅ Firma {action}: @{user_data['username']} ({user_data['id']})")

    return {
        "action": action,
        "message": message,
        "user": {
            "nombre": user_data.get("global_name") or user_data["username"],
            "username": user_data["username"],
            "discord_id": user_data["id"],
            "avatar": avatar_path,
            "fecha": now_iso,
            "rol": existing.get("rol", "Miembro") if existing else "Miembro",
        },
    }


# ─── FastAPI App ─────────────────────────────────────────────
app = FastAPI(title="Caso Armin API", docs_url=None, redoc_url=None)

# CORS — permitir GitHub Pages Y el dominio custom
origins = []
if GITHUB_PAGES_URL:
    origins.append(GITHUB_PAGES_URL)
if HOST_URL:
    origins.append(HOST_URL)
# Siempre permitir el dominio custom
if "arminbaneado.com" not in origins:
    origins.append("https://arminbaneado.com")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
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


# ─── GET /api/firmas ─────────────────────────────────────────
@app.get("/api/firmas")
async def get_firmas():
    """Devuelve todas las firmas dinámicas (las del host)."""
    return {"firmas": read_firmas()}


# ─── POST /api/firmar — FLUJO PRINCIPAL ──────────────────────
@app.post("/api/firmar")
async def firmar_post(request: Request, body: FirmarRequest):
    """
    Recibe { code: "xxx" } del frontend (GitHub Pages).
    Intercambia el código por token, obtiene datos del usuario,
    guarda firma y devuelve JSON.
    
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


# ─── GET /api/firmar/callback — FLUJO ALTERNATIVO ────────────
@app.get("/api/firmar/callback")
async def firmar_callback(request: Request, code: str = Query(None)):
    """
    Flujo alternativo: Discord redirige aquí directamente.
    Procesa la firma y redirige a GitHub Pages con parámetros.
    (Se mantiene como fallback)
    """
    redirect_target = GITHUB_PAGES_URL or HOST_URL
    if not code:
        return RedirectResponse(f"{redirect_target}?error=no_code")

    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    if not check_rate_limit(client_ip):
        return RedirectResponse(f"{redirect_target}?error=rate_limit")

    result = await process_discord_code(code)

    if "error" in result:
        params = urlencode({"error": result["error"], "message": result.get("message", "")})
        return RedirectResponse(f"{redirect_target}?{params}")

    user = result["user"]
    params = urlencode({
        "discord_id": user["discord_id"],
        "username": user["username"],
        "avatar": user["avatar"],
        "action": result["action"],
        "message": result["message"],
    })
    return RedirectResponse(f"{redirect_target}?{params}")


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


# ─── GET /firmas_old.json ────────────────────────────────────
@app.get("/firmas_old.json")
async def serve_firmas_old():
    path = Path("firmas_old.json")
    if path.exists():
        return FileResponse(path)
    return JSONResponse(content=[])


# ─── Archivos estáticos (avatares) ──────────────────────────
app.mount("/assets", StaticFiles(directory=str(AVATAR_DIR.parent)), name="assets")


# ─── Iniciar servidor ────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("═══════════════════════════════════════════")
    print("  Resolución Caso Armin — API Server")
    print("═══════════════════════════════════════════")
    print(f"  🌐  Host:        http://0.0.0.0:{PORT}")
    print(f"  📄  Frontend:    {GITHUB_PAGES_URL or '(no configurado)'}")
    print(f"  🔑  Discord:     {'✓ Configurado' if DISCORD_CLIENT_ID else '✗ NO configurado'}")
    print(f"  🔁  Redirect:    {DISCORD_REDIRECT_URI or '(no configurado)'}")
    print(f"  📁  Datos:       {DATA_DIR.resolve()}")
    print(f"  🖼️  Avatares:    {AVATAR_DIR.resolve()}")
    print("═══════════════════════════════════════════")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
