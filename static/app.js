// E.V.A. v1.0 — interface (chat em tempo real, aprovações, memórias, agenda, registro, ajustes)
'use strict';
const $ = s => document.querySelector(s);
const msgs = $('#msgs');
const E = {
  ws: null, conectado: false, tentativas: 0, desligada: false, trabalhando: false,
  bolha: null, bolhaTexto: '', renderPendente: false, pronto: false, buffer: [],
  acoes: {}, cards: {}, ultimaEva: '', pane: 'chat', logPagina: 1, tipos: false, timers: []
};

// ------------------------------------------------------------ utilidades
function toast(t, erro) {
  const el = $('#toast'); el.textContent = t; el.classList.toggle('erro', !!erro); el.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove('on'), 3200);
}
async function api(url, opts = {}) {
  if (opts.json !== undefined) { opts.body = JSON.stringify(opts.json); opts.headers = { 'Content-Type': 'application/json' }; delete opts.json; }
  let r;
  try { r = await fetch(url, opts); } catch (e) { toast('Sem conexão com a Eva agora.', true); throw e; }
  if (r.status === 401) { location.href = '/login'; throw new Error('401'); }
  if (!r.ok && r.status !== 204) {
    let msg = 'Algo deu errado (' + r.status + ').';
    try { const j = await r.json(); msg = j.detail || j.erro || msg; } catch (_) { }
    toast(msg, true); throw new Error(msg);
  }
  return r;
}
const jget = async u => (await api(u)).json();
const fmtHora = ts => { const d = new Date(ts * 1000); return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); };
function rolar(forcar) {
  const perto = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 160;
  if (perto || forcar) requestAnimationFrame(() => { msgs.scrollTop = msgs.scrollHeight; });
}
function el(tag, cls, html) { const d = document.createElement(tag); if (cls) d.className = cls; if (html != null) d.innerHTML = html; return d; }
function add(no) { msgs.appendChild(no); rolar(); return no; }

// ------------------------------------------------------------ mensagens
function addUser(t, fila) { const d = el('div', 'm u' + (fila ? ' fila' : '')); d.textContent = t; add(d); rolar(true); return d; }
function addEva(t, cls = '') { const d = el('div', 'm e ' + cls, md(t)); add(d); E.ultimaEva = t; return d; }
function addSis(t, cls = '') { const d = el('div', 's ' + cls); d.textContent = t; return add(d); }

