"""O 'computador virtual' da Eva: um Chrome que você VÊ abrindo na tela enquanto ela navega."""
from . import tool
from ..config import BROWSER_HEADLESS, WORKSPACE

_state = {"pw": None, "browser": None, "page": None, "tela": None}  # tela = último print (aparece no app)


async def _page():
    if _state["page"] is None or _state["page"].is_closed():
        from playwright.async_api import async_playwright
        if _state["pw"] is None:
            _state["pw"] = await async_playwright().start()
        if _state["browser"] is None:
            _state["browser"] = await _state["pw"].chromium.launch(headless=BROWSER_HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await _state["browser"].new_context(locale="pt-BR", viewport={"width": 1280, "height": 800})
        _state["page"] = await ctx.new_page()
    return _state["page"]


async def fechar_navegador():
    try:
        if _state["browser"]:
            await _state["browser"].close()
        if _state["pw"]:
            await _state["pw"].stop()
    finally:
        _state.update(pw=None, browser=None, page=None, tela=None)


async def _resumo(page):
    try:
        _state["tela"] = await page.screenshot(type="jpeg", quality=60)
    except Exception:
        pass
    texto = " ".join((await page.inner_text("body")).split())[:5000]
    links = await page.eval_on_selector_all(
        "a, button, input, textarea, select, [role=button]",
        """els => els.slice(0, 60).map(e => (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || e.name || '').trim().slice(0, 60)).filter(Boolean)""")
    return f"URL: {page.url}\nTítulo: {await page.title()}\n\nTEXTO:\n{texto}\n\nELEMENTOS CLICÁVEIS/CAMPOS:\n" + " | ".join(links)


@tool("navegador_abrir", "Abre uma URL no navegador da Eva (o usuário vê na tela).", {"url": {"type": "string"}})
async def navegador_abrir(url: str):
    page = await _page()
    if not url.startswith("http"):
        url = "https://" + url
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    return await _resumo(page)


@tool("navegador_ler", "Lê o texto e os elementos da página aberta no navegador.", {})
async def navegador_ler():
    return await _resumo(await _page())


@tool("navegador_clicar", "Clica num elemento pelo texto visível dele (botão, link).", {"texto": {"type": "string"}})
async def navegador_clicar(texto: str):
    page = await _page()
    loc = page.get_by_role("button", name=texto).or_(page.get_by_role("link", name=texto)).or_(page.get_by_text(texto))
    await loc.first.click(timeout=10000)
    await page.wait_for_load_state("domcontentloaded")
    return await _resumo(page)


@tool("navegador_digitar", "Digita num campo, achado pelo rótulo, placeholder ou seletor CSS.",
      {"campo": {"type": "string"}, "texto": {"type": "string"}, "enter": {"type": "boolean", "description": "apertar Enter depois"}},
      required=["campo", "texto"])
async def navegador_digitar(campo: str, texto: str, enter: bool = False):
    page = await _page()
    loc = page.get_by_label(campo).or_(page.get_by_placeholder(campo))
    if await loc.count() == 0:
        loc = page.locator(campo)
    await loc.first.fill(texto, timeout=10000)
    if enter:
        await loc.first.press("Enter")
        await page.wait_for_load_state("domcontentloaded")
    return await _resumo(page)


@tool("navegador_screenshot", "Tira um print da tela do navegador e salva em workspace_eva.", {})
async def navegador_screenshot():
    page = await _page()
    caminho = WORKSPACE / "print.png"
    await page.screenshot(path=str(caminho))
    return f"Print salvo em {caminho.name}."
