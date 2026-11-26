/* HerEvo offline support. Keeps the app, its libraries and the country packs on the phone, so HerEvo
   still opens and still answers (from the reviewed pack, with emergency numbers for safety) without a
   signal. Personal data is never cached: /v1/ask, /v1/ledger and /v1/evidence always go to the network. */
const CACHE = 'herevo-offline-v1';
const PRECACHE = ['/', '/health', '/v1/packs/BD', '/v1/packs/KE', '/v1/packs/MX'];
const NEVER_CACHE = ['/v1/ask', '/v1/ledger', '/v1/evidence', '/telegram'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin && NEVER_CACHE.some((p) => url.pathname.startsWith(p))) return;
  const sameOrigin = url.origin === self.location.origin;
  const library = /(^|\.)(jsdelivr\.net|googleapis\.com|gstatic\.com)$/.test(url.hostname);
  if (!sameOrigin && !library) return;
  // Network first (always fresh when online), cached copy when offline.
  event.respondWith(
    fetch(req).then((res) => {
      if (res && (res.ok || res.type === 'opaque')) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || caches.match('/')))
  );
});
