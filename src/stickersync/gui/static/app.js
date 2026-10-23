"use strict";

/* ------------------------------------------------------------------ setup */

const TOKEN = (() => {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("t");
  try {
    if (fromHash) sessionStorage.setItem("stickersync-token", fromHash);
    return fromHash || sessionStorage.getItem("stickersync-token") || "";
  } catch (_) {
    return fromHash || "";
  }
})();
if (location.hash) history.replaceState(null, "", location.pathname);

const $ = (id) => document.getElementById(id);
const ui = {
  peel: $("peel"), bubble: $("bubble"), syncBtn: $("sync-btn"), syncLabel: $("sync-label"),
  captureBtn: $("capture-btn"), job: $("job"), jobTitle: $("job-title"), jobPrompt: $("job-prompt"),
  jobContinue: $("job-continue"), jobLog: $("job-log"), slots: $("slots"), slotsCount: $("slots-count"),
  slotsFill: $("slots-fill"), slotsBatch: $("slots-batch"), slotsNote: $("slots-note"),
  grid: $("grid"), empty: $("empty"), selCount: $("sel-count"), selectAll: $("select-all"),
  activity: $("activity"), chipTiktok: $("chip-tiktok"), chipDiscord: $("chip-discord"),
  settings: $("settings"), setToken: $("set-token"), setGuild: $("set-guild"),
  settingsError: $("settings-error"), toast: $("toast"),
};

const store = {
  status: null,
  stickers: [],
  selected: new Set(),
  seen: new Set(),
  filter: "new",
  fmt: "auto",
  rotate: false,
  dry: false,
  previews: {},      // sticker id -> {short, ext, kb, animated}
  job: null,
  lastError: null,
};

/* ------------------------------------------------------------------ helpers */

