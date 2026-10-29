// Service worker da E.V.A. — deixa instalável (Android/Windows), mostra notificações e nunca prende versão velha.
const VERSAO = 'eva-v1.1.1';
const CASCA = ['/', '/static/app.css?v=1.1.1', '/static/app.js?v=1.1.1', '/static/md.js?v=1.1.1', '/icon-192.png', '/manifest.json'];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(VERSAO).then(c => c.addAll(CASCA)).catch(() => {}));
});
self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== VERSAO) await caches.delete(k);  // apaga caches antigos
    await self.clients.claim();
  })());
});
// Rede primeiro (sempre a versão nova); cache só se estiver sem internet. API e WebSocket nunca passam pelo cache.
self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) return;
  e.respondWith((async () => {
    try {
      const r = await fetch(req, {cache: 'no-store'});
      if (r.ok && (CASCA.includes(url.pathname + url.search) || url.pathname === '/')) {
        const c = await caches.open(VERSAO); c.put(req, r.clone());
      }
      return r;
    } catch (_) {
      const c = await caches.match(req);
      if (c) return c;
      return new Response('<meta name="viewport" content="width=device-width"><h2 style="font-family:sans-serif;color:#e8eaf0;background:#0b0d12;height:100vh;margin:0;display:grid;place-items:center;text-align:center">Sem internet.<br>A Eva volta quando a conexão voltar.</h2>',
        {headers: {'Content-Type': 'text/html; charset=utf-8'}});
    }
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil((async () => {
    const lista = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const c of lista) if ('focus' in c) return c.focus();
    return self.clients.openWindow('/');
  })());
});
