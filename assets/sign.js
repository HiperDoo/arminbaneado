// Modal de ban: firma por trazos + "No soy Armin" (Turnstile invisible ligado a un nonce) + envío a la API.

const $ = (s, r = document) => r.querySelector(s);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const INK = "#EFEFF1";

let getStroke = null;
import("https://cdn.jsdelivr.net/npm/perfect-freehand@1.2.2/+esm")
    .then((m) => { getStroke = m.getStroke; pad?.redraw(); })
    .catch(() => { /* sin la librería se dibuja con líneas */ });

/* ---------------- Dibujo ---------------- */

function sizeCanvas(canvas) {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: r.width, h: r.height };
}

function drawStrokes(ctx, strokes, w, h, simulatePressure = true) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = ctx.strokeStyle = INK;
    ctx.lineCap = ctx.lineJoin = "round";
    const size = Math.max(3, w / 140);
    for (const s of strokes) {
        if (!s.length) continue;
        const pts = s.map(([x, y, , p]) => [x * w, y * h, p ?? 0.5]);
        if (getStroke) {
            const o = getStroke(pts, { size, thinning: 0.55, smoothing: 0.5, streamline: 0.45, simulatePressure, last: true });
            if (!o.length) continue;
            const path = new Path2D();
            path.moveTo(o[0][0], o[0][1]);
            for (let i = 1; i < o.length; i++) {
                const [x0, y0] = o[i - 1], [x1, y1] = o[i];
                path.quadraticCurveTo(x0, y0, (x0 + x1) / 2, (y0 + y1) / 2);
            }
            path.closePath();
            ctx.fill(path);
        } else {
            ctx.lineWidth = size;
            ctx.beginPath();
            pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
            ctx.stroke();
        }
    }
}

