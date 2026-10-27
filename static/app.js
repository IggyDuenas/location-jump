// Front end for Location Jump: map, search, saved places, buttons, and status polling.

const $ = (id) => document.getElementById(id);

// ---------- map ----------

const map = L.map("map", { worldCopyJump: true, zoomControl: false }).setView([25, 10], 3);
L.control.zoom({ position: "topleft" }).addTo(map);

// OpenStreetMap's own tiles: free, no account or key needed. There's no official dark style,
// so in dark mode the tiles are colour-inverted with CSS (see .dark-map in style.css).
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const applyMapTheme = () => map.getContainer().classList.toggle("dark-map", darkQuery.matches);
applyMapTheme();
darkQuery.addEventListener("change", applyMapTheme);

// The spot you picked (pin) vs. what the phone is actually showing (pulsing dot, moves with drift).
const pickMarker = L.marker([0, 0], {
  icon: L.divIcon({
    className: "pick-pin",
    html: '<svg viewBox="0 0 24 24" width="34" height="34"><path fill="currentColor" stroke="#fff" stroke-width="1.5" d="M12 1.8a7.2 7.2 0 0 0-7.2 7.2c0 5.4 7.2 13.2 7.2 13.2s7.2-7.8 7.2-13.2A7.2 7.2 0 0 0 12 1.8Z"/><circle cx="12" cy="9" r="2.7" fill="#fff"/></svg>',
    iconSize: [34, 34],
    iconAnchor: [17, 33],
  }),
  keyboard: false,
});
const phoneMarker = L.marker([0, 0], {
  icon: L.divIcon({ className: "phone-icon", html: '<div class="phone-dot"><i></i></div>', iconSize: [22, 22] }),
  keyboard: false,
  zIndexOffset: 1000,
});
const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#007a0f";
const routeLine = L.polyline([], { color: accent, weight: 5, opacity: 0.75, dashArray: "2 9", lineCap: "round" });
let routeId = null;    // which walk the route line is drawn for
let flownTo = null;    // last anchor we zoomed the map to
let pickLabel = null;  // name of the picked spot, when it came from search or a saved place
let lastState = { simulating: false };

function pick(lat, lon, label = null) {
  $("lat").value = lat.toFixed(6);
  $("lon").value = lon.toFixed(6);
  pickLabel = label;
  pickMarker.setLatLng([lat, lon]).addTo(map);
}

map.on("click", (e) => pick(e.latlng.lat, ((e.latlng.lng + 540) % 360) - 180));

for (const id of ["lat", "lon"]) {
  $(id).addEventListener("change", () => {
    const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
    pickLabel = null;
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      pickMarker.setLatLng([lat, lon]).addTo(map);
      map.panTo([lat, lon]);
    }
  });
  $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("btn-set").click(); });
}

// ---------- helpers ----------

let toastTimer;
function showMessage(text, kind) {
  const el = $("message");
  clearTimeout(toastTimer);
  if (!text) { el.hidden = true; return; }
  el.textContent = text;
  el.className = "toast " + kind;
  el.hidden = false;
  toastTimer = setTimeout(() => (el.hidden = true), kind === "error" ? 9000 : 4000);
}

