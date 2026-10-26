// Front end for Location Jump: map, buttons, and status polling.

const $ = (id) => document.getElementById(id);

const map = L.map("map", { worldCopyJump: true }).setView([30, 0], 2);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

// Where you clicked / typed (blue) vs. what the phone is actually showing (red, moves with drift).
const pickMarker = L.marker([0, 0], { opacity: 0.85 });
const phoneMarker = L.circleMarker([0, 0], { radius: 8, color: "#dc2626", fillOpacity: 0.8 });
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
}

function showMessage(text, kind) {
  const el = $("message");
  el.textContent = text;
  el.className = "message " + kind;
  el.hidden = !text;
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

function renderSim(state) {
  const el = $("sim");
  if (!state.simulating) {
    el.textContent = "Real location (nothing simulated)";
    phoneMarker.remove();
    flownTo = null;
    return;
  }
  const [lat, lon] = state.current || state.anchor;
  el.innerHTML = "";
  if (state.label) {
    const name = document.createElement("strong");
    name.textContent = state.label;
    el.appendChild(name);
  }
  el.appendChild(document.createTextNode(`${lat.toFixed(6)}, ${lon.toFixed(6)}`));
  if (state.drift) el.appendChild(document.createTextNode(" (drifting)"));
  phoneMarker.setLatLng([lat, lon]).addTo(map);

  const key = state.anchor.join(",");
  if (flownTo !== key) {
    flownTo = key;
    map.flyTo(state.anchor, Math.max(map.getZoom(), 15), { duration: 1.2 });
  }
}

function renderStatus(s) {
  const box = $("status");
  box.className = "status " + (s.ready ? "status-ready" : s.connected ? "status-warn" : "status-bad");
  $("status-text").textContent = s.message;
  $("drift").checked = s.drift;
  renderSim(s);
  if (s.last_error && s.simulating) showMessage(s.last_error, "error");
}

let polling = false;
async function refreshStatus() {
  if (polling) return;
  polling = true;
  try {
    renderStatus(await api("/api/status"));
  } catch (e) {
    $("status").className = "status status-bad";
    $("status-text").textContent = e.message;
  } finally {
    polling = false;
  }
}

async function run(button, busyText, work) {
  const buttons = document.querySelectorAll("button");
  const original = button.innerHTML;
  buttons.forEach((b) => (b.disabled = true));
  button.textContent = busyText;
  showMessage("", "");
  try {
    await work();
  } catch (e) {
    showMessage(e.message, "error");
  } finally {
    button.innerHTML = original;
    buttons.forEach((b) => (b.disabled = false));
  }
}

$("btn-set").addEventListener("click", () => run($("btn-set"), "Setting…", async () => {
  const lat = parseFloat($("lat").value), lon = parseFloat($("lon").value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    throw new Error("Click the map or type a latitude and longitude first.");
  }
  const state = await api("/api/location", { lat, lon });
  renderSim(state);
  showMessage("Location set! Open Snap Map (or Apple Maps) on your phone to check.", "success");
}));

$("btn-random").addEventListener("click", () => run($("btn-random"), "Jumping…", async () => {
  const state = await api("/api/random", {});
  pick(state.anchor[0], state.anchor[1]);
  renderSim(state);
  showMessage(`Jumped to ${state.label}!`, "success");
}));

$("btn-reset").addEventListener("click", () => run($("btn-reset"), "Resetting…", async () => {
  renderSim(await api("/api/reset", {}));
  pickMarker.remove();
  showMessage("Back to your real location. (Some apps take a minute to notice.)", "success");
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
