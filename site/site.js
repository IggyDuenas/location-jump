// Location Jump website: menu, current section in the nav, scroll reveals, the tagline,
// copy buttons, and the "jump around the world" demo map.

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- mobile menu ----------
(function menu() {
  const toggle = document.querySelector(".menu-toggle");
  const panel = document.getElementById("menu");
  if (!toggle || !panel) return;

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    document.body.classList.toggle("menu-open", open);
    if (open) {
      panel.hidden = false;
      requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add("open")));
    } else {
      panel.classList.remove("open");
      setTimeout(() => { if (!panel.classList.contains("open")) panel.hidden = true; }, 300);
    }
  }

  toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));
  panel.addEventListener("click", (e) => { if (e.target.closest("a")) setOpen(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      setOpen(false);
      toggle.focus();
    }
  });
  window.matchMedia("(min-width: 1025px)").addEventListener("change", (e) => { if (e.matches) setOpen(false); });
})();

// ---------- highlight the section you're reading in the nav ----------
(function currentSection() {
  const links = [...document.querySelectorAll(".nav-links a[href^='#']")];
  const sections = links.map((a) => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  if (!sections.length || !("IntersectionObserver" in window)) return;
  const visible = new Set();
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) visible.add(entry.target.id);
      else visible.delete(entry.target.id);
    });
    // Nothing highlighted while you're in the hero or another unlisted section.
    const current = sections.find((s) => visible.has(s.id));
    links.forEach((a) => {
      if (current && a.getAttribute("href") === `#${current.id}`) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  }, { rootMargin: "-45% 0px -50% 0px" });
  sections.forEach((s) => observer.observe(s));
})();

// ---------- reveal sections once as they scroll into view ----------
(function reveals() {
  const items = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("in"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("in");
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -10% 0px" });
  items.forEach((el) => observer.observe(el));
})();

// ---------- tagline: words light up one at a time as it scrolls past ----------
(function tagline() {
  const el = document.querySelector("[data-words]");
  if (!el) return;

  // Wrap each word in a span, keeping the <br> line breaks.
  const nodes = [...el.childNodes];
  el.textContent = "";
  nodes.forEach((node) => {
    if (node.nodeType !== Node.TEXT_NODE) { el.append(node); return; }
    node.textContent.split(/(\s+)/).forEach((part) => {
      if (!part) return;
      if (/^\s+$/.test(part)) { el.append(part); return; }
      const word = document.createElement("span");
      word.className = "w";
      word.textContent = part;
      el.append(word);
    });
  });
  const words = [...el.querySelectorAll(".w")];
  if (reduceMotion || !("IntersectionObserver" in window)) {
    words.forEach((w) => w.classList.add("on"));
    return;
  }

  let ticking = false;
  function update() {
    ticking = false;
    const rect = el.getBoundingClientRect();
    const vh = window.innerHeight;
    // 0 when the text's top reaches 85% down the screen, 1 when its bottom passes 40%.
    const progress = (vh * 0.85 - rect.top) / (rect.height + vh * 0.45);
    const lit = Math.round(Math.min(1, Math.max(0, progress)) * words.length);
    words.forEach((w, i) => w.classList.toggle("on", i < lit));
  }
  function onScroll() {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }
  // Only listen to scrolling while the tagline is on screen.
  new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) { window.addEventListener("scroll", onScroll, { passive: true }); update(); }
    else window.removeEventListener("scroll", onScroll);
  }).observe(el);
})();

// ---------- copy buttons on the install commands ----------
document.querySelectorAll(".code .copy").forEach((button) => {
  const label = button.querySelector("span");
  button.addEventListener("click", async () => {
    const text = button.closest(".code").querySelector("code").textContent;
    try {
      await navigator.clipboard.writeText(text);
      label.textContent = "Copied";
    } catch {
      label.textContent = "Select and copy";
    }
    setTimeout(() => (label.textContent = "Copy"), 1600);
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
    icon: L.divIcon({ className: "", html: '<div class="demo-dot"><i></i></div>', iconSize: [24, 24] }),
    keyboard: false,
  });
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
