// E.V.A. v1.1 — interface (chat em tempo real, aprovações, memórias, agenda, registro, ajustes)
'use strict';
const $ = s => document.querySelector(s);
const msgs = $('#msgs');
const E = {
  ws: null, conectado: false, tentativas: 0, desligada: false, trabalhando: false,
  bolha: null, bolhaTexto: '', renderPendente: false, pronto: false, buffer: [],
  acoes: {}, cards: {}, ultimaEva: '', pane: 'chat', logPagina: 1, tipos: false, timers: [],
  pc: null, pcSrcModo: '', telaWS: null, quadroTs: 0, parear: null
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
      if ((ev.ferramenta || '').startsWith('computador_')) acaoPC(ev);
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
      fecharBolha(false); status(null); setTrabalhando(false); acaoPC(null);
      if (ev.status === 'cancelada') addSis('⏹ Parei.');
      Object.entries(E.cards).forEach(([id, d]) => { if (!d.dataset.fim) resolverCard(id, 'encerrada'); });
      if (E.pane === 'log') carregarLog(true);
      document.querySelectorAll('.m.u.fila').forEach((x, i) => { if (i === 0) x.classList.remove('fila'); });
      break;
    case 'lembrete':
      addSis(ev.texto, 'lem'); notificar(ev.texto, true);
      if (E.pane === 'agenda') carregarLembretes(); break;
    case 'tela':
      if (telaVisivel() && !pcAoVivoAtivo()) carregarPrint(); break;
    case 'computador': aplicarPC(ev); break;
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
  if (s.computador) aplicarPC(s.computador);
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
  if (p === 'chat') { document.body.classList.remove('lado'); rolar(true); sincronizarFrame(); return; }
  document.body.classList.add('lado');
  document.querySelectorAll('.pane').forEach(x => x.classList.toggle('on', x.id === p));
  sincronizarFrame();
  loaders[p] && loaders[p]();
}
document.querySelectorAll('.tabs button,nav button').forEach(b => b.onclick = () => abrir(b.dataset.p));

// Tela: o computador AO VIVO — teu PC Windows (quadros pelo /ws/tela) ou o desktop da nuvem (noVNC num iframe);
// sem nenhum, os prints do navegador reserva
const desktop = () => matchMedia('(min-width:861px)').matches;
function telaVisivel() { return $('#tela').classList.contains('on') && (desktop() || E.pane === 'tela') && !document.hidden; }
const pcWindows = () => !!(E.pc && E.pc.backend === 'windows');
function pcAoVivoAtivo() {
  if (!E.pc || !E.pc.configurado || !E.pc.online) return false;
  return pcWindows() ? true : !!(E.pc.ao_vivo && E.pc.vnc_url);
}

