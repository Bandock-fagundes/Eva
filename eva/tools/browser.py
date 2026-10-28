"""O 'computador virtual' da Eva: um Chromium (Playwright) que aparece na aba Tela.

- Liga só quando precisa (lazy) e fecha sozinho depois de alguns minutos parado (poupa RAM no Render).
- Um contexto persistente (cookies/logins ficam enquanto o processo vive).
- Se o Chromium morrer, reinicia sozinho e repete a ação uma vez.
- Elementos clicáveis são numerados ([12] Botão "Entrar") para a Eva clicar/digitar pelo número.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..config import BROWSER_HEADLESS, BROWSER_LEVE, BROWSER_OCIOSO_MIN, DATA_DIR, WORKSPACE
from . import tool

log = logging.getLogger("eva.navegador")

JS_ELEMENTOS = r"""
(limite) => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, summary, [role=button], [role=link], '
            + '[role=tab], [role=menuitem], [role=checkbox], [role=option], [contenteditable=""], [contenteditable=true], [onclick]';
  document.querySelectorAll('[data-eva-id]').forEach(e => e.removeAttribute('data-eva-id'));
  const out = []; let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || e.disabled) continue;
    n += 1; e.setAttribute('data-eva-id', String(n));
    const tag = e.tagName.toLowerCase(); const tipo = (e.getAttribute('type') || '').toLowerCase();
    let txt = (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder')
               || e.getAttribute('title') || e.getAttribute('name') || e.getAttribute('alt') || '').trim().replace(/\s+/g, ' ');
    if (tag === 'input' || tag === 'textarea') {
      const lab = e.labels && e.labels[0] ? e.labels[0].innerText.trim() : '';
      txt = (lab ? lab + ' — ' : '') + (e.getAttribute('placeholder') || e.getAttribute('aria-label') || e.getAttribute('name') || '')
            + (e.value && tipo !== 'password' ? ' = "' + String(e.value).slice(0, 40) + '"' : '');
    }
    const visivel = r.top < window.innerHeight && r.bottom > 0;
    out.push({n, tag, tipo, txt: txt.slice(0, 80), href: tag === 'a' ? (e.getAttribute('href') || '').slice(0, 100) : '', visivel});
    if (n >= limite) break;
  }
  return out;
}
"""

JS_INFO_ELEMENTO = r"""
(e) => ({tag: e.tagName.toLowerCase(), tipo: (e.getAttribute('type')||'').toLowerCase(),
  nome: ((e.getAttribute('name')||'') + ' ' + (e.id||'') + ' ' + (e.getAttribute('autocomplete')||'') + ' '
         + (e.getAttribute('placeholder')||'') + ' ' + (e.getAttribute('aria-label')||'')).toLowerCase(),
  texto: (e.innerText || e.value || '').slice(0, 120).toLowerCase()})
