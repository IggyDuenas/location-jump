// Location Jump website: copy buttons and the "jump around the world" demo map.

// ---------- copy buttons on the install commands ----------
document.querySelectorAll(".code .copy").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.closest(".code").querySelector("code").textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied!";
    } catch {
      button.textContent = "Select & copy";
    }
    setTimeout(() => (button.textContent = "Copy"), 1600);
  });
});

// ---------- demo map ----------
(async function demo() {
  const mapEl = document.getElementById("demo-map");
  if (!mapEl || typeof L === "undefined") return;

  // The same 55 famous places as the app's Random Location button (built from places.py).
  let places = [["Eiffel Tower, Paris", 48.8584, 2.2945]];
  try {
    places = await (await fetch("places.json")).json();
  } catch { /* keep the fallback */ }

  const map = L.map(mapEl, { zoomControl: false, scrollWheelZoom: false, attributionControl: true })
    .setView([20, 0], 2);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const theme = () => mapEl.classList.toggle("dark-map", dark.matches);
  theme();
  dark.addEventListener("change", theme);

  const dot = L.marker([0, 0], {
    icon: L.divIcon({ className: "", html: '<div class="demo-dot"><i></i></div>', iconSize: [22, 22] }),
    keyboard: false,
  });
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let last = -1;

  function jump() {
    let i;
    do { i = Math.floor(Math.random() * places.length); } while (places.length > 1 && i === last);
    last = i;
    const [name, lat, lon] = places[i];
    document.getElementById("demo-place").textContent = name;
    document.getElementById("demo-coords").textContent = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
    dot.setLatLng([lat, lon]).addTo(map);
    if (reduceMotion) map.setView([lat, lon], 15);
    else map.flyTo([lat, lon], 15, { duration: 2.2 });
  }

  document.getElementById("demo-jump").addEventListener("click", jump);
  jump();
})();