async function api(path, body, method) {
  const opts = body === undefined && !method ? {} : {
    method: method || "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error("Can't reach the Location Jump app. Is it still running in Terminal?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

const fmt = (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
const near = (a, b) => L.latLng(a).distanceTo(L.latLng(b)) < 30;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function svg(markup) {
  const t = document.createElement("template");
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}

const ICON_STAR = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9L12 3.5Z"/></svg>';
const ICON_STAR_SOLID = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9L12 3.5Z"/></svg>';
const ICON_CLOCK = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm1 3v4.6l3.2 1.9-1 1.7L11 12.7V7h2Z"/></svg>';
const ICON_X = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="m6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"/></svg>';

// ---------- place search ----------

const search = $("search");
const resultsBox = $("search-results");
let searchTimer, searchSeq = 0, results = [], activeIdx = -1;

function closeResults() {
  resultsBox.hidden = true;
  search.parentElement.setAttribute("aria-expanded", "false");
  activeIdx = -1;
}

function renderResults(items, emptyText) {
  results = items;
  activeIdx = items.length ? 0 : -1;
  resultsBox.replaceChildren(...(items.length
    ? items.map((r, i) => el("li", {
        role: "option", id: `result-${i}`, "aria-selected": String(i === activeIdx),
        onmousedown: (e) => { e.preventDefault(); choose(r); },
      }, el("span", { class: "r-name" }, r.name), el("span", { class: "r-detail" }, r.detail || fmt(r.lat, r.lon))))
    : [el("li", { class: "r-empty" }, emptyText)]));
  resultsBox.hidden = false;
  search.parentElement.setAttribute("aria-expanded", "true");
}

function highlight(i) {
  if (!results.length) return;
  activeIdx = (i + results.length) % results.length;
  resultsBox.querySelectorAll("li").forEach((li, j) => li.setAttribute("aria-selected", String(j === activeIdx)));
  resultsBox.children[activeIdx].scrollIntoView({ block: "nearest" });
}

async function runSearch(q) {
  const seq = ++searchSeq;
  renderResults([], "Searching…");
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(q)}`);
    if (seq === searchSeq) renderResults(data.results, "No places found. Try adding a city or country.");
  } catch (e) {
    if (seq === searchSeq) renderResults([], e.message);
  }
}

function choose(r) {
  closeResults();
  search.value = r.name;
  pick(r.lat, r.lon, r.name);
  map.flyTo([r.lat, r.lon], 16, { duration: 1.4 });
  showMessage(`Found ${r.name}. Press Set Location to send it to your iPhone.`, "info");
  $("btn-set").focus();
}

search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = search.value.trim();
  if (q.length < 3) { searchSeq++; closeResults(); return; }
  searchTimer = setTimeout(() => runSearch(q), 450);
});
search.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); highlight(activeIdx + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); highlight(activeIdx - 1); }
  else if (e.key === "Escape") { closeResults(); }
  else if (e.key === "Enter") {
    e.preventDefault();
    if (!resultsBox.hidden && results[activeIdx]) choose(results[activeIdx]);
    else if (search.value.trim().length >= 2) { clearTimeout(searchTimer); runSearch(search.value.trim()); }
  }
});
search.addEventListener("blur", () => setTimeout(closeResults, 150));
search.addEventListener("focus", () => { if (results.length && search.value.trim().length >= 3) renderResults(results); });

// ---------- favorites & recents ----------

let saved = { favorites: [], recents: [] };
let activeTab = "favorites";
let savedKey = null;  // what the list currently shows, so polling doesn't rebuild it for nothing

const favoriteAt = (latlon) => saved.favorites.find((f) => near([f.lat, f.lon], latlon));

function renderSaved() {
  const isFav = activeTab === "favorites";
  const items = isFav ? saved.favorites : saved.recents;
  const key = JSON.stringify([activeTab, saved, lastState.simulating && lastState.anchor]);
  if (key === savedKey) return;
  savedKey = key;
  $("tab-favorites").classList.toggle("active", isFav);
  $("tab-recents").classList.toggle("active", !isFav);
  $("tab-favorites").setAttribute("aria-selected", String(isFav));
  $("tab-recents").setAttribute("aria-selected", String(!isFav));
  $("btn-clear-recents").hidden = isFav || !items.length;

  const list = $("saved-list");
  if (!items.length) {
    list.replaceChildren(el("li", { class: "saved-empty" }, isFav
      ? "No favorites yet. Set a location, then tap the ☆ to save it here."
      : "Places you jump to will show up here."));
    return;
  }
  list.replaceChildren(...items.map((p) => {
    const name = p.name || "Custom location";
    const current = lastState.simulating && near([p.lat, p.lon], lastState.anchor);
    const fav = isFav ? p : favoriteAt([p.lat, p.lon]);
    const action = isFav
      ? el("button", { class: "icon-btn", title: "Remove from favorites", "aria-label": `Remove ${name} from favorites`,
          onclick: () => removeFavorite(p.id) }, svg(ICON_X))
      : el("button", { class: "icon-btn", title: fav ? "Remove from favorites" : "Save to favorites",
          "aria-label": fav ? `Remove ${name} from favorites` : `Save ${name} to favorites`, "aria-pressed": String(!!fav),
          onclick: () => (fav ? removeFavorite(fav.id) : addFavorite(p.name, p.lat, p.lon)) }, svg(ICON_STAR));
    return el("li", { class: current ? "current" : "" },
      el("button", { class: "saved-go", title: `Jump to ${name}`, onclick: (e) => jumpTo(p, e.currentTarget) },
        el("span", { class: "saved-icon" }, svg(isFav ? ICON_STAR_SOLID : ICON_CLOCK)),
        el("span", { class: "saved-text" },
          el("span", { class: "saved-name" }, name),
          el("span", { class: "saved-coords" }, fmt(p.lat, p.lon)))),
      action);
  }));
}

function renderStar() {
  const star = $("btn-star");
  // Hidden mid-trip: the spot is still moving. It comes back on arrival.
  star.hidden = !lastState.simulating || !!lastState.walk;
  if (star.hidden) return;
  const fav = favoriteAt(lastState.anchor);
  star.setAttribute("aria-pressed", String(!!fav));
  star.title = fav ? "Remove from favorites" : "Save to favorites";
  star.setAttribute("aria-label", star.title);
}

function applySaved(data) {
  saved = { favorites: data.favorites || [], recents: data.recents || [] };
  renderSaved();
  renderStar();
}

async function loadSaved() {
  try { applySaved(await api("/api/saved")); } catch { /* status area already explains connection problems */ }
}

async function addFavorite(name, lat, lon) {
  try {
    applySaved(await api("/api/favorites", { name, lat, lon }));
    showMessage(`Saved ${name || "this spot"} to favorites.`, "success");
  } catch (e) { showMessage(e.message, "error"); }
}

async function removeFavorite(id) {
  try { applySaved(await api(`/api/favorites/${encodeURIComponent(id)}`, undefined, "DELETE")); }
  catch (e) { showMessage(e.message, "error"); }
}

$("tab-favorites").addEventListener("click", () => { activeTab = "favorites"; renderSaved(); });
$("tab-recents").addEventListener("click", () => { activeTab = "recents"; renderSaved(); });
$("btn-clear-recents").addEventListener("click", async () => {
  try { applySaved(await api("/api/recents", undefined, "DELETE")); } catch (e) { showMessage(e.message, "error"); }
});
$("btn-star").addEventListener("click", () => {
  if (!lastState.simulating) return;
  const fav = favoriteAt(lastState.anchor);
  if (fav) removeFavorite(fav.id);
  else addFavorite(lastState.label, lastState.anchor[0], lastState.anchor[1]);
});

// ---------- travel (walk mode) ----------

const VERBS = { walk: "Walking", jog: "Jogging", bike: "Biking", drive: "Driving" };
let travelMode = "walk";
try { travelMode = localStorage.getItem("travelMode") || "walk"; } catch { /* private mode */ }
if (!VERBS[travelMode]) travelMode = "walk";

function renderMode() {
  document.querySelectorAll(".seg").forEach((b) => {
    const on = b.dataset.mode === travelMode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", String(on));
  });
}
document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => {
  travelMode = b.dataset.mode;
  try { localStorage.setItem("travelMode", travelMode); } catch { /* ignore */ }
  renderMode();
}));
renderMode();

function fmtDist(m) {
  return m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`;
}
function fmtEta(s) {
  if (s < 60) return "under a minute";
  const min = Math.round(s / 60);
  if (min < 60) return `about ${min} min`;
  const h = Math.floor(min / 60), rest = min % 60;
  return rest ? `about ${h} h ${rest} min` : `about ${h} h`;
}

function renderTrip(state) {
  const walk = state.simulating ? state.walk : null;
  $("trip").hidden = !walk;
  $("walk-badge").hidden = !walk || state.reconnecting;
  if (!walk) {
    if (routeId !== null) { routeLine.remove(); routeId = null; }
    return;
  }
  $("walk-badge-text").textContent = VERBS[walk.mode];
  const pct = walk.total_m ? (walk.done_m / walk.total_m) * 100 : 100;
  $("trip-fill").style.width = `${Math.min(pct, 100).toFixed(1)}%`;
  $("trip-text").textContent = state.reconnecting
    ? `Paused, ${fmtDist(walk.left_m)} left`
    : `${fmtDist(walk.left_m)} left · ${fmtEta(walk.eta_s)}`;
  $("trip-note").hidden = !walk.note;
  $("trip-note").textContent = walk.note || "";
  if (routeId !== walk.id) {
    routeId = walk.id;
    routeLine.setLatLngs(walk.path).addTo(map);
    map.flyToBounds(routeLine.getBounds(), { paddingTopLeft: [60, 80], paddingBottomRight: [420, 60], maxZoom: 17, duration: 1.2 });
  }
}

let statePoll = null;
function followTrip(walking) {
  // While moving, refresh the dot every second (cheap: no device checks).
  if (walking && !statePoll) {
    statePoll = setInterval(async () => {
      try { renderSim(await api("/api/state")); } catch { /* the main status poll reports problems */ }
    }, 1000);
  } else if (!walking && statePoll) {
    clearInterval(statePoll);
    statePoll = null;
  }
}

$("btn-walk").addEventListener("click", () => run($("btn-walk"), async () => {
  const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    throw new Error("Pick a destination first: search for a place or tap the map.");
  }
  const state = await api("/api/walk", {
    lat, lon, label: pickLabel, mode: travelMode, follow_streets: $("follow-streets").checked,
  });
  renderSim(state);
  loadSaved();
  const w = state.walk;
  showMessage(w.note || `${VERBS[w.mode]} to ${w.label || "your destination"}: ${fmtDist(w.total_m)}, ${fmtEta(w.eta_s)}.`,
    w.note ? "info" : "success");
}));

$("btn-stop-walk").addEventListener("click", async () => {
  try {
    renderSim(await api("/api/walk/stop", {}));
    showMessage("Stopped. Your iPhone stays where it got to.", "success");
  } catch (e) { showMessage(e.message, "error"); }
});

// ---------- rendering ----------

let wasWalking = false;
function renderSim(state) {
  lastState = state;
  const walking = !!(state.simulating && state.walk);
  renderTrip(state);
  followTrip(walking);
  const title = document.querySelector("#sim .sim-title");
  const sub = document.querySelector("#sim .sim-sub");
  $("reconnect-badge").hidden = !(state.simulating && state.reconnecting);
  // While disconnected the phone is really on its own GPS; say what will come back instead.
  $("sim-heading").textContent = state.simulating && state.reconnecting ? "Restoring when reconnected" : "Your iPhone is showing";
  $("drift-badge").hidden = !(state.simulating && state.drift && !state.reconnecting && !walking);
  renderStar();
  renderSaved();
  if (!state.simulating) {
    title.textContent = "Real location";
    sub.textContent = "Nothing simulated";
    phoneMarker.remove();
    flownTo = null;
    wasWalking = false;
    return;
  }
  const [lat, lon] = state.current || state.anchor;
  // Once the phone is at the picked spot, the pulsing dot replaces the pin.
  if (pickMarker._map && pickMarker.getLatLng().distanceTo(state.anchor) < 1) pickMarker.remove();
  title.textContent = walking
    ? `${VERBS[state.walk.mode]} to ${state.walk.label || "destination"}`
    : state.label || "Custom location";
  sub.textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  phoneMarker.setLatLng([lat, lon]).addTo(map);
  phoneMarker.unbindTooltip();
  if (state.label) {
    phoneMarker.bindTooltip(state.label, { permanent: true, direction: "top", offset: [0, -14], className: "place-tip" });
  }

  const key = state.anchor.join(",");
  if (walking || wasWalking) {
    // Moving (or just arrived): keep the dot in view without re-zooming every step.
    flownTo = key;
    if (!map.getBounds().pad(-0.15).contains([lat, lon])) map.panTo([lat, lon]);
  } else if (flownTo !== key) {
    flownTo = key;
    map.flyTo(state.anchor, Math.max(map.getZoom(), 16), { duration: 1.6 });
  }
  wasWalking = walking;
}

// ---------- several devices ----------

let devices = [];
function deviceItem(d, onPick) {
  const sub = el("span", { class: "d-sub" });
  if (d.ios) sub.append(`iOS ${d.ios}`);
  if (d.usb) sub.append(el("span", { class: "conn" }, "USB"));
  if (d.wifi) sub.append(el("span", { class: "conn" }, "Wi-Fi"));
  return el("button", { class: "device-item", role: "menuitemradio", "aria-checked": String(!!d.selected), onclick: () => onPick(d) },
    el("span", { class: "d-icon", "aria-hidden": "true" }, "📱"),
    el("span", { class: "d-text" }, el("span", { class: "d-name" }, d.name || "Device"), sub),
    el("span", { class: "d-check", "aria-hidden": "true" }, d.selected ? "✓" : ""));
}

function renderDevices(s) {
  devices = s.devices || [];
  const switchable = devices.length > 1 || (devices.length === 1 && !devices[0].selected);
  $("status").classList.toggle("switchable", switchable);
  if (!switchable) closeDeviceMenu();
  // When the app needs you to pick (or your pick isn't here), offer the choices right in the panel.
  const ask = devices.length && (s.code === "choose_device" || (s.code === "not_connected" && s.selected));
  $("device-choices").hidden = !ask;
  if (ask) $("device-choices").replaceChildren(...devices.filter((d) => !d.selected).map((d) => deviceItem(d, chooseDevice)));
}

function closeDeviceMenu() {
  $("device-menu").hidden = true;
  $("status").setAttribute("aria-expanded", "false");
}

$("status").addEventListener("click", (e) => {
  if (!$("status").classList.contains("switchable")) return;
  e.stopPropagation();
  const menu = $("device-menu");
  if (!menu.hidden) { closeDeviceMenu(); return; }
  menu.replaceChildren(el("div", { class: "menu-title" }, "Use which device?"), ...devices.map((d) => deviceItem(d, chooseDevice)));
  menu.hidden = false;
  $("status").setAttribute("aria-expanded", "true");
  menu.querySelector(".device-item")?.focus();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".device-picker")) closeDeviceMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDeviceMenu(); });