function iniciarBolha() {
  if (!E.bolha) { E.bolha = el('div', 'm e cursor'); E.bolhaTexto = ''; add(E.bolha); }
  return E.bolha;
}
function renderBolha() {
  if (E.renderPendente) return; E.renderPendente = true;
  requestAnimationFrame(() => { E.renderPendente = false; if (E.bolha) { E.bolha.innerHTML = md(E.bolhaTexto); rolar(); } });
}
function fecharBolha(comoNarracao) {
  if (!E.bolha) return;
  E.bolha.classList.remove('cursor');
  if (comoNarracao) E.bolha.classList.add('narr');
  if (!E.bolhaTexto.trim()) E.bolha.remove();
  E.bolha = null; E.bolhaTexto = '';
}
function status(texto) {
  const st = $('#status-tarefa');
  if (texto) { $('#status-texto').textContent = texto; st.hidden = false; } else st.hidden = true;
}
function argsCurtos(a) {
  try {
    const s = Object.entries(a || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ');
    return s.length > 90 ? s.slice(0, 90) + '…' : s;
  } catch (_) { return ''; }
}

function cardAprovacao(ev) {
  const d = el('div', 'card-ap');
  d.innerHTML = `<b>🛡️ A Eva quer: ${esc(ev.rotulo || ev.ferramenta)}</b>` +
    (ev.motivo ? `<div class="motivo">⚠ ${esc(ev.motivo)}</div>` : '') +
    `<pre></pre><div class="bts"><button class="ok">Permitir</button><button class="nao">Negar</button></div><div class="tempo"></div>`;
  d.querySelector('pre').textContent = `${ev.ferramenta}\n` + JSON.stringify(ev.args || {}, null, 2);
  const responder = ok => {
    enviarWS({ tipo: 'aprovacao', id: ev.id, aprovado: ok }) || api('/api/aprovacao', { method: 'POST', json: { id: ev.id, aprovado: ok } });
    resolverCard(ev.id, ok ? 'permitido' : 'negado');
  };
  d.querySelector('.ok').onclick = () => responder(true);
  d.querySelector('.nao').onclick = () => responder(false);
  contador(d, ev.expira_em);
  E.cards[ev.id] = d; add(d); rolar(true);
  notificar('A Eva precisa da tua aprovação: ' + (ev.rotulo || ev.ferramenta));
}
function cardPergunta(ev) {
  const d = el('div', 'card-ap perg');
  d.innerHTML = `<b>❓ ${esc(ev.pergunta)}</b><div class="bts"></div><form class="resp"><input placeholder="Tua resposta…"><button class="ok">Responder</button></form><div class="tempo"></div>`;
  const responder = txt => {
    if (!txt.trim()) return;
    enviarWS({ tipo: 'resposta', id: ev.id, texto: txt }) || api('/api/resposta', { method: 'POST', json: { id: ev.id, texto: txt } });
    resolverCard(ev.id, 'respondida', txt);
  };
  (ev.opcoes || []).forEach(o => { const b = el('button', 'op'); b.textContent = o; b.onclick = () => responder(o); d.querySelector('.bts').appendChild(b); });
  d.querySelector('form').onsubmit = e => { e.preventDefault(); responder(d.querySelector('input').value); };
  contador(d, ev.expira_em);
  E.cards[ev.id] = d; add(d); rolar(true);
  notificar('A Eva te fez uma pergunta: ' + ev.pergunta);
  setTimeout(() => d.querySelector('input') && d.querySelector('input').focus(), 50);
}
function contador(d, expira) {
  if (!expira) return;
  const t = d.querySelector('.tempo');
  const tick = () => {
    if (!d.isConnected || d.dataset.fim) return clearInterval(iv);
    const s = Math.max(0, Math.round(expira - Date.now() / 1000));
    t.textContent = s > 0 ? `expira em ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : 'expirando…';
  };
  const iv = setInterval(tick, 1000); tick();
}
function resolverCard(id, st, resp) {
  const d = E.cards[id]; if (!d || d.dataset.fim) return;
  d.dataset.fim = st;
  d.querySelectorAll('.bts,.resp,.tempo').forEach(x => x.remove());
  const txt = { permitido: '✅ Permitido', negado: '❌ Negado', expirada: '⌛ Expirou (contou como negado)', respondida: '💬 Respondido', encerrada: 'Tarefa encerrada' }[st] || st;
  d.appendChild(el('div', 'fim', esc(txt + (resp ? ': ' + resp : ''))));
}

// ------------------------------------------------------------ eventos do servidor
function tratar(ev) {
  switch (ev.tipo) {
    case 'estado': aplicarEstado(ev); break;
    case 'pensando':
      fecharBolha(true);
      status(ev.limite ? 'Resumindo o que fiz até aqui…' : (ev.passo > 1 ? `Pensando (passo ${ev.passo})…` : 'Pensando…'));
      setTrabalhando(true); break;
    case 'token':
      if (ev.reset) { E.bolhaTexto = ''; if (E.bolha) E.bolha.innerHTML = ''; break; }
      iniciarBolha(); E.bolhaTexto += ev.texto || ''; renderBolha(); break;
    case 'narracao':
      if (E.bolha) { E.bolhaTexto = ev.texto; E.bolha.innerHTML = md(ev.texto); fecharBolha(true); }
      else addEva(ev.texto, 'narr');
      break;
    case 'acao': {
      fecharBolha(true);
      status(`${ev.rotulo || ev.ferramenta}…`);
      const d = el('details', 'acao');
      d.innerHTML = `<summary><span class="st">⏳</span><span></span></summary><pre></pre>`;
      d.querySelector('summary span:last-child').textContent = `${ev.rotulo || ev.ferramenta}${argsCurtos(ev.args) ? ' — ' + argsCurtos(ev.args) : ''}`;
      d.querySelector('pre').textContent = JSON.stringify(ev.args || {}, null, 2);
      if (ev.id) E.acoes[ev.id] = d; add(d); break;
    }
    case 'resultado': {
      const d = ev.id && E.acoes[ev.id];
      if (d) { d.querySelector('.st').textContent = ev.ok === false ? '⚠️' : '✓'; d.querySelector('pre').textContent = ev.texto || ''; }
      status('Pensando…'); break;
    }
    case 'aprovacao':
      if (ev.ferramenta) cardAprovacao(ev); else if (ev.status) resolverCard(ev.id, ev.status);
      break;
    case 'pergunta':
      if (ev.pergunta) cardPergunta(ev); else if (ev.status) resolverCard(ev.id, ev.status, ev.resposta);
      break;
    case 'resposta':
      if (E.bolha) { E.bolhaTexto = ev.texto; E.bolha.innerHTML = md(ev.texto); fecharBolha(false); E.ultimaEva = ev.texto; }
      else if (ev.texto !== E.ultimaEva) addEva(ev.texto);
      notificar(ev.texto); break;
    case 'erro':
      fecharBolha(true); addSis('⚠ ' + ev.texto, 'err'); break;
    case 'fim':
      fecharBolha(false); status(null); setTrabalhando(false);
      if (ev.status === 'cancelada') addSis('⏹ Parei.');
      Object.entries(E.cards).forEach(([id, d]) => { if (!d.dataset.fim) resolverCard(id, 'encerrada'); });
      if (E.pane === 'log') carregarLog(true);
      document.querySelectorAll('.m.u.fila').forEach((x, i) => { if (i === 0) x.classList.remove('fila'); });
      break;
    case 'lembrete':
      addSis(ev.texto, 'lem'); notificar(ev.texto, true);
      if (E.pane === 'agenda') carregarLembretes(); break;
    case 'tela':
      if (E.pane === 'tela' || matchMedia('(min-width:861px)').matches) carregarTela(); break;
    case 'ping': enviarWS({ tipo: 'pong' }); break;
  }
}

function aplicarEstado(s) {
  E.desligada = !!s.desligada;
  if (s.modelo) $('#modelo').textContent = s.modelo;
  const k = $('#kill');
  k.classList.toggle('ligar', E.desligada);
  k.querySelector('span').textContent = E.desligada ? 'Ligar' : 'Desligar';
  k.title = E.desligada ? 'Ligar a Eva de novo' : 'Desligar a Eva (para tudo na hora)';
  setTrabalhando(!!s.trabalhando);
  atualizarDot();
  if (s.chave_configurada === false) aviso('Falta configurar a chave da IA (EVA_LLM_API_KEY) no servidor.', true);
  else if (E.desligada) aviso('A Eva está DESLIGADA. Nada roda até você ligar de novo.', true);
  else if (E.conectado) aviso(null);
}
function setTrabalhando(v) { E.trabalhando = v; if (!v) status(null); atualizarDot(); }
function atualizarDot() {
  const d = $('#dot'); d.className = '';
  if (!E.conectado) d.classList.add('descon'); else if (E.desligada) d.classList.add('off'); else if (E.trabalhando) d.classList.add('trab');
}
function aviso(t, erro) { const a = $('#aviso'); if (!t) { a.hidden = true; return; } a.textContent = t; a.classList.toggle('erro', !!erro); a.hidden = false; }

// ------------------------------------------------------------ conexão
function enviarWS(o) { if (E.ws && E.ws.readyState === 1) { E.ws.send(JSON.stringify(o)); return true; } return false; }

function conectar() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  E.ws = ws; E.pronto = false; E.buffer = [];
  let primeiro = true;
  ws.onopen = () => { E.conectado = true; E.tentativas = 0; atualizarDot(); aviso(null); };
  ws.onmessage = async e => {
    let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
    if (primeiro && ev.tipo === 'estado') {
      primeiro = false;
      await sincronizar(ev);            // histórico + replay da tarefa em andamento
      E.pronto = true; E.buffer.splice(0).forEach(tratar);
      return;
    }
    if (!E.pronto) { E.buffer.push(ev); return; }
    tratar(ev);
  };
  ws.onclose = e => {
    E.conectado = false; atualizarDot();
    if (e.code === 4401) { location.href = '/login'; return; }
    E.tentativas++;
    const espera = Math.min(10000, 800 * 2 ** Math.min(E.tentativas, 4));
    aviso(`Conexão perdida — reconectando em ${Math.round(espera / 1000)}s… (o que a Eva estiver fazendo continua rodando)`);
    clearTimeout(E.reconTimer); E.reconTimer = setTimeout(conectar, espera);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) { } };
}
// ping do cliente (mantém a conexão viva em redes móveis / proxy do Render)
setInterval(() => enviarWS({ tipo: 'ping' }), 20000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && (!E.ws || E.ws.readyState > 1)) { clearTimeout(E.reconTimer); conectar(); }
});

async function sincronizar(est) {
  let hist = [];
  try { hist = await jget('/api/historico?n=80'); } catch (_) { }
  msgs.innerHTML = ''; E.bolha = null; E.acoes = {}; E.cards = {}; E.ultimaEva = '';
  if (!hist.length) addEva('Oi! Eu sou a **E.V.A.**, tua IA pessoal. Manda o que precisar — pesquiso, lembro, navego e te aviso das coisas.');
  hist.forEach(x => {
    if (x.papel === 'user') addUser(x.conteudo);
    else if ((x.conteudo || '').startsWith('⏰ Lembrete')) addSis(x.conteudo, 'lem');
    else addEva(x.conteudo);
  });
  aplicarEstado(est);
  const t = est.tarefa;
  if (t && est.trabalhando) {
    const pend = new Set((t.pendentes || []).map(p => p.id));
    (t.eventos || []).forEach(ev => { if (!['resposta', 'fim', 'estado'].includes(ev.tipo)) tratar(ev); });
    Object.entries(E.cards).forEach(([id, d]) => { if (!pend.has(id) && !d.dataset.fim) resolverCard(id, 'encerrada'); });
    if (t.texto_parcial) { iniciarBolha(); E.bolhaTexto = t.texto_parcial; renderBolha(); }
    status('Trabalhando…');
  }
  (est.fila || []).forEach(f => addUser(f.texto, true));
  rolar(true);
}

// ------------------------------------------------------------ envio
const caixa = $('#t');
function ajustarCaixa() { caixa.style.height = 'auto'; caixa.style.height = Math.min(160, caixa.scrollHeight) + 'px'; }
caixa.addEventListener('input', ajustarCaixa);
caixa.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && matchMedia('(min-width:861px)').matches) { e.preventDefault(); $('#f').requestSubmit(); }
});
$('#f').onsubmit = e => {
  e.preventDefault();
  const t = caixa.value.trim(); if (!t) return;
  if (E.desligada) { toast('A Eva está desligada. Liga no botão lá em cima.', true); return; }
  if (!enviarWS({ tipo: 'mensagem', texto: t })) { toast('Sem conexão agora — tentando reconectar…', true); return; }
  addUser(t, E.trabalhando); caixa.value = ''; ajustarCaixa();
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
};
$('#parar').onclick = () => { enviarWS({ tipo: 'parar' }) || api('/api/parar', { method: 'POST' }); status('Parando…'); };
$('#kill').onclick = async () => {
  const estavaDesligada = E.desligada;
  if (!estavaDesligada && !confirm('Desligar a Eva agora? Tudo que ela estiver fazendo para na hora e ela fica desligada até você ligar.')) return;
  await api(estavaDesligada ? '/api/ligar' : '/api/desligar', { method: 'POST' });
  toast(estavaDesligada ? 'Eva ligada.' : 'Eva DESLIGADA. Tudo parado.');
  estado();
};
async function estado() { try { const s = await jget('/api/estado'); aplicarEstado(s); E.ultimoEstado = s; return s; } catch (_) { } }

// ------------------------------------------------------------ notificações
async function notificar(texto, forcar) {
  if (!document.hidden && !forcar) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const corpo = String(texto).replace(/[*_`#>]/g, '').slice(0, 160);
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg) reg.showNotification('E.V.A.', { body: corpo, icon: '/icon-192.png', badge: '/icon-192.png', tag: 'eva', renotify: true });
    else new Notification('E.V.A.', { body: corpo, icon: '/icon-192.png' });
  } catch (_) { }
}
$('#notifBtn').onclick = async () => {
  if (!('Notification' in window)) return toast('Este navegador não suporta notificações.', true);
  const p = await Notification.requestPermission();
  toast(p === 'granted' ? 'Notificações ativadas (funcionam com o app aberto ou em segundo plano).' : 'Notificações não permitidas.', p !== 'granted');
};