async function api(path, body) {
  const opts = { headers: { "X-StickerSync-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function ago(iso) {
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
  if (!t) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  if (d === 1) return "yesterday";
  if (d < 7) return `${d} days ago`;
  if (d < 14) return "last week";
  return `${Math.floor(d / 7)} weeks ago`;
}

let toastTimer;
function toast(msg) {
  ui.toast.textContent = msg;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 4200);
}

const imgUrl = (kind, id) => `/api/${kind}/${encodeURIComponent(id)}?t=${encodeURIComponent(TOKEN)}`;
const TILES = ["#FFEAF2", "#FFF6D9", "#E5F8F0", "#EEF9E4", "#E6F1FF", "#F1ECFF", "#FFEDE6", "#FFE6EB"];

/* ------------------------------------------------------------------ Peel */

const BODY = "M84,46 H124 L164,86 V126 C164,152 148,174 120,174 H80 C52,174 36,152 36,126 V94 C36,66 56,46 84,46 Z";
const FLAP = "M124,46 L164,86 C150,90 138,86 132,78 C125,68 122,58 124,46 Z";

function peelFace(mood) {
  if (mood === "excited") {
    return `<path d="M80,98 Q82,110 94,112 Q82,114 80,126 Q78,114 66,112 Q78,110 80,98 Z" fill="#2B2340"/>
      <path d="M120,98 Q122,110 134,112 Q122,114 120,126 Q118,114 106,112 Q118,110 120,98 Z" fill="#2B2340"/>
      <ellipse cx="100" cy="134" rx="6" ry="7" fill="#2B2340"/>`;
  }
  if (mood === "oops") {
    return `<path d="M72,104 L86,112 L72,120" fill="none" stroke="#2B2340" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M128,104 L114,112 L128,120" fill="none" stroke="#2B2340" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M92,134 Q96,130 100,134 Q104,138 108,134" fill="none" stroke="#2B2340" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M150,96 C154,104 156,108 156,111 C156,114.5 153.3,117 150,117 C146.7,117 144,114.5 144,111 C144,108 146,104 150,96 Z" fill="#8FD3FF" stroke="#fff" stroke-width="2.5"/>`;
  }
  return `<ellipse cx="80" cy="112" rx="11" ry="14" fill="#2B2340"/><ellipse cx="120" cy="112" rx="11" ry="14" fill="#2B2340"/>
    <circle cx="84" cy="106" r="4.5" fill="#fff"/><circle cx="124" cy="106" r="4.5" fill="#fff"/>
    <circle cx="77" cy="118" r="2" fill="#fff"/><circle cx="117" cy="118" r="2" fill="#fff"/>
    <path d="M92,129 Q96,134 100,129 Q104,134 108,129" fill="none" stroke="#2B2340" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`;
}

function peelSvg(mood, label) {
  return `<svg viewBox="0 30 200 170" role="img" aria-label="${esc(label)}">
    <ellipse cx="100" cy="190" rx="50" ry="6" fill="#241E36" opacity="0.10"/>
    <ellipse cx="78" cy="180" rx="13" ry="8" fill="#A893F5" stroke="#fff" stroke-width="4"/>
    <ellipse cx="122" cy="180" rx="13" ry="8" fill="#A893F5" stroke="#fff" stroke-width="4"/>
    <path d="${BODY}" transform="translate(0 4)" fill="#241E36" opacity="0.08"/>
    <path d="${BODY}" fill="#C5B3FF" stroke="#fff" stroke-width="7" stroke-linejoin="round"/>
    <path d="${FLAP}" fill="#F4F0FF" stroke="#fff" stroke-width="4" stroke-linejoin="round"/>
    <path d="M62,58 l4,8 9,1 -7,6 2,9 -8,-5 -8,5 2,-9 -7,-6 9,-1 z" fill="#2EC4A0" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>
    ${peelFace(mood)}
    <ellipse cx="62" cy="133" rx="9" ry="5" fill="#FF7EB0" opacity="0.55"/>
    <ellipse cx="138" cy="133" rx="9" ry="5" fill="#FF7EB0" opacity="0.55"/>
    <ellipse cx="82" cy="155" rx="7" ry="6" fill="#C5B3FF" stroke="#A893F5" stroke-width="2"/>
    <ellipse cx="118" cy="155" rx="7" ry="6" fill="#C5B3FF" stroke="#A893F5" stroke-width="2"/>
  </svg>`;
}

function brandIcon() {
  return `<svg viewBox="30 40 140 140" width="34" height="34" aria-hidden="true">
    <path d="${BODY}" fill="#E4DAFF" stroke="#fff" stroke-width="9" stroke-linejoin="round"/>
    <path d="${FLAP}" fill="#B9A6FF"/>
    <ellipse cx="80" cy="112" rx="12" ry="15" fill="#2B2340"/><ellipse cx="120" cy="112" rx="12" ry="15" fill="#2B2340"/>
    <circle cx="84" cy="105" r="5" fill="#fff"/><circle cx="124" cy="105" r="5" fill="#fff"/></svg>`;
}

let lastPeel = "";
function setPeel(mood, text, bounce) {
  const key = mood + bounce;
  if (key !== lastPeel) {
    ui.peel.innerHTML = peelSvg(mood, `Peel looks ${mood === "oops" ? "flustered" : mood === "excited" ? "excited" : "happy"}`);
    ui.peel.classList.toggle("bounce", !!bounce);
    lastPeel = key;
  }
  ui.bubble.textContent = text;
}

/* ------------------------------------------------------------------ derived */

const selectable = (s) => s.status === "new" || s.status === "failed";
const busy = () => store.job && (store.job.status === "running" || store.job.status === "waiting");
const discord = () => (store.status && store.status.discord) || { configured: false };
const loggedIn = () => !!(store.status && store.status.tiktok.logged_in);
const visible = () => store.filter === "all" ? store.stickers : store.stickers.filter(selectable);
const chosen = () => store.stickers.filter((s) => store.selected.has(s.id) && selectable(s));

/* ------------------------------------------------------------------ render */

function render() {
  renderChips();
  renderPeelAndActions();
  renderSlots();
  renderGrid();
  renderActivity();
  renderJob();
}

function renderChips() {
  const st = store.status;
  if (!st) return;
  const tk = ui.chipTiktok;
  tk.className = "chip " + (loggedIn() ? "ok" : "warn");
  tk.querySelector(".chip-label").textContent = loggedIn() ? "TikTok logged in" : "Log in to TikTok";
  const d = discord();
  const dc = ui.chipDiscord;
  if (!d.configured) {
    dc.className = "chip warn";
    dc.querySelector(".chip-label").textContent = "Set up Discord";
  } else if (d.error) {
    dc.className = "chip warn";
    dc.querySelector(".chip-label").textContent = "Discord: check settings";
    dc.title = d.error;
  } else {
    dc.className = "chip";
    dc.querySelector(".chip-label").textContent = `Discord · ${d.guild_name}`;
    dc.title = "";
  }
}

function renderPeelAndActions() {
  const n = chosen().length;
  const newCount = store.stickers.filter((s) => s.status === "new").length;
  const d = discord();
  const job = store.job;

  if (job && job.status === "waiting") {
    setPeel("excited", "Your turn! Follow the steps below, then tap “I'm done”.", true);
  } else if (busy()) {
    const text = { login: "Opening TikTok so you can log in…", capture: "Peeking at your TikTok favorites…", sync: store.dry ? "Converting your stickers…" : "Converting and uploading — hang tight!" }[job.kind] || "Working on it…";
    setPeel("excited", text, true);
  } else if (store.lastError) {
    setPeel("oops", `Oops! ${store.lastError}`, false);
  } else if (!loggedIn()) {
    setPeel("happy", "Hi, I'm Peel! Log in to TikTok so I can find your stickers.", false);
  } else if (!store.status.source.name) {
    setPeel("happy", "Let's check TikTok for your favorite stickers!", false);
  } else if (newCount > 0) {
    setPeel("excited", `${newCount} new favorite${newCount === 1 ? "" : "s"} since last time!`, false);
  } else {
    setPeel("happy", "All caught up! Nothing new to sync.", false);
  }

  // main button
  let label;
  let disabled = busy() || n === 0;
  if (!store.dry && !d.configured) {
    label = "Set up Discord first";
    disabled = busy();
  } else if (n === 0) {
    label = store.stickers.length ? "Pick some stickers" : "Nothing to sync yet";
  } else {
    label = `${store.dry ? "Preview" : "Sync"} ${n} sticker${n === 1 ? "" : "s"}`;
  }
  ui.syncLabel.textContent = label;
  ui.syncBtn.disabled = disabled;

  ui.captureBtn.disabled = busy();
  ui.captureBtn.lastChild.textContent = loggedIn() ? " Check TikTok for new favorites" : " Log in to TikTok";

  ui.selCount.textContent = store.stickers.length ? `${n} selected` : "";
  const sel = visible().filter(selectable);
  ui.selectAll.hidden = sel.length === 0;
  ui.selectAll.textContent = sel.length && sel.every((s) => store.selected.has(s.id)) ? "Clear all" : "Select all";
}

function renderSlots() {
  const d = discord();
  if (!d.configured || d.error || d.slot_limit == null) { ui.slots.hidden = true; return; }
  ui.slots.hidden = false;
  const used = d.slots_used, limit = d.slot_limit, n = store.dry ? 0 : chosen().length;
  const free = Math.max(limit - used, 0);
  ui.slotsCount.textContent = `${used} / ${limit} used`;
  ui.slotsFill.style.width = `${Math.min(100, (used / limit) * 100)}%`;
  const batch = Math.min(n, free);
  ui.slotsBatch.style.left = `${(used / limit) * 100}%`;
  ui.slotsBatch.style.width = `${(batch / limit) * 100}%`;
  let note;
  if (n === 0) note = `${free} free`;
  else if (n <= free) note = `${free} free — enough for this batch.`;
  else if (store.rotate) note = `${free} free — ${n - free} oldest synced sticker${n - free === 1 ? "" : "s"} will be swapped out.`;
  else note = `Only ${free} free — ${n - free} will wait. Turn on “Make room automatically” to swap old ones out.`;
  ui.slotsNote.textContent = note;
}

function badgeFor(s) {
  const p = store.previews[s.id];
  if (p) return { cls: "", text: `${p.animated ? p.ext === "gif" ? "GIF" : "APNG" : "PNG"} · ${p.kb} KB` };
  return {
    new: { cls: "", text: s.animated ? "New · animated" : "New" },
    uploaded: { cls: "uploaded", text: "On your server" },
    failed: { cls: "failed", text: "Failed — will retry" },
    skipped: { cls: "skipped", text: "Too big to fit" },
    removed: { cls: "removed", text: "Rotated out" },
  }[s.status] || { cls: "", text: s.status };
}

function renderGrid() {
  const st = store.status;
  const items = visible();
  if (!st) return;
  if (!st.source.name || items.length === 0) {
    ui.grid.innerHTML = "";
    ui.grid.hidden = true;
    ui.empty.hidden = false;
    const none = !st.source.name;
    const allDone = store.stickers.length > 0;
    ui.empty.innerHTML = `
      <div class="peel" style="flex-basis:auto">${peelSvg(none ? "happy" : "happy", "Peel")}</div>
      <h3>${none ? "No favorites yet" : allDone ? "You're all caught up" : "No stickers found"}</h3>
      <p>${none
        ? "Tap “Check TikTok for new favorites”. A browser opens, you open the Favorites tab in the sticker picker, and I'll grab the list."
        : allDone ? "Every favorite is already on your server. Switch to “All” to see them." : "The last check didn't find any stickers. Try checking TikTok again."}</p>`;
    return;
  }
  ui.empty.hidden = true;
  ui.grid.hidden = false;
  ui.grid.innerHTML = items.map((s, i) => {
    const can = selectable(s);
    const on = can && store.selected.has(s.id);
    const b = badgeFor(s);
    const p = store.previews[s.id];
    const src = p ? imgUrl("converted", p.short) + `&v=${p.kb}` : imgUrl("original", s.id);
    return `<button type="button" class="sticker" data-id="${esc(s.id)}" aria-pressed="${on}" ${can ? "" : "disabled"}>
      <span class="thumb" style="background:${TILES[i % TILES.length]}"><img src="${src}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'fallback',textContent:'no preview'}))"></span>
      <span class="sticker-name" title="${esc(s.name)}">${esc(s.name)}</span>
      <span class="badge ${b.cls}">${esc(b.text)}</span>
      <span class="check" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5L20 7"/></svg></span>
    </button>`;
  }).join("");
}

const ACT_ICONS = {
  uploaded: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  removed: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  failed: '<path d="M12 8v5M12 17h0"/><circle cx="12" cy="12" r="9"/>',
  skipped: '<path d="M12 8v5M12 17h0"/><circle cx="12" cy="12" r="9"/>',
};
const ACT_VERB = { uploaded: "Uploaded", removed: "Rotated out", failed: "Couldn't sync", skipped: "Skipped" };

function renderActivity() {
  const hist = (store.status && store.status.history) || [];
  if (!hist.length) {
    ui.activity.innerHTML = '<li><span class="act-text muted">Nothing yet — your syncs will show up here.</span></li>';
    return;
  }
  ui.activity.innerHTML = hist.map((h) => `<li>
    <span class="act-icon ${esc(h.status)}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ACT_ICONS[h.status] || ACT_ICONS.failed}</svg></span>
    <span class="act-text">${esc(ACT_VERB[h.status] || h.status)} ${esc(h.name)}${h.detail && h.status !== "uploaded" ? `<small>${esc(h.detail)}</small>` : ""}</span>
    <span class="act-time">${esc(ago(h.at))}</span>
  </li>`).join("");
}

function renderJob() {
  const job = store.job;
  if (!job) { ui.job.hidden = true; return; }
  ui.job.hidden = false;
  ui.job.classList.toggle("done", job.status === "done");
  ui.job.classList.toggle("error", job.status === "error");
  const names = { login: "Logging in to TikTok", capture: "Checking your favorites", sync: "Syncing stickers" };
  const suffix = { waiting: " — your turn", done: " — done", error: " — something went wrong" }[job.status] || "…";
  ui.jobTitle.textContent = (names[job.kind] || "Working") + suffix;
  ui.jobPrompt.hidden = !job.prompt;
  ui.jobPrompt.textContent = job.prompt || "";
  ui.jobContinue.hidden = job.status !== "waiting";
  ui.jobLog.textContent = job.log.join("\n");
  ui.jobLog.scrollTop = ui.jobLog.scrollHeight;
}

/* ------------------------------------------------------------------ data */

async function loadStatus(refresh) {
  try {
    store.status = await api("/api/status" + (refresh ? "?refresh=1" : ""));
  } catch (e) {
    toast(e.message);
  }
}

async function loadStickers() {
  try {
    const { stickers } = await api("/api/stickers");
    store.stickers = stickers;
    for (const s of stickers) {
      if (!store.seen.has(s.id)) {
        store.seen.add(s.id);
        if (s.status === "new") store.selected.add(s.id); // new arrivals start selected
      }
    }
  } catch (e) {
    toast(e.message);
  }
}

async function refreshAll(refreshDiscord) {
  await Promise.all([loadStatus(refreshDiscord), loadStickers()]);
  render();
}

let polling = false;
async function pollJob() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      const { job } = await api("/api/job");
      store.job = job;
      render();
      if (!job || (job.status !== "running" && job.status !== "waiting")) break;
      await new Promise((r) => setTimeout(r, 700));
    }
    const job = store.job;
    if (job && job.status === "error") store.lastError = job.error;
    if (job && job.status === "done" && job.kind === "sync" && job.result) {
      for (const p of job.result.prepared || []) store.previews[p.id] = p;
      const r = job.result;
      if (r.dry_run) toast(`Converted ${r.prepared.length} — previews are on the cards.`);
      else toast(`Uploaded ${r.uploaded.length}${r.waiting.length ? `, ${r.waiting.length} waiting for slots` : ""}${r.failed.length ? `, ${r.failed.length} failed` : ""}.`);
    }
    if (job && job.status === "done" && job.kind === "capture" && job.result) toast(`Found ${job.result.found} favorite sticker${job.result.found === 1 ? "" : "s"}.`);
    await refreshAll(true);
  } catch (e) {
    toast(e.message);
  } finally {
    polling = false;
  }
}

