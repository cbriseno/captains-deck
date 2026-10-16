/* Captain's Deck service worker.
 *
 * The shell is fetched from the network first, so an update reaches the phone
 * on the next open. The cache is the offline fallback: it holds the last good
 * shell, so the app still opens when the bridge is unreachable.
 * The board itself is never cached, so /api and /ws always go to the bridge.
 */

const CACHE = "deck-shell-v4";
const LOG_CACHE = "deck-push-log";

/* Every push that reaches the worker is recorded. Delivery and display fail in
   different places, so the log tells the app whether the phone received
   anything at all; the settings sheet shows it. */
async function recordPush(payload) {
  try {
    const cache = await caches.open(LOG_CACHE);
    const previous = await cache.match("/log");
    let log = { count: 0, at: 0, last: null };
    if (previous) {
      const stored = await previous.json();
      if (stored && typeof stored === "object") log = stored;
    }
    log.count = (Number(log.count) || 0) + 1;
    log.at = Date.now();
    log.last = payload;
    await cache.put(
      "/log",
      new Response(JSON.stringify(log), { headers: { "Content-Type": "application/json" } })
    );
  } catch {
    /* a debugging aid must never be the reason a notification fails */
  }
}
const SHELL = [
  "/",
  "/index.html",
  "/app.js",
  "/styles.css",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE && key !== LOG_CACHE).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  let url;
  try {
    url = new URL(event.request.url);
  } catch {
    return; // an unparseable request URL is left to the network, not failed here
  }
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws") return;

  // Network first: a deck update must reach the phone on the next open. The
  // cache answers only when the network does not.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((hit) => hit || caches.match("/index.html"))
      )
  );
});

// A push from the bridge: one new Captain's Call, and the icon badge with it.
self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  const count = Number(payload.count) || 0;
  const title = payload.title || "Captain's Deck";
  event.waitUntil(
    (async () => {
      await recordPush(payload);
      try {
        if (count > 0 && self.navigator.setAppBadge) {
          await self.navigator.setAppBadge(count);
        } else if (self.navigator.clearAppBadge) {
          await self.navigator.clearAppBadge();
        }
      } catch {
        /* the badge is a nicety: a refusal must not stop the notification */
      }
      await self.registration.showNotification(title, {
        body: payload.body || "",
        icon: "/icons/icon-192.png",
        badge: "/icons/icon-192.png",
        tag: payload.tag || "deck",
        data: { url: payload.url || "/" },
      });
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          if (client.navigate) client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
