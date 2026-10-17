"""Configuracion leida de variables de entorno. Nada sensible vive en el codigo."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Claves secretas de prueba oficiales de Cloudflare Turnstile.
# Con ellas siteverify devuelve respuestas fijas sin hostname/action/cdata reales.
TURNSTILE_TEST_SECRETS = {
    "1x0000000000000000000000000000000AA",  # siempre pasa
    "2x0000000000000000000000000000000AA",  # siempre falla
    "3x0000000000000000000000000000000AA",  # token ya usado
}


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _profiles(value: str) -> dict[str, str]:
    """`armin:123,darki:456` -> {"armin": "123", "darki": "456"}."""
    out = {}
    for pair in _list(value):
        slug, _, user_id = pair.partition(":")
        if slug and user_id.isdigit():
            out[slug.strip().lower()] = user_id.strip()
    return out


@dataclass
class Settings:
    env: str = "prod"
    db_path: Path = Path(__file__).parent / "data" / "firmas.db"

    allowed_origins: list[str] = field(default_factory=lambda: ["https://arminbaneado.com", "https://www.arminbaneado.com"])
    turnstile_hostnames: list[str] = field(default_factory=lambda: ["arminbaneado.com", "www.arminbaneado.com"])
    turnstile_secret: str = ""
    turnstile_action: str = "firmar"

    # Secretos para HMAC. Deben ser largos y aleatorios en produccion.
    ip_salt: str = ""
    nonce_secret: str = ""

    # Saltos de proxy de confianza delante de la app (alwaysdata = 1).
    trusted_proxy_hops: int = 1

    discord_webhook_url: str = ""
    discord_review_webhook_url: str = ""

    discord_bot_token: str = ""
    discord_guild_id: str = ""
    profiles: dict[str, str] = field(default_factory=dict)

    legacy_offset: int = 0
    milestones: list[dict] = field(default_factory=list)

    max_body_bytes: int = 65536

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def turnstile_relaxed(self) -> bool:
        """Con claves de prueba no se puede exigir hostname/action/cdata."""
        return self.turnstile_secret in TURNSTILE_TEST_SECRETS


def load_settings() -> Settings:
    env = os.environ.get
    s = Settings(
        env=env("ENV", "prod"),
        turnstile_secret=env("TURNSTILE_SECRET", ""),
        ip_salt=env("IP_SALT", ""),
        nonce_secret=env("NONCE_SECRET", ""),
        trusted_proxy_hops=int(env("TRUSTED_PROXY_HOPS", "1")),
        discord_webhook_url=env("DISCORD_WEBHOOK_URL", ""),
        discord_review_webhook_url=env("DISCORD_REVIEW_WEBHOOK_URL", ""),
        discord_bot_token=env("DISCORD_BOT_TOKEN", ""),
        discord_guild_id=env("DISCORD_GUILD_ID", ""),
        profiles=_profiles(env("PROFILES", "")),
        legacy_offset=int(env("LEGACY_OFFSET", "0")),
    )
    if env("DB_PATH"):
        s.db_path = Path(env("DB_PATH"))
    if env("ALLOWED_ORIGINS"):
        s.allowed_origins = _list(env("ALLOWED_ORIGINS"))
    if env("TURNSTILE_HOSTNAMES"):
        s.turnstile_hostnames = _list(env("TURNSTILE_HOSTNAMES"))
    if env("MILESTONES"):
        s.milestones = json.loads(env("MILESTONES"))
    if s.is_dev:
        s.allowed_origins += ["http://localhost:8080", "http://127.0.0.1:8080"]

    if not s.is_dev:
        missing = [n for n, v in (("IP_SALT", s.ip_salt), ("NONCE_SECRET", s.nonce_secret), ("TURNSTILE_SECRET", s.turnstile_secret)) if not v]
        if missing:
            raise RuntimeError(f"Faltan variables de entorno obligatorias: {', '.join(missing)}")
    else:
        s.ip_salt = s.ip_salt or "dev-salt"
        s.nonce_secret = s.nonce_secret or "dev-nonce-secret"
    return s
