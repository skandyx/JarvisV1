// NeuroLinked PWA service worker.
// Caches the dashboard shell so the phone's home-screen icon opens fast even
// when the laptop is briefly unreachable. We DON'T cache /api/* responses —
// those must always go live to the server. We DON'T cache the iframe contents
// (Jarvis + Brain) — those have their own cache-busting via ?v= query params.
const CACHE_NAME = 'neurolinked-shell-v1';
const SHELL_ASSETS = [
    '/',
    '/manifest.webmanifest',
    '/icon-192.png',
    '/icon-512.png',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS).catch(() => null))
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
    const req = event.request;
    const url = new URL(req.url);

    // Never cache API or websocket traffic — those are live data.
    if (url.pathname.startsWith('/api/') || url.protocol === 'ws:' || url.protocol === 'wss:') {
        return; // Let the browser handle it normally.
    }

    // Network-first for the HTML shell so updates are picked up immediately.
    // Cache fallback when the laptop's offline.
    if (req.mode === 'navigate' || (req.method === 'GET' && req.headers.get('accept')?.includes('text/html'))) {
        event.respondWith(
            fetch(req)
                .then((resp) => {
                    const clone = resp.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(req, clone)).catch(() => null);
                    return resp;
                })
                .catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
        );
        return;
    }

    // Cache-first for everything else (icons, manifest, static assets).
    event.respondWith(
        caches.match(req).then((cached) => {
            if (cached) return cached;
            return fetch(req).then((resp) => {
                if (resp.ok && req.method === 'GET') {
                    const clone = resp.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(req, clone)).catch(() => null);
                }
                return resp;
            }).catch(() => cached);
        })
    );
});
