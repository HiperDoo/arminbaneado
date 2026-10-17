"""Entrega de firmas a Discord. El estado vive en la base de datos, asi que nada se pierde al reiniciar."""

import asyncio
import json
import logging

import httpx

from db import Database

log = logging.getLogger("armin.webhook")

BATCH = 10  # Discord admite hasta 10 adjuntos por mensaje
IDLE_SECONDS = 5
MAX_ATTEMPTS = 8


class WebhookWorker:
    def __init__(self, db: Database, client: httpx.AsyncClient, *, main_url: str, review_url: str = ""):
        self.db = db
        self.client = client
        # (estado de la firma, url del webhook, texto del mensaje)
        self.targets = [("accepted", main_url, "Nuevas firmas para la expulsión de Armin")]
        if review_url:
            self.targets.append(("flagged", review_url, "Firmas en revisión (posible spam)"))
        self._stop = asyncio.Event()

    def stop(self):
        self._stop.set()

    async def run(self):
        if not self.targets[0][1]:
            log.warning("DISCORD_WEBHOOK_URL no configurado: las firmas no se envían a Discord")
            return
        while not self._stop.is_set():
            wait = IDLE_SECONDS
            for status, url, text in self.targets:
                try:
                    wait = max(wait, await self.flush(status, url, text))
                except Exception:  # el worker nunca debe morir por un error puntual
                    log.exception("Error enviando lote al webhook")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def flush(self, status: str, url: str, text: str) -> float:
        """Envía un lote. Devuelve cuántos segundos esperar antes del siguiente."""
        rows = self.db.pending_webhook(status, BATCH, MAX_ATTEMPTS)
        if not rows:
            return IDLE_SECONDS
        ids = [r["id"] for r in rows]
        payload = {
            "content": f"{text} ({len(rows)})",
            "username": "Caso Armin - Firmas",
            "allowed_mentions": {"parse": []},
            "attachments": [{"id": i, "filename": f"firma-{r['id']}.png"} for i, r in enumerate(rows)],
        }
        files = {f"files[{i}]": (f"firma-{r['id']}.png", r["png"], "image/png") for i, r in enumerate(rows)}
        try:
            resp = await self.client.post(url, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
        except httpx.HTTPError as exc:
            log.warning("Webhook inalcanzable: %s", exc)
            self.db.bump_attempts(ids)
            return 30

        if resp.status_code == 429:
            retry = _retry_after(resp)
            log.info("Discord pidió esperar %.1fs", retry)
            return retry
        if 200 <= resp.status_code < 300:
            self.db.mark_sent(ids)
            return 1 if len(rows) == BATCH else IDLE_SECONDS
        log.warning("Webhook respondió %s: %s", resp.status_code, resp.text[:300])
        self.db.bump_attempts(ids)
        return 30


def _retry_after(resp: httpx.Response) -> float:
    try:
        return float(resp.json().get("retry_after", 5)) + 0.5
    except ValueError:
        return float(resp.headers.get("x-ratelimit-reset-after", 5)) + 0.5
