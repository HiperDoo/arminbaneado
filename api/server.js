/**
 * API Server — Resolución Caso Armin
 *
 * Endpoints:
 *   GET  /api/firmas           → devuelve { firmas_old, firmas } combinadas
 *   POST /api/firmar            → registra o actualiza firma (via Discord OAuth code)
 *   GET  /api/firmar/callback   → callback de Discord OAuth
 *
 * Variables de entorno (GitHub Secrets → .env en deploy):
 *   DISCORD_CLIENT_ID=
 *   DISCORD_CLIENT_SECRET=
 *   DISCORD_REDIRECT_URI=http://localhost:3000/api/firmar/callback
 *   DISCORD_BOT_TOKEN=          (opcional, para verificar miembros del servidor)
 *   PORT=3000
 *   AVATAR_DIR=./assets/avatars (opcional, directorio para guardar avatares)
 */

// Cargar .env si existe (desarrollo local)
try { require('dotenv').config(); } catch(e) {}

const express = require('express');
const fs = require('fs');
const path = require('path');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, '..')));

const FIRMAS_OLD_PATH = path.join(__dirname, '..', 'firmas_old.json');
const FIRMAS_PATH = path.join(__dirname, '..', 'firmas.json');
const AVATAR_DIR = process.env.AVATAR_DIR || path.join(__dirname, '..', 'assets', 'avatars');

// ─── Asegurar que el directorio de avatares existe ─────────
if (!fs.existsSync(AVATAR_DIR)) {
  fs.mkdirSync(AVATAR_DIR, { recursive: true });
}

// ─── Helpers ────────────────────────────────────────────────

function readJSON(filepath) {
  try {
    return JSON.parse(fs.readFileSync(filepath, 'utf-8'));
  } catch {
    return [];
  }
}

function writeJSON(filepath, data) {
  fs.writeFileSync(filepath, JSON.stringify(data, null, 2), 'utf-8');
}

/**
 * Descarga el avatar de Discord y lo guarda localmente.
 * Devuelve la ruta local del archivo guardado.
 * Si falla la descarga, devuelve la URL original como fallback.
 */
async function saveDiscordAvatar(discordId, avatarHash, username) {
  if (!discordId || !avatarHash) {
    return '/assets/avatars/default.png';
  }

  const avatarUrl = `https://cdn.discordapp.com/avatars/${discordId}/${avatarHash}.png?size=128`;
  const localFilename = `${username || discordId}.png`;
  const localPath = path.join(AVATAR_DIR, localFilename);
  const webPath = `/assets/avatars/${localFilename}`;

  try {
    const https = require('https');
    const http = require('http');

    const response = await new Promise((resolve, reject) => {
      const mod = avatarUrl.startsWith('https') ? https : http;
      mod.get(avatarUrl, { timeout: 10000 }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          // Seguir redirect
          const redirectMod = res.headers.location.startsWith('https') ? https : http;
          redirectMod.get(res.headers.location, { timeout: 10000 }, resolve).on('error', reject);
        } else {
          resolve(res);
        }
      }).on('error', reject);
    });

    if (response.statusCode !== 200) {
      console.warn(`Avatar download failed (${response.statusCode}) for ${username}, using CDN URL`);
      return avatarUrl.replace('?size=128', '');
    }

    const chunks = [];
    for await (const chunk of response) {
      chunks.push(chunk);
    }
    const buffer = Buffer.concat(chunks);

    // Verificar que es una imagen válida (mínimo 1KB)
    if (buffer.length < 1024) {
      console.warn(`Avatar too small (${buffer.length}B) for ${username}, using CDN URL`);
      return avatarUrl.replace('?size=128', '');
    }

    fs.writeFileSync(localPath, buffer);
    console.log(`Avatar saved: ${localPath} (${(buffer.length / 1024).toFixed(1)}KB)`);
    return webPath;

  } catch (err) {
    console.warn(`Avatar save error for ${username}:`, err.message);
    return avatarUrl.replace('?size=128', '');
  }
}

// ─── GET /api/firmas ────────────────────────────────────────

