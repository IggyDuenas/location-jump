// Front end for Location Jump: map, buttons, and status polling.

const $ = (id) => document.getElementById(id);

// ---------- map ----------

const map = L.map("map", { worldCopyJump: true, zoomControl: false }).setView([25, 10], 3);
L.control.zoom({ position: "topleft" }).addTo(map);

// CARTO basemaps: clean light style, plus a dark one that follows the Mac's appearance setting.
const tileUrl = (dark) =>
  `https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "rastertiles/voyager"}/{z}/{x}/{y}{r}.png`;
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
const tiles = L.tileLayer(tileUrl(darkQuery.matches), {
  maxZoom: 20,
  subdomains: "abcd",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
}).addTo(map);
darkQuery.addEventListener("change", (e) => tiles.setUrl(tileUrl(e.matches)));

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
let flownTo = null;  // last anchor we zoomed the map to

function pick(lat, lon) {
  $("lat").value = lat.toFixed(6);
  $("lon").value = lon.toFixed(6);
  pickMarker.setLatLng([lat, lon]).addTo(map);
}

map.on("click", (e) => pick(e.latlng.lat, ((e.latlng.lng + 540) % 360) - 180));

for (const id of ["lat", "lon"]) {
  $(id).addEventListener("change", () => {
    const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
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

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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

// ---------- rendering ----------

function renderSim(state) {
  const title = document.querySelector("#sim .sim-title");
  const sub = document.querySelector("#sim .sim-sub");
  $("drift-badge").hidden = !(state.simulating && state.drift);
  if (!state.simulating) {
    title.textContent = "Real location";
    sub.textContent = "Nothing simulated";
    phoneMarker.remove();
    flownTo = null;
    return;
  }
  const [lat, lon] = state.current || state.anchor;
  // Once the phone is at the picked spot, the pulsing dot replaces the pin.
  if (pickMarker._map && pickMarker.getLatLng().distanceTo(state.anchor) < 1) pickMarker.remove();
  title.textContent = state.label || "Custom location";
  sub.textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  phoneMarker.setLatLng([lat, lon]).addTo(map);
  phoneMarker.unbindTooltip();
  if (state.label) {
    phoneMarker.bindTooltip(state.label, { permanent: true, direction: "top", offset: [0, -14], className: "place-tip" });
  }

  const key = state.anchor.join(",");
  if (flownTo !== key) {
    flownTo = key;
    map.flyTo(state.anchor, Math.max(map.getZoom(), 16), { duration: 1.6 });
  }
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
  connection_lost: "Connection lost",
};

function renderStatus(s) {
  const box = $("status");
  box.className = "status " + (s.ready ? "status-ready" : s.connected ? "status-warn" : "status-bad");
  $("status-text").textContent = s.ready && s.device
    ? `${s.device.name} · iOS ${s.device.ios}`
    : SHORT_STATUS[s.code] || "Needs attention";
  box.title = s.message;

  // Full plain-English instructions go in the panel, where there's room to read them.
  const problem = !s.ready ? s.message : s.simulating ? s.last_error : null;
  $("alert").hidden = !problem;
  $("alert").className = "alert" + (s.connected ? "" : " bad");
  $("alert-text").textContent = problem || "";

  $("drift").checked = s.drift;
  renderSim(s);
}

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

async function run(button, work) {
  const buttons = document.querySelectorAll(".btn");
  const original = button.innerHTML;
  buttons.forEach((b) => (b.disabled = true));
  button.classList.add("busy");
  const icon = button.querySelector("svg, span[aria-hidden]");
  if (icon) icon.outerHTML = '<span class="spinner" aria-hidden="true"></span>';
  showMessage("", "");
  try {
    await work();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    button.innerHTML = original;
    button.classList.remove("busy");
    buttons.forEach((b) => (b.disabled = false));
  }
}

$("btn-set").addEventListener("click", () => run($("btn-set"), async () => {
  const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    throw new Error("Tap the map or type a latitude and longitude first.");
  }
  const state = await api("/api/location", { lat, lon });
  renderSim(state);
  showMessage("Location set. Check Snap Map or Apple Maps on your phone.", "success");
}));

$("btn-random").addEventListener("click", () => run($("btn-random"), async () => {
  const state = await api("/api/random", {});
  pick(state.anchor[0], state.anchor[1]);
  renderSim(state);
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

refreshStatus();
setInterval(refreshStatus, 4000);
