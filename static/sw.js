// DeCloud Service Worker
const CACHE_NAME = 'decloud-v270';

self.addEventListener('install', e => {
  self.skipWaiting(); // activate immediately
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      // Nuke ALL caches on every SW update. (No forced reload of open pages: fetches are network-first and
      // scripts carry ?v=, so the next open is fresh anyway, and a reload here made the app load twice.)
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Network-first for everything — always get fresh content
  if (e.request.method !== 'GET') return;
  // Never cache API responses (personal data; server sends no-store anyway)
  if (new URL(e.request.url).pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request)
      .then(response => {
        // Cache a copy for offline use
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(e.request))
  );
});