async function chooseDevice(d) {
  closeDeviceMenu();
  if (d.selected) return;
  if (lastState.simulating && !confirm(`Switch to ${d.name}? The current device goes back to its real location.`)) return;
  try {
    renderStatus(await api("/api/device", { udid: d.udid }));
    showMessage(`Now using ${d.name}.`, "success");
  } catch (e) { showMessage(e.message, "error"); }
}

const SHORT_STATUS = {
  not_connected: "No iPhone connected",
  not_trusted: "Waiting for Trust on iPhone",
  locked: "iPhone is locked",
  dev_mode_off: "Developer Mode is off",
  no_tunnel: "Tunnel not running",
  tunnel_waiting: "Connecting tunnel…",
  old_ios: "iOS 17 or later required",
  timeout: "iPhone not responding",
  choose_device: "Choose a device",
  connection_lost: "Connection lost",
};

function renderStatus(s) {
  const box = $("status");
  const reconnecting = s.simulating && s.reconnecting;
  const choosing = s.code === "choose_device";
  box.className = "status " + (reconnecting || choosing ? "status-warn" : s.ready ? "status-ready" : s.connected ? "status-warn" : "status-bad");
  const statusText = $("status-text");
  if (reconnecting) statusText.textContent = "Reconnecting to iPhone…";
  else if (s.ready && s.device) {
    statusText.textContent = s.device.ios ? `${s.device.name} · iOS ${s.device.ios}` : s.device.name;
    statusText.append(" ", el("span", { class: "conn" }, s.device.connection === "wifi" ? "Wi-Fi" : "USB"));
  } else statusText.textContent = SHORT_STATUS[s.code] || "Needs attention";
  renderWifi(s);
  renderDevices(s);
  box.title = s.message;

  // Full plain-English instructions go in the panel, where there's room to read them.
  const problem = reconnecting ? s.last_error : !s.ready ? s.message : s.simulating ? s.last_error : null;
  $("alert").hidden = !problem;
  $("alert").className = "alert" + (s.connected || reconnecting || choosing ? "" : " bad");
  $("alert-text").textContent = problem || "";

  $("drift").checked = s.drift;
  renderSim(s);
}

