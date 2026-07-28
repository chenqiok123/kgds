/* KGDS Service Worker — PWA 离线缓存
 * 策略：
 *   核心资源（HTML/JS/图标/manifest）→ Cache First，后台更新
 *   CDN 依赖（unpkg 3d-force-graph / three.js）→ Cache First（版本固定，不会变）
 *   API 数据（/api/nodes /api/edges /api/tests）→ Network First，失败用缓存
 *   音频（/audio/）→ Cache First（预生成文件不变）
 *   其他 API（POST /api/submit 等）→ 不拦截，直接走网络
 */
const CACHE_CORE = 'kgds-core-v1';
const CACHE_CDN = 'kgds-cdn-v1';
const CACHE_DATA = 'kgds-data-v1';
const CACHE_AUDIO = 'kgds-audio-v1';

const CORE_ASSETS = [
  '/app.html',
  '/app.js',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

const CDN_HOSTS = ['unpkg.com', 'cdn.jsdelivr.net'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_CORE)
      .then((c) => c.addAll(CORE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys
        .filter((k) => ![CACHE_CORE, CACHE_CDN, CACHE_DATA, CACHE_AUDIO].includes(k))
        .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // 非 GET 请求不拦截
  if (e.request.method !== 'GET') return;

  // CDN 依赖：Cache First
  if (CDN_HOSTS.some((h) => url.hostname.includes(h))) {
    e.respondWith(
      caches.open(CACHE_CDN).then(async (cache) => {
        const hit = await cache.match(e.request);
        if (hit) return hit;
        const resp = await fetch(e.request);
        if (resp.ok) cache.put(e.request, resp.clone());
        return resp;
      })
    );
    return;
  }

  // 同源音频：Cache First
  if (url.origin === location.origin && url.pathname.startsWith('/audio/')) {
    e.respondWith(
      caches.open(CACHE_AUDIO).then(async (cache) => {
        const hit = await cache.match(e.request);
        if (hit) return hit;
        const resp = await fetch(e.request);
        if (resp.ok) cache.put(e.request, resp.clone());
        return resp;
      })
    );
    return;
  }

  // 其他 /api/ GET 请求（sessions/episodes/arena 等动态数据）→ 不拦截，直连网络
  if (url.origin === location.origin && url.pathname.startsWith('/api/')
      && !/^\/api\/(nodes|edges|tests)$/.test(url.pathname)) {
    return;
  }

  // 同源只读 API：Network First，失败回退缓存
  if (url.origin === location.origin && /^\/api\/(nodes|edges|tests)$/.test(url.pathname)) {
    e.respondWith(
      caches.open(CACHE_DATA).then(async (cache) => {
        try {
          const resp = await fetch(e.request);
          if (resp.ok) cache.put(e.request, resp.clone());
          return resp;
        } catch (err) {
          const hit = await cache.match(e.request);
          if (hit) return hit;
          throw err;
        }
      })
    );
    return;
  }

  // 核心静态资源：Cache First，后台更新
  if (url.origin === location.origin) {
    e.respondWith(
      caches.open(CACHE_CORE).then(async (cache) => {
        const hit = await cache.match(e.request);
        const fetching = fetch(e.request).then((resp) => {
          if (resp.ok) cache.put(e.request, resp.clone());
          return resp;
        }).catch(() => null);
        return hit || fetching.then((r) => r || new Response('离线且未缓存', { status: 503 }));
      })
    );
  }
});