// ------------------------------------------------------------ abas
const loaders = { tela: carregarTela, mem: carregarMem, agenda: carregarLembretes, log: () => carregarLog(true), ajustes: carregarAjustes };
function abrir(p) {
  E.pane = p;
  document.querySelectorAll('.tabs button,nav button').forEach(b => b.classList.toggle('on', b.dataset.p === p));
  if (p === 'chat') { document.body.classList.remove('lado'); rolar(true); return; }
  document.body.classList.add('lado');
  document.querySelectorAll('.pane').forEach(x => x.classList.toggle('on', x.id === p));
  loaders[p] && loaders[p]();
}
document.querySelectorAll('.tabs button,nav button').forEach(b => b.onclick = () => abrir(b.dataset.p));

// Tela
async function carregarTela() {
  try {
    const r = await api('/api/tela');
    const img = $('#telaImg');
    if (r.status === 200) { const b = await r.blob(); if (img.src) URL.revokeObjectURL(img.src); img.src = URL.createObjectURL(b); img.hidden = false; $('#telaVazia').hidden = true; }
    else { img.hidden = true; $('#telaVazia').hidden = false; }
  } catch (_) { }
}
$('#atualizarTela').onclick = carregarTela;

// Memórias
let categorias = [];
async function carregarMem() {
  if (!categorias.length) {
    categorias = await jget('/api/memorias/categorias');
    $('#memNovaCat').innerHTML = categorias.map(c => `<option>${c}</option>`).join('');
  }
  const q = $('#memBusca').value.trim();
  const lista = await jget('/api/memorias' + (q ? '?busca=' + encodeURIComponent(q) : ''));
  const box = $('#memLista'); box.innerHTML = '';
  if (!lista.length) { box.innerHTML = `<p class="vazio">${q ? 'Nada encontrado.' : 'Ainda não guardei nada. Conversa comigo que eu vou aprendendo!'}</p>`; return; }
  const grupos = {};
  lista.forEach(m => (grupos[q ? 'resultados' : (m.categoria || 'geral')] ||= []).push(m));
  Object.keys(grupos).sort().forEach(g => {
    box.appendChild(el('div', 'grupo', esc(g)));
    grupos[g].forEach(m => box.appendChild(itemMem(m)));
  });
}
function itemMem(m) {
  const d = el('div', 'mem');
  const imp = Math.max(1, Math.min(5, m.importancia || 3));
  d.innerHTML = `<div class="txt"></div><div class="meta"><span class="cat">${esc(m.categoria || 'geral')}</span>
    <span class="estrelas" title="importância">${'★'.repeat(imp)}</span><span>${esc((m.atualizada || m.ts || '').slice(0, 16))}</span>
    <span>${esc(m.origem || '')}</span><span class="acoes"><button class="ed">Editar</button><button class="del">Apagar</button></span></div>`;
  d.querySelector('.txt').textContent = m.conteudo;
  d.querySelector('.del').onclick = async () => {
    if (!confirm('Apagar esta memória?\n\n' + m.conteudo)) return;
    await api('/api/memorias/' + m.id, { method: 'DELETE' }); toast('Memória apagada.'); carregarMem();
  };
  d.querySelector('.ed').onclick = () => {
    const f = el('form', 'card');
    f.innerHTML = `<textarea rows="3" maxlength="2000"></textarea><div class="linha"><select class="c">${categorias.map(c => `<option>${c}</option>`).join('')}</select>
      <select class="i">${[1, 2, 3, 4, 5].map(n => `<option value="${n}">${'★'.repeat(n)}</option>`).join('')}</select>
      <button class="btn">Salvar</button><button type="button" class="btn sec cancel">Cancelar</button></div>`;
    f.querySelector('textarea').value = m.conteudo; f.querySelector('.c').value = m.categoria || 'geral'; f.querySelector('.i').value = imp;
    f.querySelector('.cancel').onclick = () => carregarMem();
    f.onsubmit = async e => {
      e.preventDefault();
      await api('/api/memorias/' + m.id, { method: 'PUT', json: { conteudo: f.querySelector('textarea').value, categoria: f.querySelector('.c').value, importancia: +f.querySelector('.i').value } });
      toast('Memória atualizada.'); carregarMem();
    };
    d.replaceWith(f); f.querySelector('textarea').focus();
  };
  return d;
}
let memT; $('#memBusca').oninput = () => { clearTimeout(memT); memT = setTimeout(carregarMem, 300); };
$('#memNovaBtn').onclick = () => { const f = $('#memNova'); f.hidden = !f.hidden; if (!f.hidden) $('#memNovaTxt').focus(); };
$('#memNova').onsubmit = async e => {
  e.preventDefault();
  const t = $('#memNovaTxt').value.trim(); if (!t) return;
  const r = await (await api('/api/memorias', { method: 'POST', json: { conteudo: t, categoria: $('#memNovaCat').value, importancia: +$('#memNovaImp').value } })).json();
  toast(r.acao === 'nova' ? 'Memória salva.' : 'Já existia algo parecido — atualizei.');
  $('#memNovaTxt').value = ''; $('#memNova').hidden = true; carregarMem();
};

