/* HerEvo offline support. Keeps the app, its libraries and the country packs on the phone, so HerEvo
   opens fast on a weak signal and still answers (from the reviewed pack, with emergency numbers for safety)
   without one. Personal data is never cached: /v1/ask, /v1/ledger and /v1/evidence always go to the network. */
const CACHE = 'herevo-offline-v5';
const PRECACHE = ['/', '/chat', '/health', '/v1/packs/BD', '/v1/packs/KE', '/v1/packs/MX', '/samples/heba.jpg', '/samples/notice.jpg', '/samples/kabin.jpg', '/v1/scenarios?lang=bn', '/v1/scenarios?lang=en'];
const NEVER_CACHE = ['/v1/ask', '/v1/ledger', '/v1/evidence', '/v1/inbox', '/v1/radar', '/telegram', '/health/ai'];
const NETWORK_WAIT_MS = 4000;  // on a weak signal, show the saved copy instead of a blank page

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

function save(req, res) {
  if (res && (res.ok || res.type === 'opaque')) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(req, copy));
  }
  return res;
}

// Fonts and the map library never change at a given address: use the saved copy, download only once.
function cacheFirst(req) {
  return caches.match(req).then((hit) => hit || fetch(req).then((res) => save(req, res)));
}

// The page and the packs: fresh from the network when it answers quickly, else the saved copy at once
// (and the fresh copy is still saved for next time when it arrives).
function networkFirst(req) {
  const network = fetch(req).then((res) => save(req, res));
  const timeout = new Promise((resolve) => setTimeout(resolve, NETWORK_WAIT_MS));
  return Promise.race([network, timeout.then(() => caches.match(req))])
    .then((res) => res || network)
    .catch(() => caches.match(req).then((hit) => hit || (req.mode === 'navigate' ? caches.match('/') : Response.error())));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;
  if (sameOrigin && NEVER_CACHE.some((p) => url.pathname === p || url.pathname.startsWith(p + '/'))) return;
  if (/(^|\.)(jsdelivr\.net|googleapis\.com|gstatic\.com)$/.test(url.hostname)) {
    event.respondWith(cacheFirst(req));
    return;
  }
  if (sameOrigin) event.respondWith(networkFirst(req));
});