async function carregarTela(verificar) {
  try { E.pc = Object.assign(E.pc || {}, await jget('/api/computador' + (verificar ? '?verificar=1' : ''))); } catch (_) { }
  if (E.pc && E.pc.download_url) document.querySelectorAll('#pcBaixar,#dispBaixar').forEach(a => a.href = E.pc.download_url);
  renderPC();
  if (!pcAoVivoAtivo()) carregarPrint();
}
async function carregarPrint() {
  try {
    const r = await api('/api/tela');
    const img = $('#telaImg');
    if (r.status === 200) { const b = await r.blob(); if (img.src) URL.revokeObjectURL(img.src); img.src = URL.createObjectURL(b); img.hidden = false; $('#telaVazia').hidden = true; }
    else { img.hidden = true; $('#telaVazia').hidden = false; }
  } catch (_) { }
}
function aplicarPC(st) {
  if (!st) return;
  const chave = x => x ? `${x.configurado}|${x.online}|${x.controle_gabriel}|${x.backend}|${x.windows ? x.windows.pausado_no_pc + '|' + x.windows.conectado : ''}` : '';
  const antes = chave(E.pc);
  E.pc = Object.assign(E.pc || {}, st);
  if (pcWindows() && E.pc.online && E.parear) pareamentoConcluido();
  if (antes === chave(E.pc)) return;
  // os links do iframe (com a senha da tela) vêm do /api/computador: busca de novo quando algo importante muda
  if (E.pc.configurado && E.pc.online && !pcWindows() && !E.pc.vnc_url) carregarTela(); else renderPC();
}
function renderPC() {
  const pc = E.pc || {};
  const win = pcWindows();
  const w = pc.windows || {};
  const aoVivo = pcAoVivoAtivo();
  $('#pcBox').hidden = !aoVivo;
  $('#pcNao').hidden = !!pc.configurado || !E.pc || !!E.parear;
  $('#pcOffline').hidden = !(pc.configurado && pc.verificado && !pc.online) || !!E.parear;
  if (win) {
    $('#pcOfflineTitulo').textContent = `🖥️ ${w.nome || 'Teu PC'} não está conectado agora`;
    $('#pcOfflineTexto').textContent = 'Abra o EVA-Agente no PC (ícone roxo perto do relógio). Com o PC ligado e com internet, ele conecta sozinho em alguns segundos. Para trocar de PC ou cortar o acesso: Ajustes → Meu PC.';
  } else {
    $('#pcOfflineTitulo').textContent = '🖥️ O computador da Eva não está respondendo';
    $('#pcOfflineTexto').textContent = 'Ele está configurado, mas não consegui falar com ele agora. Pode estar reiniciando (leva 1–2 minutos) ou a máquina na nuvem está desligada.';
  }
  $('#telaAntiga').hidden = aoVivo;
  $('#telaAntigaDica').textContent = pc.configurado && pc.online && !pc.ao_vivo
    ? 'Prints do computador da Eva (para ver ao vivo, configure EVA_PC_VNC_SENHA).'
    : 'O navegador da Eva. Quando ela navega, o print aparece aqui.';
  $('#pcFrame').hidden = win;
  $('#pcImg').hidden = !win || !E.quadroTs;
  $('#pcEsperando').hidden = !win || !!E.quadroTs || !!w.pausado_no_pc;
  $('#pcPausado').hidden = !(win && w.pausado_no_pc);
  $('#pcPausadoTexto').textContent = `A Eva está pausada no teu PC${w.motivo_pausa ? ' (' + w.motivo_pausa + ')' : ''}.`;
  const controle = !!pc.controle_gabriel;
  const b = $('#pcControle');
  b.textContent = controle ? '🤖 Devolver pra Eva' : '🖐️ Assumir controle';
  b.classList.toggle('ativo', controle);
  $('#pcControleAviso').hidden = !controle;
  $('#pcMoldura').classList.toggle('controle', controle && win);
  $('#pcDigitar').hidden = !(controle && win);
  $('#pcDica').textContent = controle
    ? (win ? 'Você está no controle: toque/clique na imagem para clicar no PC (botão direito = menu, rodinha = rolar) e digite aqui embaixo ou no teclado. Quando terminar, devolva pra Eva.'
      : 'Você está usando o computador (mouse e teclado). Quando terminar, devolva para a Eva continuar.')
    : 'Você está só assistindo. Para fazer login ou mexer você mesmo, toque em “Assumir controle”.';
  atualizarSeloAoVivo();
  $('#pcStatus').textContent = !pc.configurado ? '' : !pc.online ? 'sem conexão' : (win && w.pausado_no_pc) ? 'pausada no PC' :
    (controle ? 'você no controle' : (pc.acao_atual ? 'a Eva está usando' : (win ? (w.nome || 'conectado') : 'conectado')));
  sincronizarFrame();
}
function atualizarSeloAoVivo() {
  const vivo = pcAoVivoAtivo() && (!pcWindows() || (E.quadroTs && Date.now() - E.quadroTs < 8000));
  $('#pcAoVivo').classList.toggle('off', !vivo);
}
setInterval(() => { if (pcWindows()) atualizarSeloAoVivo(); }, 1000);

