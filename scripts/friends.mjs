// Encrypts the website's friends area so only people with the password can read it.
//
//   node scripts/friends.mjs encrypt   # friends/content.html  ->  site/friends.enc.json
//   node scripts/friends.mjs decrypt   # site/friends.enc.json ->  friends/content.html
//
// The password is read from FRIENDS_PASSWORD, or asked for. friends/ is git-ignored, so the
// readable content never gets committed: only the encrypted site/friends.enc.json does.
// The page (site/friends.js) decrypts it in the browser with the same scheme:
// PBKDF2-SHA256 (600,000 rounds) derives an AES-256-GCM key from the password.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { webcrypto as crypto } from "node:crypto";
import { createInterface } from "node:readline";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SOURCE = join(ROOT, "friends", "content.html");
const OUTPUT = join(ROOT, "site", "friends.enc.json");
const ITERATIONS = 600_000;

const b64 = (bytes) => Buffer.from(bytes).toString("base64");
const unb64 = (text) => new Uint8Array(Buffer.from(text, "base64"));

async function deriveKey(password, salt, iterations) {
  const material = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    material, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"],
  );
}

function askPassword(prompt) {
  if (process.env.FRIENDS_PASSWORD) return Promise.resolve(process.env.FRIENDS_PASSWORD);
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    rl._writeToOutput = (s) => { if (s.includes(prompt)) process.stdout.write(s); }; // hide typing
    rl.question(prompt, (answer) => { rl.close(); process.stdout.write("\n"); resolve(answer); });
  });
}

async function encrypt() {
  const html = readFileSync(SOURCE, "utf8");
  const password = await askPassword("Password for the friends area: ");
  if (password.length < 8) throw new Error("Use a password of at least 8 characters.");
  if (!process.env.FRIENDS_PASSWORD && password !== await askPassword("Type it again: ")) {
    throw new Error("The passwords don't match.");
  }
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt, ITERATIONS);
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(html));
  const payload = { v: 1, kdf: "PBKDF2-SHA256", iterations: ITERATIONS, salt: b64(salt), iv: b64(iv), data: b64(new Uint8Array(data)) };
  writeFileSync(OUTPUT, JSON.stringify(payload) + "\n");
  console.log(`Encrypted ${SOURCE} -> ${OUTPUT}`);
}

async function decrypt() {
  const payload = JSON.parse(readFileSync(OUTPUT, "utf8"));
  const password = await askPassword("Password for the friends area: ");
  const key = await deriveKey(password, unb64(payload.salt), payload.iterations);
  let data;
  try {
    data = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(payload.iv) }, key, unb64(payload.data));
  } catch {
    throw new Error("Wrong password.");
  }
  mkdirSync(dirname(SOURCE), { recursive: true });
  writeFileSync(SOURCE, new TextDecoder().decode(data));
  console.log(`Decrypted ${OUTPUT} -> ${SOURCE}`);
}

const command = process.argv[2];
const run = { encrypt, decrypt }[command];
if (!run) {
  console.error("Usage: node scripts/friends.mjs encrypt|decrypt");
  process.exit(2);
}
run().catch((err) => { console.error(err.message); process.exit(1); });