function renderWifi(s) {
  const d = s.connected ? s.device : null;
  const toggle = $("wifi");
  if (wifiBusy) return;
  toggle.checked = !!(d && d.wifi_enabled);
  toggle.disabled = !(d && d.connection === "usb");
  $("wifi-hint").textContent = !d
    ? "Use your iPhone without the cable, on the same Wi-Fi as this Mac. Plug in once to turn this on."
    : d.connection === "wifi"
      ? "Connected over Wi-Fi. Plug in with the cable to turn this off."
      : d.wifi_enabled
        ? "On: you can unplug. Your iPhone stays reachable while it's awake on the same Wi-Fi as this Mac."
        : "Use your iPhone without the cable, on the same Wi-Fi as this Mac.";
}

let wifiBusy = false;
$("wifi").addEventListener("change", async (e) => {
  wifiBusy = true;
  e.target.disabled = true;
  try {
    const s = await api("/api/wifi", { enabled: e.target.checked });
    wifiBusy = false;
    renderStatus(s);
    showMessage(e.target.checked
      ? "Wi-Fi mode is on. You can unplug: your iPhone reconnects over Wi-Fi within about a minute (keep it unlocked the first time)."
      : "Wi-Fi mode is off. Location Jump will only use the cable.", "success");
  } catch (err) {
    wifiBusy = false;
    e.target.checked = !e.target.checked;
    e.target.disabled = false;
    showMessage(err.message, "error");
  }
});

