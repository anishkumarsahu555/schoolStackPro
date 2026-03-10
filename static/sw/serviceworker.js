// ================== CONFIGURATION ================== //
const APP_VERSION = 'v1.0.47'; // Increment this to force update
const MAX_CACHE_ITEMS = 50;   // Keep only 50 most recent files
const CACHE_KEYS = {
    PRECACHE: `precache-${APP_VERSION}`,
    RUNTIME: `runtime-${APP_VERSION}`
};

// Files that must load instantly.
// KEEP THIS LIST SHORT to avoid "Request failed" errors.
const PRECACHE_URLS = [
    'https://code.jquery.com/jquery-3.7.1.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/fomantic-ui/2.9.4/semantic.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/fomantic-ui/2.9.4/semantic.min.js',
    'https://cdn.datatables.net/2.3.2/css/dataTables.semanticui.min.css',
    'https://cdn.datatables.net/fixedcolumns/5.0.0/css/fixedColumns.dataTables.min.css',
    'https://cdn.datatables.net/2.3.2/js/dataTables.min.js',
    'https://cdn.datatables.net/2.3.2/js/dataTables.semanticui.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.2.7/pdfmake.min.js'
];

// Embedded Offline Page
const OFFLINE_HTML = `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Offline</title>
  <style>
    body{font-family:-apple-system, system-ui, sans-serif; text-align:center; padding:40px 20px; color:#333; background:#f9f9f9;}
    h1{color:#e0245e; margin-bottom:10px;}
    button{background:#007bff; color:white; border:none; padding:12px 24px; border-radius:6px; font-size:16px; cursor:pointer;}
  </style>
</head>
<body>
  <h1>You are Offline</h1>
  <p>We couldn't connect to the server.</p>
  <button onclick="window.location.reload()">Retry Connection</button>
</body>
</html>`;

// ================== INSTALL & ACTIVATE ================== //

self.addEventListener('install', (event) => {
    self.skipWaiting();

    event.waitUntil(
        caches.open(CACHE_KEYS.PRECACHE)
            .then(cache => cache.addAll(PRECACHE_URLS))
            .then(() => console.log(`[SW] Precached ${PRECACHE_URLS.length} files`))
            .catch(err => console.error('[SW] Precache failed:', err))
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        (async () => {
            if (self.registration.navigationPreload) {
                await self.registration.navigationPreload.enable();
            }
            await clients.claim();

            // Cleanup old versions
            const keys = await caches.keys();
            await Promise.all(
                keys.map(key => {
                    if (!Object.values(CACHE_KEYS).includes(key)) {
                        return caches.delete(key);
                    }
                })
            );
        })()
    );
});

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (err) {
        data = { title: 'School Update', body: event.data ? event.data.text() : 'You have a new notification.' };
    }

    const schoolName = data.schoolName || (data.data && data.data.schoolName) || '';
    const eventTitle = data.eventTitle || (data.data && data.data.eventTitle) || '';
    const title = data.title || schoolName || 'School Update';
    const notificationActions = Array.isArray(data.actions) && data.actions.length
        ? data.actions
        : [
            {action: 'open_event', title: 'Open'},
            {action: 'dismiss', title: 'Dismiss'}
        ];
    const options = {
        body: data.body || (eventTitle ? `${eventTitle}\nTap to view details.` : 'You have a new update.'),
        icon: data.icon || '/static/sw/images/icon-192.png',
        badge: data.badge || '/static/sw/images/icon-192-maskable.png',
        image: data.image || '',
        tag: data.tag || 'schoolstack-notification',
        renotify: true,
        requireInteraction: false,
        timestamp: Date.now(),
        vibrate: [120, 40, 120],
        actions: notificationActions,
        data: {
            url: data.url || '/',
            actionType: (data.data && data.data.action) || '',
            ...(data.data || {})
        }
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    if (event.action === 'dismiss') {
        return;
    }
    const rawTargetUrl = (event.notification && event.notification.data && event.notification.data.url) || '/';
    const destinationUrl = new URL(rawTargetUrl, self.location.origin).href;
    event.waitUntil((async () => {
        const clientList = await clients.matchAll({ type: 'window', includeUncontrolled: true });
        for (const client of clientList) {
            if (!('focus' in client)) continue;
            try {
                const clientUrl = new URL(client.url);
                if (clientUrl.origin !== self.location.origin) continue;
                if ('navigate' in client && client.url !== destinationUrl) {
                    await client.navigate(destinationUrl);
                }
                await client.focus();
                return;
            } catch (err) {
                // Fallback to openWindow below if URL parsing/navigation fails.
            }
        }
        if (clients.openWindow) {
            await clients.openWindow(destinationUrl);
        }
    })());
});

// ================== FETCH ENGINE ================== //

self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    if (request.method !== 'GET' || url.origin !== self.location.origin) return;

    // STRATEGY 1: HTML Pages (Navigation) -> Network First + Save to Cache
    if (request.mode === 'navigate') {
        event.respondWith(
            (async () => {
                try {
                    // A. Try Preload
                    const preloadResponse = await event.preloadResponse;
                    if (preloadResponse) return preloadResponse;

                    // B. Try Network
                    const networkResponse = await fetch(request);

                    // C. SAVE NETWORK RESPONSE TO CACHE (Critical Fix)
                    // This ensures that if the user goes offline later, they can revisit this page.
                    const cache = await caches.open(CACHE_KEYS.RUNTIME);
                    cache.put(request, networkResponse.clone());

                    return networkResponse;
                } catch (error) {
                    // D. Network Failed -> Try Cache
                    const cache = await caches.open(CACHE_KEYS.RUNTIME); // Check runtime cache first
                    const cachedResponse = await cache.match(request);
                    if (cachedResponse) return cachedResponse;

                    // E. Try Precache (fallback)
                    const precache = await caches.open(CACHE_KEYS.PRECACHE);
                    const preCachedResponse = await precache.match(request);
                    if (preCachedResponse) return preCachedResponse;

                    // F. Offline Page
                    return new Response(OFFLINE_HTML, {
                        headers: { 'Content-Type': 'text/html' }
                    });
                }
            })()
        );
        return;
    }

    // STRATEGY 2: Static Assets -> Stale-While-Revalidate + Auto Cleanup
    if (isAsset(url)) {
        event.respondWith(
            caches.open(CACHE_KEYS.RUNTIME).then(async (cache) => {
                const cachedResponse = await cache.match(request);

                const fetchPromise = fetch(request).then(networkResponse => {
                    if (networkResponse.ok) {
                        // Update Cache
                        cache.put(request, networkResponse.clone());
                        // Limit Size (Prevent phone storage full)
                        limitCacheSize(CACHE_KEYS.RUNTIME, MAX_CACHE_ITEMS);
                    }
                    return networkResponse;
                }).catch(() => { /* mute errors */ });

                return cachedResponse || fetchPromise;
            })
        );
    }
});

// ================== HELPERS ================== //

function isAsset(url) {
    return /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2|json|webp)$/i.test(url.pathname);
}

// Memory Management Helper
async function limitCacheSize(cacheName, maxItems) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length > maxItems) {
        await cache.delete(keys[0]); // Remove oldest
        limitCacheSize(cacheName, maxItems); // Recursively check again
    }
}