app.get('/api/firmas', (req, res) => {
  const firmas_old = readJSON(FIRMAS_OLD_PATH);
  const firmas = readJSON(FIRMAS_PATH);
  res.json({ firmas_old, firmas });
});

// ─── POST /api/firmar ───────────────────────────────────────
/**
 * Body: { discord_id, username, avatar_url }
 * Lógica:
 *   1. Descargar avatar de Discord y guardarlo localmente
 *   2. Buscar en firmas_old → si existe, mover a firmas.json con fecha actualizada
 *   3. Buscar en firmas.json → si existe, actualizar fecha y avatar
 *   4. Si no existe → agregar a firmas.json
 *
 * Response: { action: "nueva" | "actualizada", user: {...} }
 */

app.post('/api/firmar', async (req, res) => {
  const { discord_id, username, avatar_url } = req.body;

  if (!discord_id || !username) {
    return res.status(400).json({ error: 'discord_id y username son obligatorios' });
  }

  // Guardar avatar localmente si viene una URL de Discord CDN
  let localAvatarPath = `/assets/avatars/${username}.png`;
  if (avatar_url && avatar_url.includes('cdn.discordapp.com')) {
    // Extraer el avatar hash de la URL de Discord
    const hashMatch = avatar_url.match(/\/avatars\/\d+\/(\w+)\./);
    if (hashMatch) {
      localAvatarPath = await saveDiscordAvatar(discord_id, hashMatch[1], username);
    } else {
      localAvatarPath = avatar_url;
    }
  } else if (avatar_url) {
    localAvatarPath = avatar_url;
  }

  const firmas_old = readJSON(FIRMAS_OLD_PATH);
  const firmas = readJSON(FIRMAS_PATH);
  const now = new Date().toISOString();

  // 1. Buscar en firmas_old
  const oldIndex = firmas_old.findIndex(f => f.username.toLowerCase() === username.toLowerCase() || f.discord_id === discord_id);
  if (oldIndex !== -1) {
    const oldEntry = firmas_old.splice(oldIndex, 1)[0];
    const newEntry = {
      nombre: oldEntry.nombre || username,
      username: oldEntry.username || username,
      avatar: localAvatarPath,
      discord_id: discord_id,
      fecha: now,
      fecha_original: oldEntry.fecha,
      rol: oldEntry.rol || 'Miembro'
    };
    firmas.push(newEntry);
    writeJSON(FIRMAS_OLD_PATH, firmas_old);
    writeJSON(FIRMAS_PATH, firmas);
    return res.json({ action: 'actualizada', user: newEntry, message: '¡Gracias por firmar otra vez! Tu firma ha sido actualizada.' });
  }

  // 2. Buscar en firmas.json
  const existIndex = firmas.findIndex(f => f.discord_id === discord_id || f.username.toLowerCase() === username.toLowerCase());
  if (existIndex !== -1) {
    firmas[existIndex].fecha = now;
    firmas[existIndex].avatar = localAvatarPath; // Actualizar avatar también
    writeJSON(FIRMAS_PATH, firmas);
    return res.json({ action: 'actualizada', user: firmas[existIndex], message: '¡Gracias por firmar otra vez! Se actualizó la fecha de tu firma.' });
  }

  // 3. Nueva firma
  const newEntry = {
    nombre: username,
    username: username,
    avatar: localAvatarPath,
    discord_id: discord_id,
    fecha: now,
    rol: 'Miembro'
  };
  firmas.push(newEntry);
  writeJSON(FIRMAS_PATH, firmas);
  return res.json({ action: 'nueva', user: newEntry, message: '¡Gracias por firmar!' });
});

// ─── GET /api/firmar/callback ───────────────────────────────
/**
 * Discord OAuth callback.
 * Intercambia el código por token, obtiene datos del usuario,
 * descarga y guarda el avatar localmente, procesa la firma
 * y redirige al frontend con parámetros.
 */