// liga a imagem ao vivo só quando a Tela está visível (economiza internet e bateria) e troca o modo assistir/controlar
function sincronizarFrame() {
  const f = $('#pcFrame');
  const pc = E.pc || {};
  const precisa = pcAoVivoAtivo() && telaVisivel();
  // PC Windows: WebSocket de quadros (o PC só transmite enquanto alguém assiste)
  if (pcWindows() && precisa) abrirTelaWS(); else fecharTelaWS();
  if (!f) return;
  if (pcWindows() || !precisa) {
    if (f.getAttribute('src') && f.getAttribute('src') !== 'about:blank') f.src = 'about:blank';
    E.pcSrcModo = '';
    return;
  }
  const modo = pc.controle_gabriel ? 'controle' : 'assistir';
  const url = modo === 'controle' ? pc.vnc_url_controle : pc.vnc_url;
  if (url && E.pcSrcModo !== modo) { f.src = url; E.pcSrcModo = modo; }
}
function abrirTelaWS() {
  if (E.telaWS && E.telaWS.readyState <= 1) return;
  clearTimeout(E.telaWSRetry);
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/tela');
  ws.binaryType = 'blob';
  E.telaWS = ws;
  ws.onmessage = ev => {
    if (typeof ev.data === 'string') {
      try { const m = JSON.parse(ev.data); if (m.tipo === 'erro') toast(m.texto, true); } catch (_) { }
      return;
    }
    const img = $('#pcImg');
    const antigo = img.src;
    img.src = URL.createObjectURL(ev.data);
    if (antigo && antigo.startsWith('blob:')) setTimeout(() => URL.revokeObjectURL(antigo), 1000);
    const primeiro = !E.quadroTs;
    E.quadroTs = Date.now();
    if (primeiro) renderPC(); else atualizarSeloAoVivo();
  };
  ws.onclose = () => {
    if (E.telaWS === ws) E.telaWS = null;
    if (pcWindows() && pcAoVivoAtivo() && telaVisivel()) E.telaWSRetry = setTimeout(sincronizarFrame, 2500);
  };
}
function fecharTelaWS() {
  clearTimeout(E.telaWSRetry);
  if (E.telaWS) { const ws = E.telaWS; E.telaWS = null; try { ws.close(); } catch (_) { } }
}
function enviarTela(o) { if (E.telaWS && E.telaWS.readyState === 1) { E.telaWS.send(JSON.stringify(o)); return true; } return false; }

