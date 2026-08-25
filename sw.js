const CACHE = 'gps-pdv-v15';
const PRECACHE = [
  '/',
  '/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/gps-worker.js'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      return c.addAll(PRECACHE);
    }).catch(function () {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isNavigation(req) {
  return req.mode === 'navigate';
}

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== location.origin) return;

  // Navegación (HTML): red por defecto, cache como respaldo (modo offline)
  if (isNavigation(req)) {
    e.respondWith(
      fetch(req).then(function (res) {
        if (res && res.status === 200) {
          const copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (m) {
          return m || caches.match('/');
        });
      })
    );
    return;
  }

  // APIs y estáticos: cache-first para estáticos, network-first para el resto
  const isStatic = req.url.includes('/static/') || req.url.includes('/manifest.json');
  if (isStatic) {
    e.respondWith(
      caches.match(req).then(function (m) {
        return m || fetch(req).then(function (res) {
          const copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
          return res;
        });
      })
    );
    return;
  }

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.status === 200) {
        const copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (m) {
        return m || new Response('Sin conexión. Abrí la app con conexión al menos una vez.', {
          status: 503,
          headers: { 'Content-Type': 'text/plain; charset=utf-8' }
        });
      });
    })
  );
});

// ---------------- Notificaciones push ----------------
self.addEventListener('push', function (e) {
  let data = {};
  try {
    data = e.data.json();
  } catch (err) {
    data = { title: 'GPS Merchan', body: e.data ? e.data.text() : '' };
  }
  const title = data.title || 'GPS Merchan';
  const body = data.body || '';
  e.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      vibrate: [200, 100, 200]
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clients) {
      if (clients.length) {
        return clients[0].focus();
      }
      return self.clients.openWindow('/');
    })
  );
});