let polling = false;
async function refreshStatus() {
  if (polling) return;
  polling = true;
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    $("status").className = "status status-bad";
    $("status-text").textContent = "App not reachable";
    $("alert").hidden = false;
    $("alert").className = "alert bad";
    $("alert-text").textContent = e.message;
  } finally {
    polling = false;
  }
}

// ---------- actions ----------

let busy = false;
async function run(button, work) {
  if (busy) return;
  busy = true;
  const buttons = document.querySelectorAll(".btn, .saved-go");
  const original = button.innerHTML;
  buttons.forEach((b) => (b.disabled = true));
  button.classList.add("busy");
  const icon = button.querySelector("svg, span[aria-hidden], .saved-icon > svg");
  if (icon) icon.outerHTML = '<span class="spinner" aria-hidden="true"></span>';
  showMessage("", "");
  try {
    await work();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    button.innerHTML = original;
    button.classList.remove("busy");
    document.querySelectorAll(".btn, .saved-go").forEach((b) => (b.disabled = false));
    busy = false;
  }
}

async function setLocation(lat, lon, label) {
  const state = await api("/api/location", { lat, lon, label });
  renderSim(state);
  loadSaved();
  return state;
}

function jumpTo(p, button) {
  run(button, async () => {
    pick(p.lat, p.lon, p.name);
    await setLocation(p.lat, p.lon, p.name);
    showMessage(`Jumped to ${p.name || "your saved spot"}`, "success");
  });
}

