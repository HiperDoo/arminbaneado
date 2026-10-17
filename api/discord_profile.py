"""Datos públicos de perfil de Discord (avatar, banner, roles) para la tarjeta de la página.

Usa la API REST con un bot que está en el servidor. Solo sale una lista blanca de
campos; nunca se reenvía la respuesta cruda de Discord.
Estado (en línea, ausente...) y "Sobre mí" no están disponibles por REST: van en null
y la página usa sus valores estáticos.
"""

import asyncio
import time

import httpx

API = "https://discord.com/api/v10"
CDN = "https://cdn.discordapp.com"
DISCORD_EPOCH_MS = 1420070400000
PROFILE_TTL = 600
ROLES_TTL = 3600


class ProfileUnavailable(Exception):
    pass


def _hex(color: int | None) -> str | None:
    return f"#{color:06x}" if color else None


def _ext(asset_hash: str) -> str:
    return "gif" if asset_hash.startswith("a_") else "png"


def _snowflake_iso(user_id: str) -> str:
    ms = (int(user_id) >> 22) + DISCORD_EPOCH_MS
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


class ProfileService:
    def __init__(self, client: httpx.AsyncClient, *, bot_token: str, guild_id: str, profiles: dict[str, str]):
        self.client = client
        self.guild_id = guild_id
        self.profiles = profiles
        self._headers = {"Authorization": f"Bot {bot_token}"} if bot_token else {}
        self._cache: dict[str, tuple[float, dict]] = {}
        self._roles: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._headers and self.guild_id)

    async def get(self, slug: str) -> dict:
        user_id = self.profiles.get(slug)
        if not user_id or not self.enabled:
            raise ProfileUnavailable(slug)
        cached = self._cache.get(slug)
        if cached and time.time() - cached[0] < PROFILE_TTL:
            return cached[1]
        async with self._lock:
            try:
                data = await self._fetch(user_id)
            except (httpx.HTTPError, KeyError, ValueError):
                if cached:  # Discord caído: mejor la última respuesta buena que nada
                    return cached[1]
                raise ProfileUnavailable(slug)
            self._cache[slug] = (time.time(), data)
            return data

    async def _api(self, path: str) -> dict | list:
        resp = await self.client.get(f"{API}{path}", headers=self._headers, timeout=8)
        resp.raise_for_status()
        return resp.json()

    async def _roles_map(self) -> dict:
        if self._roles and time.time() - self._roles[0] < ROLES_TTL:
            return self._roles[1]
        roles = await self._api(f"/guilds/{self.guild_id}/roles")
        mapped = {r["id"]: r for r in roles}
        self._roles = (time.time(), mapped)
        return mapped

    async def _fetch(self, user_id: str) -> dict:
        user, member, roles = await asyncio.gather(
            self._api(f"/users/{user_id}"),
            self._api(f"/guilds/{self.guild_id}/members/{user_id}"),
            self._roles_map(),
        )
        if member.get("avatar"):
            h = member["avatar"]
            avatar = f"{CDN}/guilds/{self.guild_id}/users/{user_id}/avatars/{h}.{_ext(h)}?size=256"
        elif user.get("avatar"):
            h = user["avatar"]
            avatar = f"{CDN}/avatars/{user_id}/{h}.{_ext(h)}?size=256"
        else:
            avatar = f"{CDN}/embed/avatars/{(int(user_id) >> 22) % 6}.png"

        banner = None
        if user.get("banner"):
            h = user["banner"]
            banner = f"{CDN}/banners/{user_id}/{h}.{_ext(h)}?size=600"

        decoration = None
        deco = user.get("avatar_decoration_data") or {}
        if deco.get("asset"):
            decoration = f"{CDN}/avatar-decoration-presets/{deco['asset']}.png?size=160&passthrough=true"

        member_roles = [roles[r] for r in member.get("roles", []) if r in roles and r != self.guild_id]
        member_roles.sort(key=lambda r: r.get("position", 0), reverse=True)

        return {
            "display_name": member.get("nick") or user.get("global_name") or user["username"],
            "username": user["username"],
            "avatar_url": avatar,
            "avatar_decoration_url": decoration,
            "banner_url": banner,
            # banner_color ya viene como "#rrggbb"; accent_color como entero
            "banner_color": user.get("banner_color") or _hex(user.get("accent_color")),
            "roles": [{
                "name": r["name"],
                "color": _hex(r.get("color")),
                "icon": f"{CDN}/role-icons/{r['id']}/{r['icon']}.png?size=32" if r.get("icon") else None,
                "emoji": r.get("unicode_emoji"),
            } for r in member_roles],
            "member_since": member.get("joined_at"),
            "discord_since": _snowflake_iso(user_id),
            "status": None,
            "bio": None,
        }
