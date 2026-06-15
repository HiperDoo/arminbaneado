#!/usr/bin/env python3
"""
Backend API — Resolución Caso Armin
Servidor Python que maneja Discord OAuth y firmas.

Endpoints:
  GET /api/config           → config pública para el frontend
  GET /api/firmas           → devuelve firmas dinámicas
  GET /api/firmar/callback  → callback de Discord OAuth (ÚNICA forma de firmar)
  GET /api/health           → estado del servidor

Variables de entorno (.env o sistema):
  DISCORD_CLIENT_ID=
  DISCORD_CLIENT_SECRET=
  DISCORD_REDIRECT_URI=https://TU_HOST/api/firmar/callback
  GITHUB_PAGES_URL=https://santiagortegadev.github.io/baneodearmin
  HOST_URL=https://TU_HOST
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
import httpx
import uvicorn

# ─── Config desde variables de entorno ───────────────────────
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "")
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
    # Limpiar entradas viejas (>1h)
    cutoff = now - 3600
    _rate_limits.update({k: v for k, v in _rate_limits.items() if v > cutoff})
    return True


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
                return web_url
    except Exception as e:
        print(f"⚠️  Avatar download error for {username}: {e}")

    # Fallback: usar URL de Discord CDN directamente
    return avatar_url.split("?")[0]


# ─── FastAPI App ─────────────────────────────────────────────
app = FastAPI(title="Caso Armin API", docs_url=None, redoc_url=None)

# CORS — solo permitir GitHub Pages
allowed_origins = [GITHUB_PAGES_URL] if GITHUB_PAGES_URL else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET"],
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


@app.get("/api/firmar/callback")
async def firmar_callback(request: Request, code: str = Query(None)):
    """
    Discord OAuth callback — ÚNICA forma de agregar una firma.
    Flujo:
      1. Recibe código de Discord
      2. Intercambia código por token (usa client_secret)
      3. Obtiene datos del usuario
      4. Descarga y guarda avatar localmente
      5. Registra/actualiza firma en firmas.json
      6. Redirige a la aplicación con parámetros
    """
    redirect_target = HOST_URL if HOST_URL else GITHUB_PAGES_URL
    if not code:
        return RedirectResponse(f"{redirect_target}?error=no_code")

    # Rate limit por IP
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    if not check_rate_limit(client_ip):
        return RedirectResponse(f"{redirect_target}?error=rate_limit")

    try:
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
            print(f"⚠️  Token exchange failed: {token_data.get('error', 'unknown')}")
            return RedirectResponse(f"{redirect_target}?error=no_token")

        # ── Obtener datos del usuario ──
        async with httpx.AsyncClient(timeout=10) as client:
            user_resp = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            user_data = user_resp.json()

        if "id" not in user_data:
            return RedirectResponse(f"{redirect_target}?error=no_user")

        # ── Descargar avatar ──
        avatar_path = await download_avatar(
            user_data["id"],
            user_data.get("avatar", ""),
            user_data["username"],
        )

        # ── Procesar firma ──
        firmas = read_firmas()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Buscar si ya firmó (por discord_id)
        existing = next(
            (f for f in firmas if f.get("discord_id") == user_data["id"]),
            None,
        )

        if existing:
            # Actualizar firma existente
            existing["fecha"] = now_iso
            existing["avatar"] = avatar_path
            action = "actualizada"
            message = "¡Gracias por firmar otra vez! Se actualizó tu firma."
        else:
            # Nueva firma
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

        # ── Redirigir a la aplicación ──
        params = urlencode(
            {
                "discord_id": user_data["id"],
                "username": user_data["username"],
                "avatar": avatar_path,
                "action": action,
                "message": message,
            }
        )
        return RedirectResponse(f"{redirect_target}?{params}")

    except Exception as e:
        print(f"❌ OAuth error: {e}")
        return RedirectResponse(f"{redirect_target}?error=oauth_failed")


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
    return HTMLResponse(content="<h1>Resolución Caso Armin</h1><p>El frontend index.html no se encuentra en el directorio raíz del servidor.</p>", status_code=404)


# ─── GET /config.json (Configuración dinámica para el frontend) ─
@app.get("/config.json")
async def serve_config():
    return {
        "host_url": HOST_URL if HOST_URL else f"http://arminbaneado.com",
        "discord_client_id": DISCORD_CLIENT_ID
    }


# ─── GET /firmas_old.json (Servir firmas antiguas) ────────────
@app.get("/firmas_old.json")
async def serve_firmas_old():
    path = Path("firmas_old.json")
    if path.exists():
        return FileResponse(path)
    return JSONResponse(content=[])


# ─── Archivos estáticos (avatares) ──────────────────────────
# Montar DESPUÉS de las rutas API para que /api/* tenga prioridad
app.mount("/assets", StaticFiles(directory=str(AVATAR_DIR.parent)), name="assets")


# ─── Iniciar servidor ────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("═══════════════════════════════════════════")
    print("  Resolución Caso Armin — API Server")
    print("═══════════════════════════════════════════")
    print(f"  🌐  Host:       http://0.0.0.0:{PORT}")
    print(f"  📄  GitHub Pages: {GITHUB_PAGES_URL or '(no configurado)'}")
    print(f"  🔑  Discord:     {'✓ Configurado' if DISCORD_CLIENT_ID else '✗ NO configurado'}")
    print(f"  📁  Datos:       {DATA_DIR.resolve()}")
    print(f"  🖼️  Avatares:    {AVATAR_DIR.resolve()}")
    print("═══════════════════════════════════════════")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