app.get('/api/firmar/callback', async (req, res) => {
  const { code } = req.query;
  if (!code) return res.redirect('/?error=no_code');

  try {
    // Intercambiar código por token
    const tokenRes = await fetch('https://discord.com/api/oauth2/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: process.env.DISCORD_CLIENT_ID,
        client_secret: process.env.DISCORD_CLIENT_SECRET,
        grant_type: 'authorization_code',
        code: code,
        redirect_uri: process.env.DISCORD_REDIRECT_URI
      })
    });

    const tokenData = await tokenRes.json();
    if (!tokenData.access_token) return res.redirect('/?error=no_token');

    // Obtener datos del usuario
    const userRes = await fetch('https://discord.com/api/users/@me', {
      headers: { Authorization: `Bearer ${tokenData.access_token}` }
    });
    const userData = await userRes.json();

    if (!userData.id) return res.redirect('/?error=no_user');

    // Descargar y guardar avatar localmente
    const localAvatarPath = await saveDiscordAvatar(userData.id, userData.avatar, userData.username);

    const firmas_old = readJSON(FIRMAS_OLD_PATH);
    const firmas = readJSON(FIRMAS_PATH);
    const now = new Date().toISOString();

    let action = 'nueva';
    let message = '¡Gracias por firmar!';
    let signedUser = null;

    // Verificar en firmas_old
    const oldIndex = firmas_old.findIndex(f => f.discord_id === userData.id || f.username.toLowerCase() === (userData.username || '').toLowerCase());
    if (oldIndex !== -1) {
      const oldEntry = firmas_old.splice(oldIndex, 1)[0];
      signedUser = {
        nombre: oldEntry.nombre || userData.global_name || userData.username,
        username: oldEntry.username || userData.username,
        avatar: localAvatarPath,
        discord_id: userData.id,
        fecha: now,
        fecha_original: oldEntry.fecha,
        rol: oldEntry.rol || 'Miembro'
      };
      firmas.push(signedUser);
      writeJSON(FIRMAS_OLD_PATH, firmas_old);
      writeJSON(FIRMAS_PATH, firmas);
      action = 'actualizada';
      message = '¡Gracias por firmar otra vez! Tu firma ha sido actualizada.';
    } else {
      // Verificar en firmas
      const existIndex = firmas.findIndex(f => f.discord_id === userData.id);
      if (existIndex !== -1) {
        firmas[existIndex].fecha = now;
        firmas[existIndex].avatar = localAvatarPath; // Actualizar avatar local
        writeJSON(FIRMAS_PATH, firmas);
        signedUser = firmas[existIndex];
        action = 'actualizada';
        message = '¡Gracias por firmar otra vez! Se actualizó la fecha de tu firma.';
      } else {
        signedUser = {
          nombre: userData.global_name || userData.username,
          username: userData.username,
          avatar: localAvatarPath,
          discord_id: userData.id,
          fecha: now,
          rol: 'Miembro'
        };
        firmas.push(signedUser);
        writeJSON(FIRMAS_PATH, firmas);
      }
    }

    // Redirigir al frontend con parámetros
    const params = new URLSearchParams({
      discord_id: userData.id,
      username: userData.username,
      avatar: localAvatarPath,
      action: action,
      message: message
    });
    res.redirect('/?' + params.toString());

  } catch (err) {
    console.error('OAuth error:', err);
    res.redirect('/?error=oauth_failed');
  }
});

// ─── GET /api/health ────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    discord_configured: !!(process.env.DISCORD_CLIENT_ID && process.env.DISCORD_CLIENT_SECRET),
    discord_client_id: process.env.DISCORD_CLIENT_ID || '',  // Público, solo el ID
    avatar_dir: AVATAR_DIR,
    firmas_old_count: readJSON(FIRMAS_OLD_PATH).length,
    firmas_count: readJSON(FIRMAS_PATH).length
  });
});

// ─── Start ──────────────────────────────────────────────────

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`🟢 API corriendo en http://localhost:${PORT}`);
  console.log(`📄 Página en http://localhost:${PORT}/resolucion-caso-armin.html`);
  console.log(`📁 Avatares guardados en: ${AVATAR_DIR}`);
  console.log(`🔑 Discord OAuth: ${process.env.DISCORD_CLIENT_ID ? 'Configurado ✓' : 'NO configurado ✗'}`);
});
