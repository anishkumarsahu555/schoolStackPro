const APP_VERSION = 'v1.0.49';
const CACHE_PREFIX = 'schoolstack';
const CACHE_KEYS = {
    PRECACHE: `${CACHE_PREFIX}-precache-${APP_VERSION}`,
    PAGES: `${CACHE_PREFIX}-pages-${APP_VERSION}`,
    ASSETS: `${CACHE_PREFIX}-assets-${APP_VERSION}`
};

const MAX_PAGE_CACHE_ITEMS = 12;
const MAX_ASSET_CACHE_ITEMS = 90;
const NETWORK_TIMEOUT_MS = 4500;

const PRECACHE_URLS = [
    '/manifest.webmanifest',
    '/static/sw/offline.html',
    '/static/sw/images/icon-192.png',
    '/static/sw/images/icon-512.png',
    '/static/sw/images/icon-192-maskable.png',
    '/static/sw/images/favicon.ico',
    '/static/sw/images/apple-touch-icon.png',
    '/static/images/default_avatar.svg'
];

const OFFLINE_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Offline - Schools Stack</title>
  <style>
    :root{color-scheme:light dark}
    *{box-sizing:border-box}
    body{align-items:center;background:#eef3fb;color:#152033;display:flex;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;justify-content:center;margin:0;min-height:100vh;overflow:hidden;padding:24px}
    body:before{background:radial-gradient(circle at 18% 18%,rgba(34,197,94,.20),transparent 30%),radial-gradient(circle at 84% 22%,rgba(37,99,235,.20),transparent 32%),linear-gradient(135deg,#f8fbff,#e7eef8);content:"";inset:0;position:fixed;z-index:-2}
    body:after{background-image:linear-gradient(rgba(15,23,42,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(15,23,42,.045) 1px,transparent 1px);background-size:34px 34px;content:"";inset:0;opacity:.75;position:fixed;z-index:-1}
    main{background:rgba(255,255,255,.88);border:1px solid rgba(148,163,184,.35);border-radius:8px;box-shadow:0 24px 70px rgba(15,23,42,.16);max-width:520px;padding:34px;text-align:left;width:100%}
    .brand{align-items:center;display:flex;gap:12px;margin-bottom:24px}
    .brand-mark{align-items:center;background:#163f8f;border-radius:8px;color:#fff;display:flex;font-size:24px;font-weight:800;height:52px;justify-content:center;width:52px}
    .brand-text{font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
    .status{align-items:center;background:#fff7ed;border:1px solid #fed7aa;border-radius:999px;color:#9a3412;display:inline-flex;font-size:13px;font-weight:800;gap:8px;margin-bottom:14px;padding:7px 12px}
    .dot{animation:pulse 1.4s infinite;background:#f97316;border-radius:999px;height:8px;width:8px}
    h1{font-size:34px;letter-spacing:0;line-height:1.08;margin:0 0 12px}
    p{color:#536073;font-size:16px;line-height:1.6;margin:0}
    .actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:26px}
    button,a{align-items:center;border-radius:6px;display:inline-flex;font-size:14px;font-weight:800;justify-content:center;min-height:42px;padding:0 16px;text-decoration:none}
    button{background:#1d4ed8;border:0;color:#fff;cursor:pointer}
    a{background:#f8fafc;border:1px solid #cbd5e1;color:#243044}
    .tips{border-top:1px solid #e2e8f0;display:grid;gap:10px;margin-top:28px;padding-top:22px}
    .tip{align-items:flex-start;color:#526071;display:flex;font-size:14px;gap:10px;line-height:1.45}
    .tick{background:#dcfce7;border-radius:999px;color:#166534;flex:0 0 auto;font-size:12px;font-weight:900;height:20px;line-height:20px;text-align:center;width:20px}
    @keyframes pulse{0%,100%{opacity:.45;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
    @media (max-width:520px){main{padding:26px}h1{font-size:29px}.actions{display:grid}button,a{width:100%}}
    @media (prefers-color-scheme:dark){
      body{background:#0f172a;color:#edf3ff}
      body:before{background:radial-gradient(circle at 18% 18%,rgba(34,197,94,.12),transparent 30%),radial-gradient(circle at 84% 22%,rgba(59,130,246,.16),transparent 32%),linear-gradient(135deg,#0f172a,#111827)}
      body:after{background-image:linear-gradient(rgba(226,232,240,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(226,232,240,.045) 1px,transparent 1px)}
      main{background:rgba(15,23,42,.90);border-color:rgba(148,163,184,.28)}
      p,.tip{color:#aeb8c6}.tips{border-color:#263244}a{background:#172033;border-color:#334155;color:#e5e7eb}.status{background:#431407;border-color:#9a3412;color:#fed7aa}
    }
  </style>
</head>
<body>
  <main aria-labelledby="offline-title">
    <div class="brand">
      <div class="brand-mark">S</div>
      <div>
        <div class="brand-text">Schools Stack</div>
        <p>Offline mode</p>
      </div>
    </div>
    <div class="status"><span class="dot"></span>No internet connection</div>
    <h1 id="offline-title">We will be right back when your network is.</h1>
    <p>This page needs a live connection. Your saved app shell and previously opened pages stay available where possible.</p>
    <div class="actions">
      <button type="button" onclick="window.location.reload()">Try again</button>
      <a href="javascript:history.back()">Go back</a>
    </div>
    <div class="tips" aria-label="Offline tips">
      <div class="tip"><span class="tick">✓</span><span>Check Wi-Fi or mobile data, then tap Try again.</span></div>
      <div class="tip"><span class="tick">✓</span><span>Keep this tab open; the app will reconnect cleanly once the network returns.</span></div>
      <div class="tip"><span class="tick">✓</span><span>Live school data, uploads, and form submissions need internet access.</span></div>
    </div>
  </main>
</body>
</html>`;

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_KEYS.PRECACHE);
        await Promise.allSettled(PRECACHE_URLS.map(url => cache.add(url)));
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        if (self.registration.navigationPreload) {
            await self.registration.navigationPreload.enable();
        }
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => {
            if (key.startsWith(`${CACHE_PREFIX}-`) && !Object.values(CACHE_KEYS).includes(key)) {
                return caches.delete(key);
            }
            return Promise.resolve(false);
        }));
        await clients.claim();
        await broadcastMessage({type: 'SW_ACTIVATED', version: APP_VERSION});
    })());
});

self.addEventListener('message', (event) => {
    const message = event.data || {};
    if (message.type === 'SKIP_WAITING') {
        event.waitUntil(self.skipWaiting());
    }
    if (message.type === 'CLEAR_OFFLINE_CACHE') {
        event.waitUntil(clearOfflineCaches());
    }
    if (message.type === 'GET_VERSION') {
        event.source && event.source.postMessage({type: 'SW_VERSION', version: APP_VERSION});
    }
});

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (err) {
        data = {title: 'School Update', body: event.data ? event.data.text() : 'You have a new notification.'};
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
        const clientList = await clients.matchAll({type: 'window', includeUncontrolled: true});
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
                // Fall through to openWindow.
            }
        }
        if (clients.openWindow) {
            await clients.openWindow(destinationUrl);
        }
    })());
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);

    if (request.method !== 'GET') {
        return;
    }

    if (request.mode === 'navigate') {
        event.respondWith(handleNavigationRequest(event));
        return;
    }

    if (isApiRequest(request, url)) {
        event.respondWith(handleApiRequest(request));
        return;
    }

    if (isCacheableAsset(request, url)) {
        event.respondWith(staleWhileRevalidate(request));
    }
});

async function handleNavigationRequest(event) {
    const request = event.request;
    try {
        const preloadResponse = await event.preloadResponse;
        if (preloadResponse) {
            await maybeCachePage(request, preloadResponse.clone());
            return preloadResponse;
        }

        const networkResponse = await fetchWithTimeout(request, NETWORK_TIMEOUT_MS);
        await maybeCachePage(request, networkResponse.clone());
        return networkResponse;
    } catch (error) {
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }
        const offlineResponse = await caches.match('/static/sw/offline.html');
        if (offlineResponse) {
            return offlineResponse;
        }
        return new Response(OFFLINE_HTML, {
            status: 503,
            statusText: 'Offline',
            headers: {'Content-Type': 'text/html; charset=utf-8'}
        });
    }
}

async function handleApiRequest(request) {
    try {
        return await fetch(request);
    } catch (error) {
        return new Response(JSON.stringify({
            success: false,
            offline: true,
            message: 'You are offline. Reconnect and try again.'
        }), {
            status: 503,
            statusText: 'Offline',
            headers: {
                'Content-Type': 'application/json; charset=utf-8',
                'Cache-Control': 'no-store'
            }
        });
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE_KEYS.ASSETS);
    const cachedResponse = await cache.match(request, {ignoreVary: true});
    const networkPromise = fetch(request)
        .then(async (networkResponse) => {
            if (isCacheableResponse(networkResponse)) {
                await cache.put(request, networkResponse.clone());
                await limitCacheSize(CACHE_KEYS.ASSETS, MAX_ASSET_CACHE_ITEMS);
            }
            return networkResponse;
        })
        .catch(() => null);

    return cachedResponse || await networkPromise || new Response('', {status: 504, statusText: 'Offline'});
}

async function maybeCachePage(request, response) {
    if (!shouldCachePage(request, response)) {
        return;
    }
    const cache = await caches.open(CACHE_KEYS.PAGES);
    await cache.put(request, response);
    await limitCacheSize(CACHE_KEYS.PAGES, MAX_PAGE_CACHE_ITEMS);
}

function shouldCachePage(request, response) {
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return false;
    if (!response || !response.ok || response.type === 'opaqueredirect') return false;
    if (!isHtmlResponse(response)) return false;
    if (hasNoStore(response)) return false;
    return !isSensitivePath(url.pathname);
}

function isSensitivePath(pathname) {
    return [
        '/admin/',
        '/api/',
        '/logout',
        '/reset-password',
        '/forgot-password',
        '/access-link',
        '/email-verification'
    ].some(prefix => pathname === prefix || pathname.startsWith(prefix));
}

function isApiRequest(request, url) {
    if (url.origin !== self.location.origin) return false;
    const accept = request.headers.get('Accept') || '';
    return url.pathname.includes('/api/') || accept.includes('application/json');
}

function isCacheableAsset(request, url) {
    if (!['script', 'style', 'image', 'font', 'manifest'].includes(request.destination)) {
        return isAssetPath(url.pathname);
    }
    return url.protocol === 'http:' || url.protocol === 'https:';
}

function isAssetPath(pathname) {
    return /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|json|webp|map)$/i.test(pathname);
}

function isHtmlResponse(response) {
    const contentType = response.headers.get('Content-Type') || '';
    return contentType.includes('text/html');
}

function hasNoStore(response) {
    const cacheControl = response.headers.get('Cache-Control') || '';
    return /no-store/i.test(cacheControl);
}

function isCacheableResponse(response) {
    if (!response) return false;
    if (response.type === 'opaque') return true;
    if (!response.ok) return false;
    return !hasNoStore(response);
}

function fetchWithTimeout(request, timeoutMs) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('network-timeout')), timeoutMs);
        fetch(request).then((response) => {
            clearTimeout(timer);
            resolve(response);
        }).catch((error) => {
            clearTimeout(timer);
            reject(error);
        });
    });
}

async function limitCacheSize(cacheName, maxItems) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    while (keys.length > maxItems) {
        const key = keys.shift();
        await cache.delete(key);
    }
}

async function clearOfflineCaches() {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith(`${CACHE_PREFIX}-`)).map(key => caches.delete(key)));
}

async function broadcastMessage(message) {
    const clientList = await clients.matchAll({type: 'window', includeUncontrolled: true});
    await Promise.all(clientList.map(client => client.postMessage(message)));
}
