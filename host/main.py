"""
Armin Ban Case — Backend API
Recibe firmas (imágenes dibujadas) y las reenvía por webhook a Discord.
No guarda firmas en disco. Cola de envío con 5 segundos entre cada una.
"""

import asyncio
import base64
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ===== CONFIG =====
# Lee la URL del webhook desde una variable de entorno o hardcodea aquí
import os
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

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
# El contador empieza en 0 y solo se incrementa con cada firma recibida
# No se carga de ningún archivo — la fuente de verdad es la memoria
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
        # Quitar el prefijo data:image/png;base64,
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]

        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        print(f"[Webhook] Error decodificando imagen: {e}")
        return

    async with httpx.AsyncClient(timeout=30) as client:
        # Enviar como multipart con archivo adjunto
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
    """Recibe una firma (imagen), incrementa el contador y la envía a Discord."""
    global counter

    if not payload.firma:
        raise HTTPException(status_code=400, detail="Firma vacía")

    # Incrementar contador
    counter += 1

    # Encolar envío a Discord webhook
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
