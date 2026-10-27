// Location Jump website: the friends area. Its content lives encrypted in friends.enc.json
// (made by scripts/friends.mjs) and is only decrypted here, in the browser, with the password.

(function friends() {
  const form = document.getElementById("unlock");
  const input = document.getElementById("password");
  const error = document.getElementById("unlock-error");
  const button = form.querySelector("button");
  const label = button.querySelector(".btn-label");
  const unb64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    input.select();
  }

  async function decrypt(password) {
    const payload = await (await fetch("friends.enc.json", { cache: "no-store" })).json();
    const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt: unb64(payload.salt), iterations: payload.iterations },
      material, { name: "AES-GCM", length: 256 }, false, ["decrypt"],
    );
    const data = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(payload.iv) }, key, unb64(payload.data));
    return new TextDecoder().decode(data);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.hidden = true;
    input.removeAttribute("aria-invalid");
    if (!input.value) {
      showError("Enter the password to continue.");
      return;
    }
    if (!window.crypto || !crypto.subtle) {
      showError("This browser can't unlock the page. Try a recent Safari, Chrome or Firefox.");
      return;
    }

    button.disabled = true;
    label.textContent = "Unlocking…";
    let html;
    try {
      html = await decrypt(input.value);
    } catch (err) {
      button.disabled = false;
      label.textContent = "Unlock";
      // A wrong password makes AES-GCM reject the data; anything else is a loading problem.
      if (err && err.name === "OperationError") showError("That password didn't work. Check it and try again.");
      else showError("The page couldn't load. Check your connection and try again.");
      return;
    }

    const content = document.getElementById("content");
    content.innerHTML = html; // our own content, written by us and encrypted with scripts/friends.mjs
    document.getElementById("lock").hidden = true;
    content.hidden = false;
    content.focus();
    findApp();
  });

  // ---------- jumping straight from the menu ----------
  // Location Jump runs on this Mac (port 5000, or 5001 when AirPlay has 5000) and lets this
  // website check it's there and set a location, nothing else.
  const PORTS = [5000, 5001];
  let appUrl = null;

  function setStatus(text, state) {
    const status = document.getElementById("app-status");
    if (!status) return;
    status.textContent = text;
    status.dataset.state = state;
  }

  async function findApp() {
    for (const port of PORTS) {
      const url = `http://127.0.0.1:${port}`;
      try {
        const res = await fetch(`${url}/api/ping`, { signal: AbortSignal.timeout(2000) });
        const body = await res.json();
        if (body.app === "location-jump") {
          appUrl = url;
          setStatus("Connected to Location Jump on this Mac.", "ok");
          return url;
        }
      } catch { /* not running on this port, or an older version that doesn't allow this page */ }
    }
    appUrl = null;
    setStatus("Location Jump isn't reachable. Open it on this Mac (update it first if it's been a while), and if your browser asks to reach apps on this device, choose Allow. Then press Jump here.", "off");
    return null;
  }

  async function jump(place, button) {
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Jumping…";
    try {
      const url = appUrl || await findApp();
      if (!url) throw new Error("unreachable");
      const res = await fetch(`${url}/api/location`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: Number(place.dataset.lat), lon: Number(place.dataset.lon), label: place.dataset.label }),
      });
      const body = await res.json();
      if (!body.ok) {
        setStatus(body.error || "Location Jump couldn't set that location.", "error");
        return;
      }
      document.querySelectorAll(".place.current").forEach((p) => p.classList.remove("current"));
      place.classList.add("current");
      setStatus(`Your iPhone is now at ${place.dataset.label}.`, "ok");
    } catch {
      appUrl = null;
      setStatus("Couldn't reach Location Jump. Make sure it's open and up to date on this Mac, and that your browser is allowed to reach it, then try again.", "off");
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  // Copy and Jump buttons on the menu.
  document.getElementById("content").addEventListener("click", async (e) => {
    const jumpButton = e.target.closest("button.jump");
    if (jumpButton) {
      jump(jumpButton.closest(".place"), jumpButton);
      return;
    }
    const copy = e.target.closest("button[data-copy]");
    if (!copy) return;
    try {
      await navigator.clipboard.writeText(copy.dataset.copy);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select it";
    }
    setTimeout(() => (copy.textContent = "Copy"), 1600);
  });
})();
