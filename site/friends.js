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
  });
})();
