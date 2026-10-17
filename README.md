# Caso Armin

Petición de broma para la comunidad. **Esto es joda, te amamos Armin.**

- Raíz del repo: la web, publicada con GitHub Pages en https://arminbaneado.com
  - `config.json`: URL de la API (`api_url`) y site key pública de Cloudflare Turnstile (`turnstile_sitekey`).
  - `embed.json`: vista previa de Discord. La actualiza sola la Action `embed.yml` cada 10 min con el número de firmas de la API.
  - `data/mensajes.json`: los motivos. `data/perfil.json`: la tarjeta de perfil.
- `api/`: la API (FastAPI + SQLite). Arranque en el servidor, desde la carpeta `api/`:

  ```
  uvicorn api3:app --host '::' --port $PORT --proxy-headers --forwarded-allow-ips "::1"
  ```

  Variables de entorno: ver `api/env.example`. Tests: `python3 -m pytest -q api/tests`.
