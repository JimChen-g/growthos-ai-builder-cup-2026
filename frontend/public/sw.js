// GrowthOS service worker: fast repeat visits and an offline page shell.
// API responses are never cached, so business data is always fresh or clearly unavailable.
const CACHE = "growthos-shell-v2";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

function cacheable(response, pathname, navigation = false) {
  if (!response.ok || response.redirected) return false;
  const contentType = (response.headers.get("Content-Type") || "").split(";", 1)[0].trim().toLowerCase();
  if (navigation) return contentType === "text/html";
  if (pathname.endsWith(".js")) return /^(?:text|application)\/(?:java|ecma)script$/.test(contentType);
  if (pathname.endsWith(".css")) return contentType === "text/css";
  if (pathname.endsWith(".webmanifest")) return ["application/manifest+json", "application/json"].includes(contentType);
  if (/\.(?:woff2?|ttf|otf)$/.test(pathname)) return /^(?:font\/|application\/(?:font-|x-font-))/.test(contentType);
  if (/\.(?:png|jpe?g|svg|webp|avif|ico)$/.test(pathname)) return contentType.startsWith("image/");
  return false;
}

async function fetchAndCache(request, navigation = false) {
  const response = await fetch(request);
  const pathname = new URL(request.url).pathname;
  if (cacheable(response, pathname, navigation)) {
    // A full browser cache must not turn a successful network response into a page error.
    await caches.open(CACHE).then((cache) => cache.put(request, response.clone())).catch(() => undefined);
  }
  return response;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => Promise.all(SHELL.map(async (path) => {
      const response = await fetch(path, { cache: "reload" });
      if (!cacheable(response, path, path === "/")) throw new Error("Invalid offline shell response");
      await cache.put(path, response);
    }))).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("growthos-shell-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return;

  // Hashed build assets never change: cache first.
  if (url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(
      caches.match(request).then(
        (hit) => hit || fetchAndCache(request),
      ),
    );
    return;
  }

  // Pages: network first, cached shell when offline.
  if (request.mode === "navigate") {
    event.respondWith(
      fetchAndCache(request, true)
        .catch(() => caches.match(request).then((hit) => hit || caches.match("/"))),
    );
  }
});
