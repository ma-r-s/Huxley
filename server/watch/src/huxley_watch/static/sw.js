/* The phone app's service worker (T1.46). Registered only in a secure
   context (iOS runs none over plain http on the tailnet: the app works
   without it). It keeps the app shell and a copy of the last /app.json
   for opening offline, with his words taken out (the phone never stores
   what he said), and nothing else: a day's words (/day/), recordings
   (/audio/) and the caregiver's page pass straight through, never stored. */
"use strict";
const SHELL = "walkie-shell-1";
const DATA = "walkie-data-2";  // 2: his words stripped
const SCOPE = new URL(self.registration.scope);
const APP_JSON = new URL("../app.json", SCOPE).pathname;
const FILES = ["./", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES.map(f => new URL(f, SCOPE).href))));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// The copy kept for offline: no transcript, no struggle's evidence.
async function wordless(response) {
  const data = await response.clone().json();
  if (data.today && data.today.last_used) data.today.last_used.said = null;
  for (const s of (data.talks && data.talks.struggles) || []) {
    s.said = null;
    s.detail = "";
  }
  return new Response(JSON.stringify(data), {headers: {"Content-Type": "application/json"}});
}

async function fresh(request, cacheName, key, keep) {
  try {
    const response = await fetch(request, {cache: "no-store"});
    if (response.ok) {
      const cache = await caches.open(cacheName);
      await cache.put(key, keep ? await keep(response) : response.clone());
    }
    return response;
  } catch (err) {
    const kept = await caches.match(key, {cacheName});
    if (kept) return kept;
    throw err;
  }
}

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== SCOPE.origin) return;
  if (url.pathname === APP_JSON) {
    event.respondWith(fresh(event.request, DATA, APP_JSON, wordless));  // only the last one
    return;
  }
  const inShell = FILES.some(f => new URL(f, SCOPE).pathname === url.pathname)
    || url.pathname === SCOPE.pathname + "index.html";
  if (inShell) event.respondWith(fresh(event.request, SHELL, url.pathname));
  // Anything else (/day/, /audio/, /, /status.json) is not ours to keep.
});