async function startJob(path, body) {
  store.lastError = null;
  try {
    const { job } = await api(path, body || {});
    store.job = job;
    render();
    pollJob();
  } catch (e) {
    store.lastError = e.message;
    render();
  }
}

/* ------------------------------------------------------------------ events */

ui.grid.addEventListener("click", (ev) => {
  const card = ev.target.closest(".sticker");
  if (!card || card.disabled) return;
  const id = card.dataset.id;
  if (store.selected.has(id)) store.selected.delete(id); else store.selected.add(id);
  card.setAttribute("aria-pressed", String(store.selected.has(id)));
  renderPeelAndActions();
  renderSlots();
});

ui.selectAll.addEventListener("click", () => {
  const sel = visible().filter(selectable);
  const all = sel.every((s) => store.selected.has(s.id));
  for (const s of sel) all ? store.selected.delete(s.id) : store.selected.add(s.id);
  render();
});

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  store.filter = b.dataset.filter;
  document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  render();
}));

document.querySelectorAll("#formats button").forEach((b) => b.addEventListener("click", () => {
  store.fmt = b.dataset.fmt;
  store.previews = {}; // previews were for the old format
  document.querySelectorAll("#formats button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  render();
}));

function bindSwitch(el, key) {
  el.addEventListener("click", () => {
    store[key] = !store[key];
    el.setAttribute("aria-checked", String(store[key]));
    render();
  });
}
bindSwitch($("opt-rotate"), "rotate");
bindSwitch($("opt-dry"), "dry");

ui.syncBtn.addEventListener("click", () => {
  if (!store.dry && !discord().configured) return openSettings();
  const ids = chosen().map((s) => s.id);
  if (!ids.length) return;
  startJob("/api/sync", { ids, format: store.fmt, rotate: store.rotate, dry_run: store.dry });
});

ui.captureBtn.addEventListener("click", () => startJob(loggedIn() ? "/api/capture" : "/api/login"));
ui.chipTiktok.addEventListener("click", () => { if (!busy()) startJob(loggedIn() ? "/api/capture" : "/api/login"); });
ui.chipDiscord.addEventListener("click", openSettings);
$("open-settings").addEventListener("click", openSettings);
ui.jobContinue.addEventListener("click", async () => {
  ui.jobContinue.disabled = true;
  try { await api("/api/job/continue", {}); } catch (e) { toast(e.message); }
  ui.jobContinue.disabled = false;
});

function openSettings() {
  ui.settingsError.hidden = true;
  ui.setToken.value = "";
  const d = discord();
  ui.setToken.placeholder = d.configured ? "Saved — leave blank to keep it" : "Paste your bot token";
  ui.setGuild.value = d.guild_id || "";
  ui.settings.showModal();
}

$("settings-form").addEventListener("submit", async (ev) => {
  if (ev.submitter && ev.submitter.value !== "save") return;
  ev.preventDefault();
  const save = $("settings-save");
  save.disabled = true;
  try {
    const body = { guild_id: ui.setGuild.value };
    if (ui.setToken.value.trim()) body.discord_token = ui.setToken.value.trim();
    const { discord: d } = await api("/api/settings", body);
    ui.setToken.value = "";
    if (d.error) {
      ui.settingsError.textContent = d.error;
      ui.settingsError.hidden = false;
    } else {
      ui.settings.close();
      toast(d.configured ? `Connected to ${d.guild_name}!` : "Saved.");
      store.lastError = null;
    }
    await refreshAll(false);
  } catch (e) {
    ui.settingsError.textContent = e.message;
    ui.settingsError.hidden = false;
  } finally {
    save.disabled = false;
  }
});

$("relogin").addEventListener("click", () => { ui.settings.close(); startJob("/api/login"); });

/* ------------------------------------------------------------------ boot */

$("brand-icon").innerHTML = brandIcon();
setPeel("happy", "Hi! Let's grab your favorites.", false);
(async () => {
  if (!TOKEN) {
    setPeel("oops", "This page needs the link StickerSync opened. Run “stickersync gui” again.", false);
    return;
  }
  await refreshAll(false);
  pollJob(); // resume a job that was running before a reload
  setInterval(() => { if (!busy()) refreshAll(false); }, 20000);
})();