export function replay(canvas, strokes, maxMs = 2000) {
    const { ctx, w, h } = sizeCanvas(canvas);
    const flat = strokes.flat();
    if (!flat.length) return;
    if (reduceMotion) return drawStrokes(ctx, strokes, w, h);
    const t0 = flat[0][2], total = flat[flat.length - 1][2] - t0;
    const k = Math.max(2.5, total / maxMs);
    const start = performance.now();
    const frame = (now) => {
        const t = t0 + (now - start) * k;
        drawStrokes(ctx, strokes.map((s) => s.filter((p) => p[2] <= t)).filter((s) => s.length), w, h);
        if (t < t0 + total) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
}

/* ---------------- Validación (espejo de la del servidor) ---------------- */

function check(strokes) {
    const pts = strokes.flat();
    if (!pts.length) return { ok: false, msg: "" };
    if (pts.length < 20) return { ok: false, msg: "Sigue dibujando" };
    if (pts[pts.length - 1][2] - pts[0][2] < 400) return { ok: false, msg: "Dibújala con más calma" };
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    if (Math.max(...xs) - Math.min(...xs) < 0.15 || Math.max(...ys) - Math.min(...ys) < 0.1) return { ok: false, msg: "Usa más espacio del recuadro" };
    const n = pts.length, mx = xs.reduce((a, b) => a + b) / n, my = ys.reduce((a, b) => a + b) / n;
    let sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
    const tr = (sxx + syy) / n, det = (sxx * syy - sxy * sxy) / (n * n), d = Math.sqrt(Math.max(tr * tr / 4 - det, 0));
    if (Math.sqrt(Math.max(tr / 2 - d, 0) / (tr / 2 + d)) < 0.03) return { ok: false, msg: "Una línea recta no es una firma" };
    return { ok: true, msg: "Firma lista" };
}

/* ---------------- Pad ---------------- */

class Pad {
    constructor(root, canvas, onChange) {
        Object.assign(this, { root, canvas, onChange, strokes: [], t0: null, pointer: null, pen: false });
        canvas.addEventListener("pointerdown", (e) => this.down(e));
        canvas.addEventListener("pointermove", (e) => this.move(e));
        canvas.addEventListener("pointerup", (e) => this.up(e));
        canvas.addEventListener("pointercancel", (e) => this.up(e));
        new ResizeObserver(() => this.resize()).observe(canvas);
    }
    resize() { Object.assign(this, sizeCanvas(this.canvas)); this.redraw(); }
    pt(e) {
        const r = this.canvas.getBoundingClientRect();
        this.t0 ??= e.timeStamp;
        const clamp = (v) => Math.min(1, Math.max(0, v));
        const p = e.pointerType === "pen" && e.pressure > 0 ? e.pressure : 0.5;
        return [+clamp((e.clientX - r.left) / r.width).toFixed(4), +clamp((e.clientY - r.top) / r.height).toFixed(4), Math.round(e.timeStamp - this.t0), +p.toFixed(2)];
    }
    down(e) {
        if (this.pointer !== null) return; // un dedo a la vez
        e.preventDefault();
        this.pointer = e.pointerId;
        this.canvas.setPointerCapture(e.pointerId);
        this.pen ||= e.pointerType === "pen";
        this.strokes.push([this.pt(e)]);
        this.root.dataset.ink = "";
        this.root.dataset.active = "";
        this.redraw();
    }
    move(e) {
        if (e.pointerId !== this.pointer) return;
        e.preventDefault();
        const s = this.strokes[this.strokes.length - 1];
        for (const ev of e.getCoalescedEvents?.() || [e]) {
            const p = this.pt(ev), last = s[s.length - 1];
            p[2] = Math.max(p[2], last[2]);
            if (Math.abs(p[0] - last[0]) + Math.abs(p[1] - last[1]) > 0.0015) s.push(p);
        }
        this.redraw();
    }
    up(e) {
        if (e.pointerId !== this.pointer) return;
        this.pointer = null;
        delete this.root.dataset.active;
        this.onChange();
    }
    redraw() { if (this.ctx) drawStrokes(this.ctx, this.strokes, this.w, this.h, !this.pen); }
    undo() { this.strokes.pop(); this.strokes.length ? (this.redraw(), this.onChange()) : this.clear(); }
    clear() {
        Object.assign(this, { strokes: [], t0: null, pen: false });
        delete this.root.dataset.ink;
        this.redraw();
        this.onChange();
    }
}

/* ---------------- Select de Discord ---------------- */

function wireSelect(root) {
    const btn = $(".d-select-btn", root), list = $(".d-select-list", root), value = $("#historyValue", root);
    const opts = [...list.querySelectorAll(".d-select-opt")];
    let active = 0;
    const setActive = (i) => { active = (i + opts.length) % opts.length; opts.forEach((o, j) => (j === active ? (o.dataset.active = "") : delete o.dataset.active)); };
    const open = () => { list.hidden = false; btn.setAttribute("aria-expanded", "true"); setActive(opts.findIndex((o) => o.getAttribute("aria-selected") === "true")); list.focus(); };
    const close = (focus = true) => { list.hidden = true; btn.setAttribute("aria-expanded", "false"); if (focus) btn.focus(); };
    const choose = (i) => { opts.forEach((o, j) => o.setAttribute("aria-selected", String(j === i))); value.textContent = opts[i].firstChild.textContent; close(); };
    btn.addEventListener("click", () => (list.hidden ? open() : close()));
    opts.forEach((o, i) => o.addEventListener("click", () => choose(i)));
    list.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
        else if (e.key === "Tab") close(false);
    });
    document.addEventListener("pointerdown", (e) => { if (!root.contains(e.target) && !list.hidden) close(false); });
}

/* ---------------- Captcha ---------------- */

function waitForTurnstile(timeout = 8000) {
    return new Promise((resolve, reject) => {
        const t0 = Date.now();
        (function poll() {
            if (window.turnstile) return resolve(window.turnstile);
            if (Date.now() - t0 > timeout) return reject(new Error("No se pudo cargar la verificación. Revisa tu conexión o el bloqueador de anuncios."));
            setTimeout(poll, 100);
        })();
    });
}

/* ---------------- Modal ---------------- */

let pad, ctx, lastCheck = { ok: false };
const captcha = { state: "idle", token: null, nonce: null, widget: null };

function setCaptcha(state, error = "") {
    captcha.state = state;
    const el = $("#captcha");
    el.setAttribute("aria-checked", state === "verified" ? "true" : state === "verifying" ? "mixed" : "false");
    $("#captchaMeta").textContent = { verifying: "Verificando…", verified: "Verificado" }[state] || "Verificación con Cloudflare, sin acertijos";
    $("#captchaError").textContent = error;
    updateSubmit();
}

function resetCaptcha(error = "") {
    if (captcha.widget !== null && window.turnstile) { try { window.turnstile.remove(captcha.widget); } catch { /* ya no existe */ } }
    Object.assign(captcha, { token: null, nonce: null, widget: null });
    setCaptcha("idle", error);
}

