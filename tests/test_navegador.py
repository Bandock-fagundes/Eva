"""Navegador real (Playwright/Chromium) contra páginas locais. Pulado se o Chromium não abrir nesta máquina."""
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PAGINAS = {
    "/": """<html><head><title>Loja Teste</title></head><body><h1>Bem-vindo à loja</h1>
      <a href="/produto">Ver produto</a> <button onclick="document.getElementById('r').innerText='clicou!'">Botão comum</button>
      <p id="r"></p><label for="busca">Buscar</label><input id="busca" name="q" placeholder="O que procura?">
      <label for="pw">Senha</label><input id="pw" type="password" name="senha">
      <div style="height:3000px"></div><p>rodapé longe</p></body></html>""",
    "/produto": """<html><head><title>Produto</title></head><body><h1>Tênis</h1>
      <button id="comprar">Finalizar compra</button></body></html>""",
}


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = PAGINAS.get(self.path.split("?")[0], "<h1>404</h1>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
async def nav(monkeypatch):
    from eva import config
    from eva.tools import browser
    monkeypatch.setattr(config, "PERMITIR_ENDERECOS_LOCAIS", True)
    monkeypatch.setattr(browser, "BROWSER_HEADLESS", True)
    monkeypatch.setattr(browser, "BROWSER_LEVE", os.getenv("EVA_TESTE_BROWSER_LEVE") == "1")
    try:
        await browser.nav.garantir()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium não abre nesta máquina: {str(e)[:120]}")
    yield browser
    await browser.nav.fechar()


async def test_abrir_ler_clicar_digitar(nav, site):
    b = nav
    r = await b.navegador_abrir(site + "/")
    assert "Loja Teste" in r and "Bem-vindo" in r
    assert '"Ver produto"' in r and "[1]" in r
    els = await b.navegador_elementos()
    num_botao = next(l for l in els.splitlines() if "Botão comum" in l).split("]")[0].strip("[")
    r = await b.navegador_clicar(num_botao)
    assert "clicou!" in r
    r = await b.navegador_digitar("O que procura?", "tênis azul")
    assert 'tênis azul' in r
    assert b.nav.tela and b.nav.tela_versao > 0  # print para a aba Tela
    r = await b.navegador_rolar("baixo", 3000)
    assert "URL:" in r
    r = await b.navegador_clicar("Ver produto")
    assert "Tênis" in r
    r = await b.navegador_voltar()
    assert "Bem-vindo" in r
    assert "não existe mais" in await b.navegador_clicar("999")


async def test_sensibilidade_compra_e_senha(nav, site):
    b = nav
    await b.navegador_abrir(site + "/")
    assert await b._sensivel_digitar({"campo": "Senha"}) is not None
    assert await b._sensivel_digitar({"campo": "O que procura?"}) is None
    assert await b._sensivel_clicar({"alvo": "Botão comum"}) is None
    await b.navegador_abrir(site + "/produto")
    assert await b._sensivel_clicar({"alvo": "#comprar"}) == "parece um botão de compra/pagamento"
    els = await b.navegador_elementos()
    num = next(l for l in els.splitlines() if "Finalizar" in l).split("]")[0].strip("[")
    assert await b._sensivel_clicar({"alvo": num}) is not None


async def test_reinicia_se_o_chromium_morrer(nav, site):
    b = nav
    await b.navegador_abrir(site + "/")
    await b.nav.contexto.close()  # simula o Chromium morrendo
    await asyncio.sleep(0.2)
    r = await b.navegador_abrir(site + "/produto")
    assert "Tênis" in r


async def test_fecha_e_reabre(nav, site):
    b = nav
    assert "fechado" in await b.navegador_fechar()
    assert not b.nav.aberto
    r = await b.navegador_ler()  # reabre sozinho (página em branco)
    assert "URL:" in r