// Agenda
async function carregarLembretes() {
  const todos = $('#lemTodos').checked;
  const lista = await jget('/api/lembretes' + (todos ? '?todos=true' : ''));
  const box = $('#lemLista'); box.innerHTML = '';
  if (!lista.length) { box.innerHTML = '<p class="vazio">Nenhum lembrete agendado.</p>'; return; }
  lista.forEach(l => {
    const d = el('div', 'lem' + (l.status !== 'pendente' ? ' passado' : ''));
    d.innerHTML = `<span class="q">${esc(l.quando_legivel)}</span><span class="x"></span>` +
      (l.status === 'pendente' ? '<button class="btn sec">Cancelar</button>' : `<span class="hint">${esc(l.status)}</span>`);
    d.querySelector('.x').textContent = l.texto;
    const b = d.querySelector('button');
    if (b) b.onclick = async () => { await api('/api/lembretes/' + l.id, { method: 'DELETE' }); toast('Lembrete cancelado.'); carregarLembretes(); };
    box.appendChild(d);
  });
}
$('#lemTodos').onchange = carregarLembretes;
$('#lemForm').onsubmit = async e => {
  e.preventDefault();
  const quando = $('#lemQuando').value.replace('T', ' ');
  await api('/api/lembretes', { method: 'POST', json: { quando, texto: $('#lemTexto').value } });
  toast('Lembrete agendado. ⏰'); $('#lemTexto').value = ''; carregarLembretes();
};
(function () { const d = new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60e3); $('#lemQuando').value = d.toISOString().slice(0, 16); })();