$("btn-set").addEventListener("click", () => run($("btn-set"), async () => {
  const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    throw new Error("Search for a place, tap the map, or type a latitude and longitude first.");
  }
  await setLocation(lat, lon, pickLabel);
  showMessage("Location set. Check Snap Map or Apple Maps on your phone.", "success");
}));

$("btn-random").addEventListener("click", () => run($("btn-random"), async () => {
  const state = await api("/api/random", {});
  pick(state.anchor[0], state.anchor[1], state.label);
  renderSim(state);
  loadSaved();
  showMessage(`Jumped to ${state.label}`, "success");
}));

$("btn-reset").addEventListener("click", () => run($("btn-reset"), async () => {
  renderSim(await api("/api/reset", {}));
  pickMarker.remove();
  showMessage("Back to your real location. Some apps take a minute to notice.", "success");
}));

$("drift").addEventListener("change", async (e) => {
  try {
    renderSim(await api("/api/drift", { enabled: e.target.checked }));
  } catch (err) {
    e.target.checked = !e.target.checked;
    showMessage(err.message, "error");
  }
});

let statusTimer;
$("btn-quit").addEventListener("click", async () => {
  const sim = lastState.simulating;
  if (!confirm(sim ? "Quit Location Jump? Your iPhone will go back to its real location."
                   : "Quit Location Jump?")) return;
  $("btn-quit").disabled = true;
  try {
    await api("/api/quit", {});
  } catch (e) {
    $("btn-quit").disabled = false;
    showMessage(e.message, "error");
    return;
  }
  clearInterval(statusTimer);
  followTrip(false);
  $("quit-screen").hidden = false;
});

renderSaved();
loadSaved();
refreshStatus();
statusTimer = setInterval(refreshStatus, 4000);