"""

RE_COMPRA = re.compile(r"comprar|finalizar|pagar|pagamento|confirmar (pedido|compra|pagamento)|fechar pedido|"
                       r"assinar|contratar|checkout|place order|buy now|pay now|purchase|concluir (compra|pedido)|"
                       r"enviar pedido|doar|transferir|pix", re.I)
RE_CAMPO_SENSIVEL = re.compile(r"password|senha|card|cart[aã]o|cc-|cvv|cvc|cpf|security code|c[oó]digo de seguran", re.I)
RE_PAGINA_SENSIVEL = re.compile(r"checkout|pagamento|payment|carrinho|cart\b|/pay|billing", re.I)


class Navegador:
    def __init__(self):
        self.pw = None
        self.contexto = None
        self.page = None
        self.tela: bytes | None = None
        self.tela_versao = 0
        self.ultimo_uso = 0.0
        self._lock = asyncio.Lock()
        self._vigia: asyncio.Task | None = None
        self._morto = False

    @property
    def aberto(self) -> bool:
        return self.page is not None and not self._morto and not self.page.is_closed()

    async def _iniciar(self):
        from playwright.async_api import async_playwright
        await self._fechar_interno()
        self.pw = await async_playwright().start()
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions",
                "--mute-audio", "--no-first-run"]
        if BROWSER_LEVE:
            args += ["--single-process", "--no-zygote", "--renderer-process-limit=1",
                     "--js-flags=--max-old-space-size=192"]
        perfil = DATA_DIR / "navegador"
        perfil.mkdir(parents=True, exist_ok=True)
        self.contexto = await self.pw.chromium.launch_persistent_context(
            str(perfil), headless=BROWSER_HEADLESS, args=args, locale="pt-BR", timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        self._morto = False
        self.contexto.on("close", lambda *_: setattr(self, "_morto", True))

        async def _bloquear(route):  # mídia pesada não serve pra Eva e gasta RAM
            if route.request.resource_type in ("media", "font"):
                await route.abort()
            else:
                await route.continue_()

        await self.contexto.route("**/*", _bloquear)
        self.page = self.contexto.pages[0] if self.contexto.pages else await self.contexto.new_page()
        self.page.set_default_timeout(15000)
        if self._vigia is None or self._vigia.done():
            self._vigia = asyncio.create_task(self._vigiar_ocioso())

    async def garantir(self):
        if not self.aberto:
            await self._iniciar()
        elif self.page.is_closed():
            self.page = await self.contexto.new_page()
        self.ultimo_uso = time.time()
        return self.page

    async def _vigiar_ocioso(self):
        try:
            while True:
                await asyncio.sleep(30)
                if self.contexto is None:
                    return
                if time.time() - self.ultimo_uso > BROWSER_OCIOSO_MIN * 60:
                    log.info("Navegador ocioso; fechando para poupar memória.")
                    await self.fechar()
                    return
        except asyncio.CancelledError:
            pass

    async def _fechar_interno(self):
        try:
            if self.contexto is not None:
                await asyncio.wait_for(self.contexto.close(), timeout=10)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.pw is not None:
                await asyncio.wait_for(self.pw.stop(), timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self.pw = self.contexto = self.page = None

    async def fechar(self):
        await self._fechar_interno()
        self.tela = None
        v = self._vigia
        self._vigia = None
        if v and not v.done() and v is not asyncio.current_task():
            v.cancel()

    async def executar(self, acao):
        """Roda uma ação; se o Chromium tiver morrido, reinicia e tenta mais uma vez."""
        async with self._lock:
            for tentativa in range(2):
                page = await self.garantir()
                try:
                    return await acao(page)
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    morreu = self._morto or any(s in msg for s in ("Target closed", "has been closed", "Browser closed",
                                                                  "Target page, context or browser", "crash"))
                    if morreu and tentativa == 0:
                        log.warning("Chromium caiu (%s); reiniciando.", msg[:120])
                        await self._fechar_interno()
                        continue
                    raise

    async def screenshot(self, page=None):
        page = page or self.page
        if page is None:
            return
        try:
            self.tela = await page.screenshot(type="jpeg", quality=55, timeout=10000)
            self.tela_versao += 1
        except Exception as e:  # noqa: BLE001
            log.info("Screenshot falhou: %s", str(e)[:100])

    async def esperar(self, page):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:  # noqa: BLE001
            pass

    async def elementos(self, page, limite=80) -> list[dict]:
        try:
            return await page.evaluate(JS_ELEMENTOS, limite)
        except Exception:  # noqa: BLE001
            return []

    async def resumo(self, page, limite_texto=3000, limite_elementos=60) -> str:
        await self.screenshot(page)
        try:
            titulo = await page.title()
        except Exception:  # noqa: BLE001
            titulo = ""
        try:
            texto = " ".join((await page.inner_text("body", timeout=5000)).split())
        except Exception:  # noqa: BLE001
            texto = ""
        if len(texto) > limite_texto:
            texto = texto[:limite_texto] + f" … (+{len(texto) - limite_texto} caracteres; use navegador_ler/rolar)"
        els = await self.elementos(page, limite_elementos)
        linhas = []
        for e in els:
            tipo = e["tag"] + (f":{e['tipo']}" if e["tipo"] else "")
            extra = f" -> {e['href']}" if e.get("href") else ""
            linhas.append(f"[{e['n']}] {tipo} \"{e['txt']}\"{extra}{'' if e['visivel'] else ' (fora da tela)'}")
        return (f"URL: {page.url}\nTítulo: {titulo}\n\nTEXTO DA PÁGINA:\n{texto}\n\n"
                f"ELEMENTOS (use o número em navegador_clicar/navegador_digitar):\n" + ("\n".join(linhas) or "(nenhum)"))

    def localizar(self, page, alvo: str):
        alvo = str(alvo).strip()
        m = re.fullmatch(r"\[?(\d+)\]?", alvo)
        if m:
            return page.locator(f'[data-eva-id="{m.group(1)}"]'), True
        if re.match(r"^[#.\[]|^(css|xpath)=|^//|[>~]|\w\[", alvo):
            return page.locator(alvo), False
        loc = (page.get_by_role("button", name=alvo).or_(page.get_by_role("link", name=alvo))
               .or_(page.get_by_label(alvo)).or_(page.get_by_placeholder(alvo)).or_(page.get_by_text(alvo)))
        return loc, False


nav = Navegador()
_state = {"nav": nav}  # compatibilidade


async def fechar_navegador():
    await nav.fechar()


# ---------------------------------------------------------------- sensibilidade (pede aprovação sozinho)

async def _info_alvo(alvo: str) -> dict | None:
    if not nav.aberto:
        return None
    try:
        loc, _ = nav.localizar(nav.page, alvo)
        if await loc.count() == 0:
            return None
        return await loc.first.evaluate(JS_INFO_ELEMENTO)
    except Exception:  # noqa: BLE001
        return None


async def _sensivel_clicar(args: dict) -> str | None:
    alvo = str(args.get("alvo", ""))
    if RE_COMPRA.search(alvo):
        return "parece um botão de compra/pagamento"
    info = await _info_alvo(alvo)
    if info and RE_COMPRA.search(info.get("texto", "") + " " + info.get("nome", "")):
        return "parece um botão de compra/pagamento"
    if nav.aberto and RE_PAGINA_SENSIVEL.search(nav.page.url or "") and info and info.get("tag") in ("button", "input"):
        return "botão numa página de pagamento/carrinho"
    return None


async def _sensivel_digitar(args: dict) -> str | None:
    campo = str(args.get("campo", ""))
    if RE_CAMPO_SENSIVEL.search(campo):
        return "campo de senha/cartão/documento"
    info = await _info_alvo(campo)
    if info and (info.get("tipo") == "password" or RE_CAMPO_SENSIVEL.search(info.get("nome", ""))):
        return "campo de senha/cartão/documento"
    if nav.aberto and RE_PAGINA_SENSIVEL.search(nav.page.url or ""):
        return "formulário numa página de pagamento/carrinho"
    return None


# ---------------------------------------------------------------- ferramentas

@tool("navegador_abrir", "Abre uma URL no navegador da Eva (aparece na aba Tela). Devolve texto e elementos numerados.",
      {"url": {"type": "string"}}, rotulo="Abrindo um site", timeout=90)
async def navegador_abrir(url: str):
    from .web import validar_url
    url = await validar_url(url)

    async def acao(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await nav.esperar(page)
        return await nav.resumo(page)
    return await nav.executar(acao)


@tool("navegador_ler", "Lê o texto (mais completo) e os elementos da página aberta.", {},
      rotulo="Lendo a página", timeout=60)
async def navegador_ler():
    return await nav.executar(lambda page: nav.resumo(page, limite_texto=9000, limite_elementos=80))


@tool("navegador_elementos", "Lista os elementos clicáveis/campos numerados da página aberta.", {},
      rotulo="Vendo os botões da página", timeout=60)
async def navegador_elementos():
    async def acao(page):
        els = await nav.elementos(page, 120)
        return "\n".join(f"[{e['n']}] {e['tag']}{':' + e['tipo'] if e['tipo'] else ''} \"{e['txt']}\"" for e in els) \
            or "Nenhum elemento clicável encontrado."
    return await nav.executar(acao)


@tool("navegador_clicar", "Clica num elemento: pelo número da lista ([12] -> '12'), pelo texto visível ou seletor CSS.",
      {"alvo": {"type": "string", "description": "número do elemento, texto do botão/link, ou seletor CSS"}},
      rotulo="Clicando no site", sensivel=_sensivel_clicar, timeout=60)
async def navegador_clicar(alvo: str):
    async def acao(page):
        loc, por_numero = nav.localizar(page, alvo)
        if await loc.count() == 0:
            if por_numero:
                return f"O elemento {alvo} não existe mais (a página mudou). Use navegador_elementos para ver a lista nova."
            return f"Não achei nenhum elemento com '{alvo}'. Use navegador_elementos e clique pelo número."
        await loc.first.scroll_into_view_if_needed(timeout=5000)
        await loc.first.click(timeout=10000)
        await nav.esperar(page)
        return await nav.resumo(page)
    return await nav.executar(acao)


@tool("navegador_digitar", "Digita num campo (pelo número da lista, rótulo, placeholder ou seletor CSS).",
      {"campo": {"type": "string"}, "texto": {"type": "string"},
       "enter": {"type": "boolean", "description": "apertar Enter depois (ex.: buscar)"}},
      required=["campo", "texto"], rotulo="Digitando no site", sensivel=_sensivel_digitar, timeout=60)
async def navegador_digitar(campo: str, texto: str, enter: bool = False):
    async def acao(page):
        loc, por_numero = nav.localizar(page, campo)
        if await loc.count() == 0:
            return f"Não achei o campo '{campo}'. Use navegador_elementos e informe o número."
        alvo = loc.first
        try:
            await alvo.fill(texto, timeout=8000)
        except Exception:  # noqa: BLE001 — campos "contenteditable" ou com máscara
            await alvo.click(timeout=5000)
            await page.keyboard.type(texto, delay=15)
        if enter:
            await alvo.press("Enter")
            await nav.esperar(page)
        return await nav.resumo(page)
    return await nav.executar(acao)


@tool("navegador_rolar", "Rola a página para baixo ou para cima.",
      {"direcao": {"type": "string", "enum": ["baixo", "cima"]},
       "quanto": {"type": "integer", "description": "pixels (padrão 700)"}},
      required=["direcao"], rotulo="Rolando a página", timeout=30)
async def navegador_rolar(direcao: str = "baixo", quanto: int = 700):
    dy = abs(int(quanto or 700)) * (-1 if str(direcao).startswith("c") else 1)

    async def acao(page):
        await page.mouse.wheel(0, dy)
        await asyncio.sleep(0.6)
        return await nav.resumo(page, limite_texto=4000)
    return await nav.executar(acao)


@tool("navegador_voltar", "Volta para a página anterior.", {}, rotulo="Voltando uma página", timeout=45)
async def navegador_voltar():
    async def acao(page):
        await page.go_back(wait_until="domcontentloaded", timeout=20000)
        await nav.esperar(page)
        return await nav.resumo(page)
    return await nav.executar(acao)


@tool("navegador_screenshot", "Atualiza o print da tela (aba Tela) e salva print.png em workspace_eva.", {},
      rotulo="Tirando print", timeout=30)
async def navegador_screenshot():
    async def acao(page):
        await nav.screenshot(page)
        if nav.tela:
            (WORKSPACE / "print.jpg").write_bytes(nav.tela)
        return "Print atualizado na aba Tela (salvo como print.jpg)."
    return await nav.executar(acao)


@tool("navegador_fechar", "Fecha o navegador (libera memória).", {}, rotulo="Fechando o navegador", timeout=30)
async def navegador_fechar():
    await nav.fechar()
    return "Navegador fechado."