// Registro
async function carregarLog(reiniciar) {
  if (!E.tipos) {
    E.tipos = true;
    try { const tipos = await jget('/api/log/tipos'); $('#logTipo').innerHTML += tipos.map(t => `<option>${esc(t)}</option>`).join(''); } catch (_) { E.tipos = false; }
  }
  if (reiniciar) { E.logPagina = 1; $('#logLista').innerHTML = ''; }
  const p = new URLSearchParams({ pagina: E.logPagina, tamanho: 50 });
  if ($('#logTipo').value) p.set('tipo', $('#logTipo').value);
  if ($('#logBusca').value.trim()) p.set('busca', $('#logBusca').value.trim());
  const d = await jget('/api/log?' + p);
  d.itens.forEach(r => {
    const { id, ts, tipo, ...rest } = r;
    const x = el('div', 'lg');
    x.innerHTML = `<div class="cab"><span class="ts">${esc(ts)}</span><b class="${/erro|falhou|bloque/.test(tipo) ? 'erro' : ''}">${esc(tipo)}</b></div><div class="det"></div>`;
    const det = x.querySelector('.det');
    det.textContent = Object.keys(rest).length ? JSON.stringify(rest, null, 1) : '';
    det.onclick = () => det.classList.toggle('aberto');
    $('#logLista').appendChild(x);
  });
  if (!d.total) $('#logLista').innerHTML = '<p class="vazio">Nada no registro.</p>';
  $('#logMais').hidden = d.pagina * d.tamanho >= d.total;
}
$('#logMais').onclick = () => { E.logPagina++; carregarLog(false); };
$('#logTipo').onchange = () => carregarLog(true);
let logT; $('#logBusca').oninput = () => { clearTimeout(logT); logT = setTimeout(() => carregarLog(true), 350); };

