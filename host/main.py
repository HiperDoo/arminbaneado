"""
Armin Ban Case — Backend API
Recibe firmas (imágenes dibujadas) y las reenvía por webhook a Discord.
No guarda firmas en disco. Cola de envío con 5 segundos entre cada una.

Reglas de tamaño:
  - 756x280 → cuenta + envía a webhook
  - Menor que 756x280 → cuenta pero descarta (no guarda, no envía)
  - Mayor que 756x280 → rechaza inmediatamente (no cuenta)
"""

import asyncio
import base64
import io
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PIL import Image

# ===== CONFIG =====
import os
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Dimensiones requeridas para enviar al webhook
REQUIRED_WIDTH = 756
REQUIRED_HEIGHT = 280

# ===== APP =====
app = FastAPI(title="Armin Ban API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== COUNTER =====
counter = 0

# ===== WEBHOOK QUEUE =====
webhook_queue: deque = deque()
queue_processing = False

async def process_webhook_queue():
    """Procesa la cola de webhooks, enviando uno cada 5 segundos."""
    global queue_processing
    queue_processing = True

    while webhook_queue:
        image_data = webhook_queue.popleft()
        try:
            await send_webhook(image_data)
        except Exception as e:
            print(f"[Webhook Error] {e}")

        # Esperar 5 segundos antes del siguiente envío
        if webhook_queue:
            await asyncio.sleep(5)

    queue_processing = False

async def send_webhook(image_base64: str):
    """Envía la firma como imagen adjunta al webhook de Discord."""
    if not DISCORD_WEBHOOK_URL:
        print("[Webhook] No configurado — saltando envío")
        return

    # Decodificar base64 a bytes
    try:
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        print(f"[Webhook] Error decodificando imagen: {e}")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        files = {
            "file": ("firma.png", image_bytes, "image/png")
        }
        payload = {
            "content": "🔥 Nueva firma recibida para banear a Armin!",
            "username": "Ban Armin - Firmas",
        }

        try:
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                data=payload,
                files=files
            )
            if resp.status_code == 204 or resp.status_code == 200:
                print("[Webhook] Firma enviada a Discord correctamente")
            else:
                print(f"[Webhook] Error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[Webhook] Error enviando: {e}")

def enqueue_webhook(image_base64: str):
    """Añade una firma a la cola de webhooks."""
    webhook_queue.append(image_base64)
    if not queue_processing:
        asyncio.create_task(process_webhook_queue())

def decode_and_check_size(image_base64: str):
    """
    Decodifica la imagen base64 y verifica sus dimensiones.
    Retorna (width, height) o lanza HTTPException si es muy grande.
    """
    try:
        # Quitar prefijo data:image/...;base64,
        raw = image_base64
        if "," in raw:
            raw = raw.split(",", 1)[1]

        image_bytes = base64.b64decode(raw)
        img = Image.open(io.BytesIO(image_bytes))
        return img.width, img.height
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Imagen inválida: {e}")

# ===== MODELS =====
class SignaturePayload(BaseModel):
    firma: str  # base64 image data

# ===== ENDPOINTS =====

@app.get("/api/contador")
async def get_counter():
    """Devuelve el contador actual de firmas."""
    return {"count": counter}

@app.post("/api/firmar")
async def sign(payload: SignaturePayload):
    """
    Recibe una firma (imagen):
      - Mayor que 756x280 → rechaza (422)
      - Exactamente 756x280 → cuenta + envía a webhook
      - Menor que 756x280 → cuenta pero descarta (no guarda, no envía)
    """
    global counter

    if not payload.firma:
        raise HTTPException(status_code=400, detail="Firma vacía")

    # Verificar dimensiones de la imagen
    width, height = decode_and_check_size(payload.firma)

    # Mayor que lo permitido → rechazar
    if width > REQUIRED_WIDTH or height > REQUIRED_HEIGHT:
        raise HTTPException(
            status_code=422,
            detail=f"Imagen demasiado grande ({width}x{height}). Máximo permitido: {REQUIRED_WIDTH}x{REQUIRED_HEIGHT}."
        )

    # Incrementar contador (siempre que no sea rechazada)
    counter += 1

    # Solo enviar al webhook si es exactamente 756x280
    if width == REQUIRED_WIDTH and height == REQUIRED_HEIGHT:
        enqueue_webhook(payload.firma)

    return {"count": counter, "status": "ok"}

@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "counter": counter}

# ===== RUN =====
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
