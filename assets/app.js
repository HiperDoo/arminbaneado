// Caso Armin: arranque de la página hecha con piezas de Discord.
import { initSign, openBan, isSending, replay } from "./sign.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const nf = new Intl.NumberFormat("es-ES");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* modo privado */ } },
};

let deviceIdValue = null;
let config = {
    api_url: "https://arminserver.alwaysdata.net",
    turnstile_sitekey: "1x00000000000000000000AA",
    share_text: "Firma para pedir la expulsión del moderador Armin.",
};
let count = null;

async function loadConfig() {
    try {
        const res = await fetch("/config.json", { cache: "no-cache" });
        if (res.ok) config = { ...config, ...(await res.json()) };
    } catch { /* valores por defecto */ }
    if (["localhost", "127.0.0.1"].includes(location.hostname)) {
        config.api_url = `http://${location.hostname}:8000`;
        config.turnstile_sitekey = "1x00000000000000000000AA";
    }
}
const api = (path, opts = {}) => fetch(config.api_url.replace(/\/$/, "") + path, opts);

/* ---------------- Toast ---------------- */

let toastTimer;
function toast(msg, tone = "ok") {
    const el = $("#toast");
    $("#toastText").textContent = msg;
    el.dataset.tone = tone;
    el.querySelector("i").className = tone === "error" ? "ph-fill ph-warning-circle" : tone === "info" ? "ph-fill ph-info" : "ph-fill ph-check-circle";
    el.dataset.show = "";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => delete el.dataset.show, 3200);
}

/* ---------------- Tooltips (300 ms de espera; instantáneos entre vecinos) ---------------- */

function wireTooltips() {
    const tip = $("#tooltip");
    let showTimer, current = null, lastHidden = 0;
    const place = (target) => {
        tip.textContent = target.dataset.tip;
        const r = target.getBoundingClientRect();
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        let side = "top", top = r.top - th - 10;
        if (top < 8) { side = "bottom"; top = r.bottom + 10; }
        const left = Math.min(innerWidth - tw - 8, Math.max(8, r.left + r.width / 2 - tw / 2));
        tip.dataset.side = side;
        tip.style.setProperty("--tip-origin", side === "top" ? "bottom center" : "top center");
        tip.style.top = `${top}px`;
        tip.style.left = `${left}px`;
    };
    const show = (target) => {
        current = target;
        place(target);
        Date.now() - lastHidden < 400 ? (tip.dataset.instant = "") : delete tip.dataset.instant;
        tip.dataset.show = "";
    };
    const hide = () => {
        clearTimeout(showTimer);
        if (!current) return;
        current = null;
        lastHidden = Date.now();
        delete tip.dataset.show;
    };
    document.addEventListener("pointerover", (e) => {
        const t = e.target.closest?.("[data-tip]");
        if (!t || t === current) return;
        clearTimeout(showTimer);
        if (current || Date.now() - lastHidden < 400) show(t);
        else showTimer = setTimeout(() => show(t), 300);
    });
    document.addEventListener("pointerout", (e) => {
        const t = e.target.closest?.("[data-tip]");
        if (t && !t.contains(e.relatedTarget)) hide();
    });
    document.addEventListener("focusin", (e) => { const t = e.target.closest?.("[data-tip]"); if (t && t.matches(":focus-visible")) show(t); });
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", hide);
    addEventListener("scroll", hide, { passive: true });
    return { show, hide };
}

let tips;

/* ---------------- Fechas ---------------- */