// Ajustes
async function carregarAjustes() {
  const [perms, persona, est] = await Promise.all([jget('/api/permissoes'), jget('/api/persona'), estado()]);
  const box = $('#permLista'); box.innerHTML = '';
  perms.forEach(p => {
    const d = el('div', 'perm');
    d.innerHTML = `<div class="nome">${esc(p.rotulo)} ${p.sensivel ? '⚡' : ''}<small>${esc(p.ferramenta)}${p.valor !== p.padrao ? ' · padrão: ' + esc(p.padrao) : ''}</small></div>
      <select><option value="permitir">✅ permitir</option><option value="perguntar">❔ perguntar</option><option value="bloquear">⛔ bloquear</option></select>`;
    d.title = p.descricao;
    const s = d.querySelector('select'); s.value = p.valor; s.className = 'v-' + p.valor;
    s.onchange = async () => { await api('/api/permissoes', { method: 'POST', json: { ferramenta: p.ferramenta, valor: s.value } }); s.className = 'v-' + s.value; toast('Permissão salva.'); };
    box.appendChild(d);
  });
  $('#persona').value = persona.texto;
  $('#personaInfo').textContent = persona.padrao ? '(usando a personalidade padrão)' : '(personalizada)';
  if (est) {
    $('#info').textContent = [
      `Versão: ${est.versao}`, `Modelo em uso: ${est.modelo || '—'}`,
      'Modelos: ' + (est.modelos || []).map(m => `${m.modelo} (${m.status})`).join(', '),
      `Streaming: ${est.streaming ? 'sim' : 'não'} · Busca por significado (embeddings): ${est.embeddings ? 'sim' : 'não, usando palavras-chave'}`,
      `Banco: ${est.banco} · Chave da IA: ${est.chave_configurada ? 'configurada' : 'FALTANDO'}`,
      `Navegador: ${est.navegador_aberto ? 'aberto' : 'fechado'}`
    ].join('\n');
  }
}
$('#permRestaurar').onclick = async () => { if (!confirm('Voltar todas as permissões para o padrão?')) return; await api('/api/permissoes/restaurar', { method: 'POST' }); toast('Permissões restauradas.'); carregarAjustes(); };
$('#salvarPersona').onclick = async () => { await api('/api/persona', { method: 'POST', json: { texto: $('#persona').value } }); toast('Personalidade salva. Vale a partir da próxima mensagem.'); $('#personaInfo').textContent = '(personalizada)'; };
$('#restaurarPersona').onclick = async () => {
  if (!confirm('Restaurar a personalidade padrão? O texto atual será perdido.')) return;
  const r = await (await api('/api/persona/restaurar', { method: 'POST' })).json();
  $('#persona').value = r.texto; $('#personaInfo').textContent = '(usando a personalidade padrão)'; toast('Personalidade padrão restaurada.');
};
$('#sair').onclick = async () => { await api('/logout', { method: 'POST' }); location.href = '/login'; };

// ------------------------------------------------------------ início
if ('serviceWorker' in navigator) {
  const jaTinha = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.register('/sw.js').then(reg => {
    reg.update();
    reg.addEventListener('updatefound', () => {
      const nw = reg.installing;
      nw && nw.addEventListener('statechange', () => { if (nw.state === 'activated' && jaTinha) toast('E.V.A. atualizada para a versão nova.'); });
    });
  }).catch(() => { });
}
conectar();
estado();
if (matchMedia('(min-width:861px)').matches) carregarTela();
setInterval(() => { if (!document.hidden) estado(); }, 30000);
