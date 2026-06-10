const CACHE_NAME = 'nexus-dash-v2';
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/sw.js',
  '/static/css/style.css',
  '/static/js/app.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  // Network-first for API calls, fall back to offline error
  if (event.request.url.includes('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response('{"error":"offline"}', {
          headers: { 'Content-Type': 'application/json' },
        })
      )
    );
    return;
  }

  // Cache-first for static assets, update cache in background
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((resp) => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return resp;
        })
        .catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