function wireTimes() {
    const now = new Date();
    const hm = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit" }).format(now);
    const full = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(now);
    $$("[data-now]").forEach((t) => { t.textContent = `Hoy a las ${hm}`; t.dateTime = now.toISOString(); t.dataset.tip = full; });
    $$("[data-today]").forEach((s) => (s.textContent = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long", year: "numeric" }).format(now)));
}

/* ---------------- Perfil y popout ---------------- */

let profile = null;

function fillProfile(root, p) {
    $$('[data-field="display_name"]', root).forEach((e) => (e.textContent = p.display_name));
    $$('[data-field="username"]', root).forEach((e) => (e.textContent = p.username));
    const banner = $('[data-field="banner"]', root);
    if (banner) {
        banner.style.setProperty("--banner", p.banner_color || "#000");
        banner.style.backgroundImage = p.banner_url ? `url("${encodeURI(p.banner_url)}")` : "";
    }
    const avatar = $('[data-field="avatar"]', root);
    if (avatar && p.avatar_url) avatar.src = p.avatar_url;
    const roles = $('[data-field="roles"]', root);
    if (roles) {
        roles.replaceChildren(...(p.roles || []).map((r) => {
            const li = document.createElement("li");
            li.className = "d-role";
            const dot = document.createElement("span");
            dot.className = "d-role-dot";
            if (r.color) dot.style.setProperty("--role", r.color);
            li.append(dot, document.createTextNode(r.name));
            if (r.icon) {
                const img = new Image(14, 14);
                img.src = r.icon;
                img.alt = "";
                li.append(img);
            } else if (r.emoji) li.append(document.createTextNode(` ${r.emoji}`));
            return li;
        }));
    }
}

function applyProfile(p) {
    profile = p;
    fillProfile($("#profileSlot"), p);
    $$("[data-name]").forEach((e) => (e.textContent = p.display_name));
    $$("[data-username]").forEach((e) => (e.textContent = p.username));
    if (p.avatar_url) $$("img[data-avatar]").forEach((img) => (img.src = p.avatar_url));
}

async function loadProfile() {
    $("#profileSlot").append($("#profileTpl").content.cloneNode(true));
    let base = {};
    try {
        const res = await fetch("/data/perfil.json", { cache: "no-cache" });
        if (res.ok) base = await res.json();
    } catch { /* se queda lo del HTML */ }
    // Solo datos estáticos: la foto y el @ reales no se muestran.
    applyProfile(base);
}

/* ---------------- Encuesta = contador ---------------- */

const signed = () => store.get("armin_signed") === "1";

function renderPoll() {
    const poll = $("#poll");
    const yes = count ?? 0;
    const pct = yes ? 100 : 0;
    $('[data-votes="yes"]').textContent = `${nf.format(yes)} ${yes === 1 ? "voto" : "votos"}`;
    $('[data-pct="yes"]').textContent = `${pct} %`;
    const yesBtn = $('[data-answer="yes"]');
    yesBtn.querySelector(".d-poll-fill").style.setProperty("--pct", pct / 100);
    yes ? (yesBtn.dataset.winner = "") : delete yesBtn.dataset.winner;
    const shown = count === null ? "-" : nf.format(yes);
    $("#pollTotal").textContent = shown;
    $("#pollUnit").textContent = count === 1 ? "voto" : "votos";
    $("#mobileUnit").textContent = count === 1 ? "voto" : "votos";
    $("#mobileCount").textContent = shown;
    if (count !== null && count > 0) {
        $("#heroProof").hidden = false;
        $("#heroCount").textContent = nf.format(yes);
        $("#heroUnit").textContent = yes === 1 ? "persona ya votó" : "personas ya votaron";
    }
    if (signed()) {
        poll.dataset.mode = "results";
        yesBtn.dataset.mine = "";
        $("#pollHint").textContent = "Ya votaste";
        $("#pollVote").hidden = true;
        $("#pollToggle").hidden = true;
    }
}

function wirePoll() {
    const poll = $("#poll"), yes = $('[data-answer="yes"]'), no = $('[data-answer="no"]'), vote = $("#pollVote"), toggle = $("#pollToggle");
    yes.addEventListener("click", () => {
        if (poll.dataset.mode === "results") return;
        const on = yes.getAttribute("aria-checked") !== "true";
        yes.setAttribute("aria-checked", String(on));
        vote.disabled = !on;
    });
    no.addEventListener("click", () => {
        if (poll.dataset.mode === "results") return;
        tips.show(no);
        setTimeout(tips.hide, 1400);
    });
    vote.addEventListener("click", () => openSign());
    toggle.addEventListener("click", () => {
        const results = poll.dataset.mode !== "results";
        poll.dataset.mode = results ? "results" : "vote";
        toggle.textContent = results ? "Volver a votar" : "Mostrar resultados";
        vote.hidden = results;
    });
}

async function fetchCount() {
    try {
        const res = await api("/api/contador");
        if (!res.ok) throw new Error();
        count = (await res.json()).count;
    } catch {
        if (count === null) $("#pollHint").textContent = "No se pudieron cargar los votos ahora mismo";
    }
    renderPoll();
}

function pollCount() {
    fetchCount();
    let timer = setInterval(fetchCount, 30000);
    document.addEventListener("visibilitychange", () => {
        clearInterval(timer);
        if (!document.hidden) { fetchCount(); timer = setInterval(fetchCount, 30000); }
    });
}

/* ---------------- Motivos (data/mensajes.json) ---------------- */

function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
}

const FIRE = "🔥";
let reactionCounts = {};
const reacted = new Set(JSON.parse(store.get("armin_reacted") || "[]"));
const saveReacted = () => store.set("armin_reacted", JSON.stringify([...reacted]));

function paintReaction(btn) {
    const id = btn.dataset.message;
    const on = reacted.has(id);
    const n = Math.max(reactionCounts[id] || 0, on ? 1 : 0);
    btn.setAttribute("aria-pressed", String(on));
    btn.querySelector("[data-count]").textContent = String(n);
    btn.setAttribute("aria-label", `${n} ${n === 1 ? "reacción" : "reacciones"} con fuego${on ? ", incluida la tuya" : ""}`);
}

function reactionNode(messageId) {
    const b = el("button", "d-reaction");
    b.type = "button";
    b.dataset.message = messageId;
    b.dataset.tip = "Reaccionar con 🔥";
    const count = el("span");
    count.dataset.count = "";
    b.append(el("span", "d-reaction-emoji", FIRE), count);
    paintReaction(b);
    b.addEventListener("click", () => toggleReaction(b));
    return b;
}

async function toggleReaction(btn) {
    if (btn.dataset.busy) return;
    const id = btn.dataset.message, on = !reacted.has(id);
    const before = reactionCounts[id] || 0;
    // Optimista: se pinta ya y se corrige si la API falla.
    on ? reacted.add(id) : reacted.delete(id);
    reactionCounts[id] = Math.max(0, before + (on ? 1 : -1));
    paintReaction(btn);
    btn.dataset.busy = "";
    try {
        const res = await api("/api/reacciones", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message_id: id, device_id: deviceIdValue, on }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "");
        reactionCounts[id] = data.count;
        saveReacted();
    } catch (err) {
        on ? reacted.delete(id) : reacted.add(id);
        reactionCounts[id] = before;
        toast(err.message || "No se pudo guardar la reacción. Inténtalo de nuevo.", "error");
    } finally {
        delete btn.dataset.busy;
        paintReaction(btn);
    }
}

