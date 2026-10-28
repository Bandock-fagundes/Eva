// Service worker: deixa a Eva instalável como app (Windows e Android). Sempre busca a versão nova.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).catch(() => new Response('<h2 style="font-family:sans-serif;color:#fff;background:#0d0f14;height:100vh;margin:0;display:grid;place-items:center">Sem internet. A Eva volta quando a conexão voltar.</h2>', {headers: {'Content-Type': 'text/html; charset=utf-8'}})));
});