// controle remoto pela imagem (só com "Assumir controle")
function pontoNaImagem(ev) {
  const img = $('#pcImg');
  const r = img.getBoundingClientRect();
  const nw = img.naturalWidth || 16, nh = img.naturalHeight || 10;
  const fator = Math.min(r.width / nw, r.height / nh);
  const w = nw * fator, h = nh * fator, ox = r.left + (r.width - w) / 2, oy = r.top + (r.height - h) / 2;
  const x = (ev.clientX - ox) / w, y = (ev.clientY - oy) / h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x: Math.round(x * 1000), y: Math.round(y * 1000) };
}
const podeControlar = () => pcWindows() && E.pc && E.pc.controle_gabriel;
let cliqueT = null;
$('#pcImg').addEventListener('click', ev => {
  if (!podeControlar()) return;
  const p = pontoNaImagem(ev); if (!p) return;
  $('#pcMoldura').focus({ preventScroll: true });
  if (cliqueT) { clearTimeout(cliqueT); cliqueT = null; enviarTela({ tipo: 'mouse', acao: 'duplo', ...p }); return; }
  cliqueT = setTimeout(() => { cliqueT = null; enviarTela({ tipo: 'mouse', acao: 'clicar', ...p }); }, 260);
});
$('#pcImg').addEventListener('contextmenu', ev => {
  if (!podeControlar()) return;
  ev.preventDefault();
  const p = pontoNaImagem(ev); if (p) enviarTela({ tipo: 'mouse', acao: 'direito', ...p });
});
let rodaT = 0;
$('#pcImg').addEventListener('wheel', ev => {
  if (!podeControlar()) return;
  ev.preventDefault();
  if (Date.now() - rodaT < 120) return; rodaT = Date.now();
  const p = pontoNaImagem(ev) || { x: 500, y: 500 };
  enviarTela({ tipo: 'rolar', dy: ev.deltaY > 0 ? 3 : -3, ...p });
}, { passive: false });
const TECLAS_PC = { Enter: 'enter', Backspace: 'backspace', Tab: 'tab', Escape: 'esc', Delete: 'delete', ArrowUp: 'up', ArrowDown: 'down',
  ArrowLeft: 'left', ArrowRight: 'right', Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown', ' ': 'space' };
let textoPend = '', textoT = null;
function mandarTexto() { if (textoPend) { enviarTela({ tipo: 'texto', texto: textoPend }); textoPend = ''; } }
$('#pcMoldura').addEventListener('keydown', ev => {
  if (!podeControlar() || ev.target !== $('#pcMoldura')) return;
  const mod = (ev.ctrlKey ? 'ctrl+' : '') + (ev.altKey ? 'alt+' : '') + (ev.metaKey ? 'win+' : '');
  if (!mod && ev.key.length === 1 && ev.key !== ' ') {
    textoPend += ev.key; clearTimeout(textoT); textoT = setTimeout(mandarTexto, 250); ev.preventDefault(); return;
  }
  const nome = TECLAS_PC[ev.key] || (/^F\d{1,2}$/.test(ev.key) ? ev.key.toLowerCase() : (mod && ev.key.length === 1 ? ev.key.toLowerCase() : null));
  if (!nome) return;
  ev.preventDefault(); mandarTexto();
  enviarTela({ tipo: 'tecla', teclas: mod + nome });
});
$('#pcDigitarTxt').addEventListener('keydown', ev => {
  if (ev.key !== 'Enter') return;
  ev.preventDefault();
  const t = ev.target.value; if (!t) { enviarTela({ tipo: 'tecla', teclas: 'enter' }); return; }
  enviarTela({ tipo: 'texto', texto: t }); ev.target.value = '';
});
document.querySelectorAll('#pcDigitar [data-tecla]').forEach(b => b.onclick = () => {
  const t = $('#pcDigitarTxt').value;
  if (t) { enviarTela({ tipo: 'texto', texto: t }); $('#pcDigitarTxt').value = ''; }
  enviarTela({ tipo: 'tecla', teclas: b.dataset.tecla });
});

function acaoPC(ev) {
  const box = $('#pcAcao');
  if (!ev) { box.hidden = true; if (E.pc) { E.pc.acao_atual = null; renderPC(); } return; }
  const a = argsCurtos(ev.args);
  $('#pcAcaoTexto').textContent = `${ev.rotulo || ev.ferramenta}${a ? ' — ' + a : ''}`;
  box.hidden = false;
  if (E.pc) { E.pc.acao_atual = ev.rotulo; $('#pcStatus').textContent = E.pc.controle_gabriel ? 'você no controle' : 'a Eva está usando'; }
}
async function trocarControle(gabriel) {
  try {
    const r = await (await api('/api/computador/controle', { method: 'POST', json: { gabriel } })).json();
    E.pc = Object.assign(E.pc || {}, r);
    await carregarTela();       // links novos e iframe no modo certo (assistir/controlar)
    if (gabriel && pcWindows()) $('#pcMoldura').focus({ preventScroll: true });
    toast(gabriel ? 'Você está no controle. A Eva espera você devolver.' : 'Controle devolvido pra Eva. 🤖');
  } catch (_) { }
}

// Conectar meu PC (código de uso único que vale 10 minutos)
async function parearPC() {
  try {
    const d = await (await api('/api/pc/parear', { method: 'POST' })).json();
    E.parear = { fim: Date.now() + d.validade_s * 1000 };
    $('#pcCodigo').textContent = d.codigo;
    $('#pcEndereco').textContent = location.origin;
    $('#pcParear').hidden = false;
    abrir('tela');
    clearInterval(E.parearT);
    E.parearT = setInterval(async () => {
      const resta = Math.max(0, Math.round((E.parear.fim - Date.now()) / 1000));
      $('#pcTempo').textContent = `${Math.floor(resta / 60)}:${String(resta % 60).padStart(2, '0')}`;
      if (resta % 3 === 0) { try { aplicarPC(await jget('/api/computador')); } catch (_) { } }
      if (!resta) { cancelarPareamento(false); toast('O código venceu. Toque em “Conectar meu PC” para gerar outro.', true); }
    }, 1000);
    renderPC();
  } catch (_) { }
}
function cancelarPareamento(avisarServidor) {
  clearInterval(E.parearT); E.parear = null; $('#pcParear').hidden = true;
  if (avisarServidor) api('/api/pc/parear', { method: 'DELETE' }).catch(() => { });
  renderPC();
}
function pareamentoConcluido() {
  cancelarPareamento(false);
  toast(`Pronto! ${(E.pc.windows && E.pc.windows.nome) || 'Teu PC'} está conectado à Eva. 🎉`);
  carregarTela();
  if (E.pane === 'ajustes') carregarDispositivos();
}
$('#pcConectar').onclick = parearPC;
$('#dispConectar').onclick = parearPC;
$('#pcParearCancelar').onclick = () => cancelarPareamento(true);

async function carregarDispositivos() {
  const box = $('#dispLista');
  let lista = [];
  try { lista = await jget('/api/pc/dispositivos'); } catch (_) { return; }
  box.innerHTML = lista.length ? '' : '<p class="hint">Nenhum PC conectado ainda.</p>';
  lista.forEach(d => {
    const row = el('div', 'disp');
    const visto = d.ultimo_contato ? new Date(d.ultimo_contato).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : 'nunca';
    row.innerHTML = `<span class="${d.conectado ? 'on' : 'off'}">●</span><div class="x">${esc(d.nome)}<small>${d.conectado ? 'conectado agora' : 'desligado · visto ' + esc(visto)}${d.versao ? ' · agente v' + esc(d.versao) : ''}</small></div>`;
    const b = el('button', 'btn perigo'); b.textContent = 'Desconectar';
    b.onclick = async () => {
      if (!confirm(`Desconectar “${d.nome}”? A Eva perde o acesso a esse PC na hora (para voltar, precisa de um código novo).`)) return;
      await api(`/api/pc/dispositivos/${d.id}`, { method: 'DELETE' });
      toast('PC desconectado.'); carregarDispositivos(); carregarTela();
    };
    row.appendChild(b); box.appendChild(row);
  });
}

$('#pcControle').onclick = () => trocarControle(!(E.pc && E.pc.controle_gabriel));
$('#pcDevolver').onclick = () => trocarControle(false);
$('#pcTentar').onclick = () => carregarTela(true);
$('#pcGrande').onclick = () => document.body.classList.toggle('tela-grande');
function sairCheia() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => { });
  $('#pcMoldura').classList.remove('cheia'); $('#pcSairCheia').hidden = true;
}
$('#pcCheia').onclick = async () => {
  const m = $('#pcMoldura');
  try {
    if (m.requestFullscreen) { await m.requestFullscreen(); return; }
    if (m.webkitRequestFullscreen) { m.webkitRequestFullscreen(); return; }
  } catch (_) { }
  m.classList.add('cheia'); $('#pcSairCheia').hidden = false;   // iPhone: tela cheia "de mentirinha"
};
$('#pcSairCheia').onclick = sairCheia;
$('#pcFrame').addEventListener('load', () => {
  if (pcAoVivoAtivo() && E.pcSrcModo) $('#pcStatus').textContent = E.pc.controle_gabriel ? 'você no controle' : 'conectado';
});
document.addEventListener('visibilitychange', sincronizarFrame);
window.addEventListener('resize', sincronizarFrame);
setInterval(() => { if (telaVisivel() && E.pc && E.pc.configurado) carregarTela(); }, 20000);
$('#atualizarTela').onclick = () => carregarTela(true);


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
  carregarDispositivos();
  $('#persona').value = persona.texto;
  $('#personaInfo').textContent = persona.padrao ? '(usando a personalidade padrão)' : '(personalizada)';
  if (est) {
    $('#info').textContent = [
      `Versão: ${est.versao}`, `Modelo em uso: ${est.modelo || '—'}`,
      'Modelos: ' + (est.modelos || []).map(m => `${m.modelo} (${m.status})`).join(', '),
      `Streaming: ${est.streaming ? 'sim' : 'não'} · Busca por significado (embeddings): ${est.embeddings ? 'sim' : 'não, usando palavras-chave'}`,
      `Banco: ${est.banco} · Chave da IA: ${est.chave_configurada ? 'configurada' : 'FALTANDO'}`,
      `Navegador: ${est.navegador_aberto ? 'aberto' : 'fechado'}`,
      `Computador: ${!est.computador || !est.computador.configurado ? 'não configurado' : `${est.computador.backend === 'windows' ? 'teu PC (' + ((est.computador.windows || {}).nome || 'Windows') + ')' : 'desktop na nuvem'} — ${est.computador.online ? `ligado (${est.computador.largura}x${est.computador.altura})` : 'fora do ar'}`}`
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
carregarTela();
setInterval(() => { if (!document.hidden) estado(); }, 30000);

$('#limparConversa').onclick = async () => {
  if (!confirm('Apagar o histórico da conversa? As memórias da Eva continuam guardadas.')) return;
  await api('/api/historico', { method: 'DELETE' });
  msgs.innerHTML = ''; E.bolha = null; E.acoes = {}; E.cards = {}; E.ultimaEva = '';
  toast('Conversa limpa.');
};