async function loadReactions() {
    try {
        const res = await api("/api/reacciones");
        if (res.ok) reactionCounts = (await res.json()).counts || {};
    } catch { /* se quedan a 0 */ }
    $$(".d-reaction[data-message]").forEach(paintReaction);
}

// {@Nombre} se pinta como una mención de Discord.
function richLine(line) {
    const p = el("p");
    line.split(/(\{@[^}]+\})/).forEach((part) => {
        const m = part.match(/^\{@([^}]+)\}$/);
        p.append(m ? el("span", "d-mention d-mention--static", `@${m[1]}`) : document.createTextNode(part));
    });
    return p;
}

function messageNode(m, prev) {
    // Discord agrupa mensajes seguidos del mismo autor enviados casi a la vez.
    const grouped = prev && prev.author === m.author && prev.time === m.time && !m.reply;
    const art = el("article", `d-msg${grouped ? " d-msg--grouped" : ""}${m.reply ? " d-msg--reply" : ""}`);
    if (m.reply) {
        const ref = el("p", "d-msg-ref");
        const img = new Image(16, 16);
        img.src = m.reply.avatar || "https://cdn.discordapp.com/embed/avatars/0.png";
        img.alt = "";
        const who = el("span", "d-msg-ref-user", `@${m.reply.author}`);
        if (m.reply.color) who.style.setProperty("--role", m.reply.color);
        ref.append(img, who, el("span", "d-msg-ref-text", m.reply.content));
        art.append(ref);
    }
    if (!grouped) {
        const av = el("img", "d-msg-avatar");
        av.src = m.avatar || "https://cdn.discordapp.com/embed/avatars/0.png";
        av.alt = "";
        av.loading = "lazy";
        const head = el("h3", "d-msg-head");
        const name = el("span", "d-msg-author", m.author);
        if (m.color) name.style.setProperty("--role", m.color);
        head.append(name);
        if (m.role_icon) { const ic = el("img", "d-msg-roleicon"); ic.src = m.role_icon; ic.alt = ""; head.append(ic); }
        if (m.tag) head.append(el("span", "d-server-tag", m.tag));
        const t = el("time", "d-msg-time", m.time || "Hoy");
        if (m.time_full) t.dataset.tip = m.time_full;
        head.append(t);
        art.append(av, head);
    }
    const body = el("div", "d-msg-body");
    String(m.content).split("\n").forEach((line) => body.append(richLine(line)));
    art.append(body);

    if (m.id) {
        const reactions = el("div", "d-reactions");
        reactions.append(reactionNode(m.id));
        art.append(reactions);
    }
    return art;
}

async function loadMessages() {
    const box = $("#messages");
    let list = [];
    try {
        const res = await fetch("/data/mensajes.json", { cache: "no-cache" });
        if (res.ok) list = (await res.json()).motivos || [];
    } catch { /* placeholder */ }
    if (!list.length) {
        const sk = () => {
            const d = el("div", "d-skeleton");
            d.setAttribute("aria-hidden", "true");
            const line = el("i");
            line.style.width = `${45 + Math.round(Math.random() * 35)}%`;
            d.append(el("i"), line);
            return d;
        };
        box.replaceChildren(...[0, 1, 2].map(() => { const c = el("article", "motivo"); c.append(sk()); return c; }));
        $("#motivosNote").hidden = false;
        return;
    }
    box.replaceChildren(...list.map((thread) => {
        const card = el("article", "motivo");
        thread.forEach((m, i) => card.append(messageNode(m, thread[i - 1])));
        return card;
    }));
}