async function toggleCaptcha() {
    if (captcha.state === "verifying") return;
    if (captcha.state === "verified") return resetCaptcha();
    setCaptcha("verifying");
    try {
        const ts = await waitForTurnstile();
        const res = await ctx.api("/api/desafio", { cache: "no-store" });
        if (res.status === 429) throw new Error("Demasiados intentos. Espera un minuto.");
        if (!res.ok) throw new Error("");
        captcha.nonce = (await res.json()).nonce;
        captcha.widget = ts.render("#turnstileSlot", {
            sitekey: ctx.config.turnstile_sitekey,
            action: "firmar",
            cData: captcha.nonce,
            execution: "execute",
            appearance: "interaction-only",
            theme: "dark",
            language: "es",
            callback: (token) => { captcha.token = token; setCaptcha("verified"); },
            "error-callback": () => { resetCaptcha("No pudimos verificar que eres humano. Inténtalo de nuevo."); return true; },
            "timeout-callback": () => resetCaptcha("La verificación tardó demasiado. Inténtalo de nuevo."),
            "expired-callback": () => resetCaptcha("La verificación caducó. Marca otra vez la casilla."),
        });
        ts.execute(captcha.widget);
    } catch (err) {
        resetCaptcha(err.message || "No pudimos conectar con el servidor. Inténtalo de nuevo.");
    }
}

function updateSubmit() {
    const btn = $("#banSubmit");
    if (btn.getAttribute("aria-busy") === "true") return;
    btn.disabled = !(lastCheck.ok && captcha.state === "verified");
}

function onPadChange() {
    lastCheck = check(pad.strokes);
    const hint = $("#padHint");
    hint.textContent = lastCheck.msg;
    lastCheck.ok ? (hint.dataset.ok = "") : delete hint.dataset.ok;
    $("#padUndo").disabled = $("#padClear").disabled = !pad.strokes.length;
    $("#formError").textContent = "";
    updateSubmit();
}

function showView(name) {
    document.querySelectorAll("#banModal [data-view]").forEach((v) => (v.hidden = v.dataset.view !== name));
}

async function submit() {
    const btn = $("#banSubmit"), strokes = pad.strokes;
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
    $("#formError").textContent = "";
    try {
        const res = await ctx.api("/api/firmar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ strokes, turnstile_token: captcha.token, nonce: captcha.nonce, device_id: ctx.deviceId, public_wall: false }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            const review = data.status === "review";
            $("#doneTitleText").textContent = review ? "Tu firma quedó en revisión" : "Armin fue baneado";
            $("#doneText").textContent = review
                ? "Se parece mucho a otra reciente. Alguien del equipo la revisará antes de sumarla."
                : `De tu corazón. En el servidor todavía no, pero tu firma nº ${new Intl.NumberFormat("es-ES").format(data.signature_no)} ya cuenta.`;
            showView("done");
            replay($("#replayCanvas"), strokes);
            ctx.onSigned(data);
            resetCaptcha();
            pad.clear();
            return;
        }
        const detail = typeof data.detail === "string" ? data.detail : "No se pudo registrar la firma. Inténtalo de nuevo.";
        if (res.status === 403) resetCaptcha(detail);
        else {
            if (res.status !== 422) resetCaptcha(); // el nonce ya se gastó en el servidor
            $("#formError").textContent = detail;
        }
        if (res.status === 429 && /firmaste/.test(detail)) ctx.onSigned({});
    } catch {
        $("#formError").textContent = "No pudimos conectar con el servidor. Tu firma no se envió. Inténtalo de nuevo.";
        resetCaptcha();
    } finally {
        btn.removeAttribute("aria-busy");
        updateSubmit();
    }
}

export function openBan() {
    const modal = $("#banModal");
    showView("form");
    modal.showModal();
    pad.resize();
    onPadChange();
}

export function initSign(context) {
    ctx = context;
    pad = new Pad($("#pad"), $("#padCanvas"), onPadChange);
    if ($("#historySelect")) wireSelect($("#historySelect"));
    $("#padUndo").addEventListener("click", () => pad.undo());
    $("#padClear").addEventListener("click", () => pad.clear());
    $("#captcha").addEventListener("click", toggleCaptcha);
    $("#banSubmit").addEventListener("click", submit);
    $("#banModal").addEventListener("cancel", (e) => { if ($("#banSubmit").getAttribute("aria-busy") === "true") e.preventDefault(); });
}

export const isSending = () => $("#banSubmit").getAttribute("aria-busy") === "true";