/* ---------------- Firmar, compartir, modales ---------------- */

function markSigned() {
    store.set("armin_signed", "1");
    $$("[data-sign-label]").forEach((s) => (s.textContent = "Ya firmaste"));
    renderPoll();
}

function openSign() {
    if (signed()) { toast("Ya registramos tu firma desde este dispositivo.", "info"); return; }
    openBan();
}

function wireModals() {
    $$("dialog.d-modal").forEach((d) => {
        d.addEventListener("click", (e) => {
            if (d.id === "banModal" && isSending()) return;
            if (e.target === d || e.target.closest("[data-close]")) d.close();
        });
    });
    $$("[data-open]").forEach((b) => b.addEventListener("click", () => $(`#${b.dataset.open}`).showModal()));
}

function wireMobileBar() {
    const bar = $("#mobileBar");
    new IntersectionObserver(([e]) => {
        const show = !e.isIntersecting && e.boundingClientRect.top < 0;
        show ? (bar.dataset.show = "") : delete bar.dataset.show;
        bar.setAttribute("aria-hidden", String(!show));
        bar.querySelector("button").tabIndex = show ? 0 : -1;
    }).observe($("#heroActions"));
}

/* ---------------- Landing: nav, revelado, demos del bento ---------------- */

function wireNav() {
    const nav = $("#nav");
    addEventListener("scroll", () => (scrollY > 8 ? (nav.dataset.scrolled = "") : delete nav.dataset.scrolled), { passive: true });
    const links = $$("[data-nav]");
    const io = new IntersectionObserver((entries) => {
        entries.forEach((e) => {
            if (!e.isIntersecting) return;
            links.forEach((a) => a.setAttribute("aria-current", String(a.dataset.nav === e.target.id)));
        });
    }, { rootMargin: "-45% 0px -50% 0px" });
    links.forEach((a) => { const sec = document.getElementById(a.dataset.nav); if (sec) io.observe(sec); });
    io.observe($("#top")); // en el hero no se marca ninguna sección
}

function wireReveal() {
    const io = new IntersectionObserver((entries) => {
        entries.forEach((e) => { if (e.isIntersecting) { e.target.dataset.in = ""; io.unobserve(e.target); } });
    }, { threshold: 0.15 });
    $$(".reveal").forEach((n) => io.observe(n));
}

async function wireDemoSignature() {
    const canvas = $("#demoCanvas");
    let strokes;
    try { strokes = (await (await fetch("/data/firma-ejemplo.json")).json()).strokes; } catch { return; }
    const io = new IntersectionObserver(([e]) => {
        if (!e.isIntersecting) return;
        io.disconnect();
        replay(canvas, strokes, 2400);
    }, { threshold: 0.6 });
    io.observe(canvas);
}

function wireDemoCheck() {
    const box = $("#demoCheck"), meta = $("#demoMeta");
    box.addEventListener("click", () => {
        const state = box.getAttribute("aria-checked");
        if (state === "mixed") return;
        if (state === "true") { box.setAttribute("aria-checked", "false"); meta.textContent = ""; return; }
        box.setAttribute("aria-checked", "mixed");
        meta.textContent = "Verificando…";
        setTimeout(() => { box.setAttribute("aria-checked", "true"); meta.textContent = "Eres humano"; }, reduceMotion ? 0 : 900);
    });
}

/* ---------------- Arranque ---------------- */

async function init() {
    await loadConfig();
    let deviceId = store.get("armin_device");
    if (!deviceId) { deviceId = crypto.randomUUID(); store.set("armin_device", deviceId); }
    deviceIdValue = deviceId;

    wireTimes();
    wireNav();
    wireReveal();
    wireDemoCheck();
    wireDemoSignature();
    tips = wireTooltips();
    wireModals();
    wirePoll();
    wireMobileBar();
    initSign({
        api, config, deviceId,
        onSigned(data) {
            if (typeof data.count === "number") count = data.count;
            markSigned();
        },
    });

    // Delegación: también cubre los botones de la tarjeta de perfil clonada.
    document.addEventListener("click", (e) => {
        if (e.target.closest("[data-sign]")) openSign();
    });

    if (signed()) markSigned();
    pollCount();
    loadProfile();
    loadMessages().then(loadReactions);
}

init();
