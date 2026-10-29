"""PC Windows do Gabriel (EVA-Agente): pareamento, tokens (hash, revogar), WebSocket /ws/pc, hub (pedido/resposta,
tempo esgotado, queda no meio, pausa), ferramentas indo para o PC, killswitch, controle remoto, tela ao vivo e um
teste PONTA A PONTA: o núcleo de verdade do agente (com tela falsa) rodando contra um servidor da Eva de verdade."""
import asyncio
import base64
import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from eva import agent as agent_mod
from eva import brain, pc_hub
from eva.computador import pc
from eva.db import db
from eva.killswitch import kill_switch
from eva.pc_hub import ComputadorErro, hub
from eva.tools import TOOLS
from eva.tools import computador as ferr
from tests.conftest import SENHA_TESTE

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "agente_windows"))

from eva_agente.config import Config  # noqa: E402
from eva_agente.nucleo import Agente, Interface  # noqa: E402
from eva_agente.pareamento import parear  # noqa: E402
from eva_agente.plataforma import PlataformaFalsa  # noqa: E402


async def esperar(cond, timeout=8.0, passo=0.02):
    for _ in range(int(timeout / passo)):
        if cond():
            return True
        await asyncio.sleep(passo)
    return cond()


@pytest.fixture
def cliente():
    from eva import server
    with TestClient(server.app) as c:
        assert c.post("/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


# ================================================================ pareamento e dispositivos

def test_pareamento_completo(cliente):
    from eva import server
    assert TestClient(server.app).post("/api/pc/parear").status_code == 401        # gerar exige login
    d = cliente.post("/api/pc/parear").json()
    codigo = d["codigo"]
    assert d["ok"] and len(codigo) == 9 and codigo[4] == "-" and d["validade_s"] == 600
    assert d["download_url"].endswith("/releases/latest/download/EVA-Agente.exe")
    assert cliente.get("/api/computador").json()["windows"]["codigo_pendente"] is True
    anon = TestClient(server.app)                                                     # o agente não tem cookie
    r = anon.post("/api/pc/parear/confirmar", json={"codigo": "ZZZZ-ZZZZ", "nome": "PC"})
    assert r.status_code == 401 and "Código errado" in r.json()["erro"]
    r = anon.post("/api/pc/parear/confirmar", json={"codigo": codigo.lower().replace("-", " "), "nome": "PC-Sala",
                                                    "versao": "1.0.0"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert tok.startswith("evapc_") and len(tok) > 40 and r.json()["nome"] == "PC-Sala"
    # uso único
    assert anon.post("/api/pc/parear/confirmar", json={"codigo": codigo, "nome": "x"}).status_code == 401
    # só o hash fica no banco
    linhas = db.all("SELECT * FROM dispositivos")
    assert len(linhas) == 1 and linhas[0]["token_hash"] == pc_hub.hash_token(tok) and tok not in json.dumps(linhas)
    assert not any(tok in str(v) for v in db.all("SELECT valor FROM config"))
    lst = cliente.get("/api/pc/dispositivos").json()
    assert lst[0]["nome"] == "PC-Sala" and lst[0]["conectado"] is False
    e = cliente.get("/api/computador").json()
    assert e["configurado"] and e["backend"] == "windows" and e["online"] is False and e["windows"]["pareado"]
    # desconectar = revogar
    assert cliente.delete(f"/api/pc/dispositivos/{lst[0]['id']}").json()["ok"]
    assert pc_hub.dispositivo_por_token(tok) is None
    assert cliente.get("/api/pc/dispositivos").json() == []
    assert cliente.get("/api/computador").json()["configurado"] is False


def test_codigo_vence(cliente, monkeypatch):
    codigo = cliente.post("/api/pc/parear").json()["codigo"]
    agora = time.time()
    monkeypatch.setattr(pc_hub.time, "time", lambda: agora + 601)
    assert pc_hub.confirmar_codigo(codigo, "PC") is None
    monkeypatch.setattr(pc_hub.time, "time", lambda: agora)
    assert pc_hub.confirmar_codigo(codigo, "PC")["token"]


def test_freio_contra_forca_bruta(cliente):
    from eva import server
    anon = TestClient(server.app)
    for _ in range(pc_hub.MAX_FALHAS_IP):
        assert anon.post("/api/pc/parear/confirmar", json={"codigo": "AAAA-AAAA"}).status_code == 401
    codigo = cliente.post("/api/pc/parear").json()["codigo"]
    r = anon.post("/api/pc/parear/confirmar", json={"codigo": codigo})
    assert r.status_code == 429                           # bloqueado mesmo com o código certo
    assert "Muitas tentativas" in r.json()["erro"]


def test_ws_pc_recusa_token_errado(cliente):
    from starlette.websockets import WebSocketDisconnect
    with cliente.websocket_connect("/ws/pc", headers={"Authorization": "Bearer evapc_inventado"}) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401
    with cliente.websocket_connect("/ws/pc") as ws:        # sem token nenhum
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_ws_pc_conecta_status_e_revoga(cliente):
    from starlette.websockets import WebSocketDisconnect
    tok = pc_hub.confirmar_codigo(cliente.post("/api/pc/parear").json()["codigo"], "PC-Quarto")["token"]
    with cliente.websocket_connect("/ws/pc", headers={"Authorization": f"Bearer {tok}"}) as ws:
        ws.send_text(json.dumps({"tipo": "ola", "nome": "PC-Quarto", "versao": "1.0.0",
                                 "tela": {"largura": 2560, "altura": 1440}}))
        assert json.loads(ws.receive_text())["tipo"] == "bem_vindo"
        e = cliente.get("/api/computador").json()
        assert e["backend"] == "windows" and e["online"] and e["windows"]["conectado"]
        assert e["windows"]["nome"] == "PC-Quarto" and (e["largura"], e["altura"]) == (2560, 1440)
        assert e["ao_vivo"] is True and e["vnc_url"] is None
        est = cliente.get("/api/estado").json()["computador"]
        assert est["windows"]["conectado"] and est["windows"]["versao_agente"] == "1.0.0"
        assert db.one("SELECT ultimo_contato FROM dispositivos")["ultimo_contato"]
        ws.send_text(json.dumps({"tipo": "ping"}))
        assert json.loads(ws.receive_text())["tipo"] == "pong"
        ws.send_text(json.dumps({"tipo": "estado", "pausado": True, "motivo": "pausada pelo ícone"}))
        for _ in range(100):
            if hub.pausado:
                break
            time.sleep(0.02)
        assert cliente.get("/api/computador").json()["windows"]["pausado_no_pc"] is True
        did = cliente.get("/api/pc/dispositivos").json()[0]["id"]
        cliente.delete(f"/api/pc/dispositivos/{did}")
        assert json.loads(ws.receive_text())["tipo"] == "revogado"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4401
    assert not hub.conectado and cliente.get("/api/computador").json()["configurado"] is False


def test_ws_pc_primeira_mensagem_precisa_ser_ola(cliente):
    from starlette.websockets import WebSocketDisconnect
    tok = pc_hub.confirmar_codigo(cliente.post("/api/pc/parear").json()["codigo"], "PC")["token"]
    with cliente.websocket_connect("/ws/pc", headers={"Authorization": f"Bearer {tok}"}) as ws:
        ws.send_text(json.dumps({"tipo": "cmd"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4400 and not hub.conectado


# ================================================================ hub (com uma conexão de mentira no mesmo loop)

class ConexaoFalsa(pc_hub.Conexao):
    """Faz o papel do agente: responde aos comandos conforme `responder`."""

    def __init__(self):
        self.enviadas, self.binarios = [], []
        self.fechada = None
        self.responder = lambda cmd: {"ok": True, "dados": {"tela": {"largura": 1366, "altura": 768},
                                                             "mouse": {"x": 1, "y": 2}, "janela": "Bloco de Notas"}}
        self.atraso = 0.0
        self.mudo = False

    async def enviar_texto(self, texto):
        m = json.loads(texto)
        self.enviadas.append(m)
        if m.get("tipo") == "cmd" and not self.mudo:
            async def resp():
                await asyncio.sleep(self.atraso)
                await hub.receber_texto(self, {"tipo": "resp", "id": m["id"], **self.responder(m)})
            asyncio.ensure_future(resp())

    async def enviar_bytes(self, dados):
        self.binarios.append(dados)

    async def fechar(self, codigo=1000, motivo=""):
        self.fechada = (codigo, motivo)


@pytest.fixture
async def pc_falso():
    tok = pc_hub.confirmar_codigo(pc_hub.criar_codigo()["codigo"], "PC-Falso")["token"]
    disp = pc_hub.dispositivo_por_token(tok)
    c = ConexaoFalsa()
    await hub.conectar(c, disp, {"tipo": "ola", "nome": "PC-Falso", "versao": "1.0.0",
                                 "tela": {"largura": 1366, "altura": 768}})
    yield c
    await hub.desconectar(c)


async def test_hub_pedido_e_resposta(pc_falso):
    d = await hub.pedir("info", {})
    assert d["janela"] == "Bloco de Notas"
    cmd = [m for m in pc_falso.enviadas if m["tipo"] == "cmd"][-1]
    assert cmd["op"] == "info" and len(cmd["id"]) == 12
    assert pc_falso.enviadas[0]["tipo"] == "bem_vindo"
    pc_falso.responder = lambda m: {"ok": False, "erro": "fora da tela", "codigo": "invalido"}
    with pytest.raises(ComputadorErro, match="ERRO do PC: fora da tela"):
        await hub.pedir("mouse", {"acao": "clicar", "x": 9999, "y": 1})


async def test_hub_tempo_esgotado_e_queda(pc_falso):
    pc_falso.mudo = True
    with pytest.raises(ComputadorErro, match="demorou demais"):
        await hub.pedir("info", {}, timeout=0.2)
    assert not hub.pendentes
    t = asyncio.ensure_future(hub.pedir("comando", {"cmd": "x"}, timeout=10))
    await esperar(lambda: hub.pendentes)
    await hub.desconectar(pc_falso, "teste")
    with pytest.raises(ComputadorErro, match="desconectou no meio"):
        await t
    with pytest.raises(ComputadorErro, match="não está conectado"):
        await hub.pedir("info", {})


async def test_hub_pausa_no_pc_e_emergencia(pc_falso, eventos):
    chamadas = []
    antigo = hub.ao_emergencia
    hub.ao_emergencia = chamadas.append
    try:
        await hub.receber_texto(pc_falso, {"tipo": "estado", "pausado": True, "motivo": "atalho de emergência",
                                           "emergencia": True})
        assert hub.pausado and chamadas == ["atalho de emergência"]
        assert any(e["tipo"] == "computador" and e["windows"]["pausado_no_pc"] for e in eventos)
        r = await ferr.computador_clicar(500, 500)
        assert "PAUSOU a Eva no PC" in r and "atalho de emergência" in r
        assert not [m for m in pc_falso.enviadas if m.get("op") == "mouse"]
        await hub.receber_texto(pc_falso, {"tipo": "estado", "pausado": False})
        assert (await ferr.computador_clicar(500, 500)).startswith("Cliquei")
    finally:
        hub.ao_emergencia = antigo


async def test_hub_resposta_pausado_vinda_do_pc(pc_falso):
    pc_falso.responder = lambda m: {"ok": False, "erro": "mouse no canto", "codigo": "pausado"}
    r = await ferr.computador_digitar("oi")
    assert "PAUSOU" in r and hub.pausado


async def test_ferramentas_vao_para_o_pc_windows(pc_falso):
    assert pc.backend() == "windows" and await pc.disponivel()
    r = await ferr.computador_clicar(500, 500, "duplo")
    cmd = [m for m in pc_falso.enviadas if m.get("op") == "mouse"][-1]
    assert cmd["args"] == {"acao": "duplo", "x": 682, "y": 384}            # grade -> pixels de 1366x768
    assert "pixel (682, 384)" in r and "Bloco de Notas" in r
    await ferr.computador_digitar("Olá ção 😀", enter=True)
    t = [m for m in pc_falso.enviadas if m.get("op") == "teclado"][-1]["args"]
    assert t == {"texto": "Olá ção 😀", "teclas": "Return"}
    await ferr.computador_tecla("win+r")
    assert [m for m in pc_falso.enviadas if m.get("op") == "teclado"][-1]["args"] == {"teclas": "win+r"}
    await ferr.computador_abrir(app="calculadora")
    assert [m for m in pc_falso.enviadas if m.get("op") == "abrir"][-1]["args"] == {"app": "calculadora"}
    pc_falso.responder = lambda m: {"ok": True, "dados": {"codigo": 0, "tempo_esgotado": False,
                                                          "saida": "Gabriel\r\n"}}
    r = await ferr.computador_comando("whoami")
    assert "Gabriel" in r and [m for m in pc_falso.enviadas if m.get("op") == "comando"][-1]["args"] == \
        {"cmd": "whoami", "timeout": 60}
    pc_falso.responder = lambda m: {"ok": True, "dados": {"janelas": [
        {"titulo": "Planilha1 - Excel", "ativa": True}, {"titulo": "WhatsApp", "ativa": False}]}}
    r = await ferr.computador_janelas()
    assert "➤ Planilha1 - Excel" in r and "- WhatsApp" in r
    img = b"\xff\xd8\xff\xe0jpegfalso"
    pc_falso.responder = lambda m: {"ok": True, "dados": {"imagem_b64": base64.b64encode(img).decode(),
                                                          "tela": {"largura": 1366, "altura": 768}}}
    assert await pc.capturar() == img and pc.tela == img
    pc_falso.responder = lambda m: {"ok": True, "dados": {"imagem_b64": ""}}
    with pytest.raises(ComputadorErro, match="print vazio"):
        await pc.capturar()


async def test_area_transferencia_e_arquivos(pc_falso, tmp_path):
    pc_falso.responder = lambda m: {"ok": True, "dados": {"texto": "copiado"}}
    assert await TOOLS["computador_area_transferencia"]["sensivel"]({"acao": "ler"})       # ler pede aprovação
    assert await TOOLS["computador_area_transferencia"]["sensivel"]({"acao": "escrever", "texto": "oi"}) is None
    assert "copiado" in await ferr.computador_area_transferencia("ler")
    await ferr.computador_area_transferencia("escrever", "texto longo")
    assert [m for m in pc_falso.enviadas if m.get("op") == "clipboard_escrever"][-1]["args"] == {"texto": "texto longo"}
    pc_falso.responder = lambda m: {"ok": True, "dados": {"nome": "nota.pdf",
                                                          "dados_b64": base64.b64encode(b"%PDF").decode()}}
    r = await ferr.computador_arquivo("baixar", "Downloads/nota.pdf")
    from eva.config import WORKSPACE
    assert "do_computador/nota.pdf" in r and (WORKSPACE / "do_computador" / "nota.pdf").read_bytes() == b"%PDF"
    pc_falso.responder = lambda m: {"ok": True, "dados": {"caminho": "C:\\Users\\Gabriel\\Desktop\\nota.pdf"}}
    r = await ferr.computador_arquivo("enviar", "Desktop/nota.pdf", origem="do_computador/nota.pdf")
    env = [m for m in pc_falso.enviadas if m.get("op") == "arquivo_escrever"][-1]["args"]
    assert base64.b64decode(env["dados_b64"]) == b"%PDF" and env["sobrescrever"] is False
    assert "Enviei" in r


async def test_killswitch_pausa_o_pc_e_ligar_libera(pc_falso):
    await agent_mod.desligar_tudo("teste")
    assert await esperar(lambda: any(m["tipo"] == "parar" for m in pc_falso.enviadas))
    assert (await ferr.computador_clicar(1, 1)).startswith("ERRO: a Eva está desligada")
    agent_mod.ligar()
    assert await esperar(lambda: any(m["tipo"] == "liberar" for m in pc_falso.enviadas))
    assert (await ferr.computador_clicar(1, 1)).startswith("Cliquei")


async def test_conecta_com_eva_desligada_ja_pausa(eventos):
    kill_switch._off = True
    tok = pc_hub.confirmar_codigo(pc_hub.criar_codigo()["codigo"], "PC")["token"]
    c = ConexaoFalsa()
    await hub.conectar(c, pc_hub.dispositivo_por_token(tok), {"tipo": "ola"})
    assert [m["tipo"] for m in c.enviadas][:2] == ["bem_vindo", "parar"]
    await hub.desconectar(c)


async def test_outro_pc_derruba_o_anterior(pc_falso):
    tok = pc_hub.confirmar_codigo(pc_hub.criar_codigo()["codigo"], "Notebook")["token"]
    c2 = ConexaoFalsa()
    await hub.conectar(c2, pc_hub.dispositivo_por_token(tok), {"tipo": "ola", "nome": "Notebook"})
    assert pc_falso.fechada[0] == 4409 and hub.dispositivo["nome"] == "Notebook"
    await hub.desconectar(c2)


async def test_quadros_ao_vivo_e_espectadores(pc_falso):
    recebidos = []
    eid = await hub.adicionar_espectador(recebidos.append)
    assert pc_falso.enviadas[-1] == {"tipo": "ao_vivo", "ligar": True, "fps": 2.0}
    await hub.receber_bytes(pc_falso, b"F\xff\xd8quadro1")
    assert recebidos == [b"\xff\xd8quadro1"] and pc_falso.enviadas[-1]["tipo"] == "frame_ok"
    recebidos2 = []
    eid2 = await hub.adicionar_espectador(recebidos2.append)     # quem chega depois já vê o último quadro
    assert recebidos2 == [b"\xff\xd8quadro1"]
    await hub.remover_espectador(eid)
    assert not any(m == {"tipo": "ao_vivo", "ligar": False} for m in pc_falso.enviadas)
    await hub.remover_espectador(eid2)
    assert pc_falso.enviadas[-1] == {"tipo": "ao_vivo", "ligar": False}


async def test_prompt_do_pc_windows():
    s = await brain.build_system_prompt("x", None, "online", "windows")
    assert "Windows 10/11 de verdade" in s and "grade 0–1000" in s and "PowerShell" in s
    for regra in ("NUNCA feche janelas", "trabalho não salvo", "apagar ou mover arquivos", "instalar/desinstalar",
                  "enviar mensagens", "comprar ou pagar", "NUNCA digite senhas", "UAC", "Assumir controle"):
        assert regra in s, regra
    s = await brain.build_system_prompt("x", None, "offline", "windows")
    assert "NÃO está conectado" in s and "navegador_abrir" in s and "Windows 10/11 de verdade" not in s
    s = await brain.build_system_prompt("x", None, "online", "nuvem")
    assert "desktop Linux na nuvem" in s


async def test_tarefa_com_pc_windows_offline_usa_navegador(fake, rodar_tarefa):
    pc_hub.confirmar_codigo(pc_hub.criar_codigo()["codigo"], "PC")
    fake.adicionar({"texto": "Teu PC está desligado."})
    await rodar_tarefa("abre o excel no meu pc")
    req = fake.requisicoes[0]
    nomes = {t["function"]["name"] for t in req["tools"]}
    assert "navegador_abrir" in nomes and "computador_clicar" not in nomes
    assert "NÃO está conectado" in req["messages"][0]["content"]


def test_ui_conectar_meu_pc(cliente):
    html = cliente.get("/").text
    assert "Conectar meu PC" in html and "Executar assim mesmo" in html and 'id="pcImg"' in html
    assert "releases/latest/download/EVA-Agente.exe" in html and "Ctrl+Alt+Shift+P" in html
    js = cliente.get("/static/app.js").text
    assert "/api/pc/parear" in js and "/ws/tela" in js and "/api/pc/dispositivos" in js


# ================================================================ PONTA A PONTA: agente de verdade x Eva de verdade

def _porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def eva_real():
    """Servidor da Eva de verdade (uvicorn + websockets) numa thread, como no Render."""
    import uvicorn

    from eva import server
    porta = _porta_livre()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=porta, log_level="error",
                                        lifespan="off", ws_ping_interval=20, ws_ping_timeout=30))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True
    th.join(5)


class Ui(Interface):
    def __init__(self):
        self.estados, self.banners, self.revogado = [], [], []

    def estado(self, e, t):
        self.estados.append((e, t))

    def banner(self, v, t=""):
        self.banners.append(v)

    def token_invalido(self, m):
        self.revogado.append(m)


class AgenteNaThread:
    """O EVA-Agente como roda no Windows: núcleo asyncio na própria thread, com a tela falsa."""

    def __init__(self, cfg, tmp_path):
        self.p = PlataformaFalsa(1920, 1080)
        self.ui = Ui()
        self.ag = Agente(cfg, self.p, self.ui, casa=tmp_path, nome="PC-E2E")
        self.ag.backoff_base = 0.1
        self.ag.atraso_banner = 0.1
        self.fim = None
        self.th = threading.Thread(target=lambda: setattr(self, "fim", asyncio.run(self.ag.rodar())), daemon=True)
        self.th.start()

    def parar(self):
        self.ag.sair()
        self.th.join(10)


@pytest.fixture
async def ponta_a_ponta(eva_real, tmp_path):
    async with httpx.AsyncClient(base_url=eva_real, timeout=20) as web:
        assert (await web.post("/login", json={"senha": SENHA_TESTE})).status_code == 200
        codigo = (await web.post("/api/pc/parear")).json()["codigo"]
        d = await asyncio.to_thread(parear, eva_real, codigo, "PC-E2E", 20)   # como a janela de pareamento faz
        cfg = Config(servidor=d["servidor"], dispositivo_id=d["dispositivo_id"], nome=d["nome"], token=d["token"],
                     manter_eva_acordada=False)
        a = AgenteNaThread(cfg, tmp_path)
        assert await esperar(lambda: hub.conectado and a.ag.conectado, 10), a.ui.estados
        a.web = web
        a.servidor = eva_real
        try:
            yield a
        finally:
            a.parar()


async def test_ponta_a_ponta_ferramentas(ponta_a_ponta):
    a = ponta_a_ponta
    est = (await a.web.get("/api/computador")).json()
    assert est["backend"] == "windows" and est["online"] and est["windows"]["nome"] == "PC-E2E"
    assert (est["largura"], est["altura"]) == (1920, 1080)
    assert est["dispositivos"][0]["conectado"] is True
    r = await ferr.computador_clicar(500, 500)
    assert ("mover", 960, 540) in a.p.chamadas and ("botao", "esquerdo", "clicar") in a.p.chamadas, r
    await ferr.computador_digitar("Olá, ação ✓ 😀", enter=True)
    assert ("digitar", "Olá, ação ✓ 😀") in a.p.chamadas and ("combo", ("enter",)) in a.p.chamadas
    await ferr.computador_tecla("ctrl+l")
    assert ("combo", ("ctrl", "l")) in a.p.chamadas
    await ferr.computador_rolar("baixo", 3)
    assert ("rolar", 3, 0) in a.p.chamadas
    await ferr.computador_arrastar(100, 100, 900, 900)
    assert ("botao", "esquerdo", "descer") in a.p.chamadas
    await ferr.computador_abrir(url="https://g1.globo.com")
    assert ("abrir_url", "https://g1.globo.com") in a.p.chamadas
    r = await ferr.computador_comando("echo eva-no-pc")
    assert "eva-no-pc" in r and "(código de saída 0)" in r
    r = await ferr.computador_janelas(focar="word")
    assert "Documento1 - Word" in r
    img = await pc.capturar()
    assert img[:2] == b"\xff\xd8" and pc.largura == 1920
    r = await ferr.computador_ver_tela()
    assert "Tela 1920x1080" in r
    assert a.ui.banners and True in a.ui.banners              # o aviso apareceu no PC durante as ações
    assert any(e[0] == "agindo" for e in a.ui.estados)


async def test_ponta_a_ponta_tarefa_completa_com_print(ponta_a_ponta, fake, eventos, rodar_tarefa):
    a = ponta_a_ponta
    fake.adicionar(
        {"tool_calls": [{"nome": "computador_abrir", "args": {"app": "editor"}, "assinatura": "S1"}]},
        {"tool_calls": [{"nome": "computador_digitar", "args": {"texto": "Lista de compras"}, "assinatura": "S2"}]},
        {"texto": "Abri o Bloco de Notas e escrevi a lista."},
    )
    t = await rodar_tarefa("abre o bloco de notas no meu pc e escreve Lista de compras", timeout=30)
    assert t.status == "concluida", t.eventos
    req0 = fake.requisicoes[0]
    nomes = {x["function"]["name"] for x in req0["tools"]}
    assert {"computador_clicar", "computador_janelas", "computador_arquivo"} <= nomes
    assert not any(n.startswith("navegador_") for n in nomes)
    assert "Windows 10/11 de verdade" in req0["messages"][0]["content"]
    m2 = fake.requisicoes[1]["messages"]
    assert m2[-2]["role"] == "tool" and m2[-1]["role"] == "user"
    assert m2[-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")   # print do PC
    assert ("abrir_programa", "notepad") in a.p.chamadas and ("digitar", "Lista de compras") in a.p.chamadas
    assert a.p.capturas >= 2


async def test_ponta_a_ponta_pausa_killswitch_e_emergencia(ponta_a_ponta):
    a = ponta_a_ponta
    # killswitch no app -> PC pausa também
    await agent_mod.desligar_tudo("teste")
    assert await esperar(lambda: a.ag.pausa_remota is not None)
    agent_mod.ligar()
    assert await esperar(lambda: a.ag.pausa_remota is None)
    assert (await ferr.computador_clicar(10, 10)).startswith("Cliquei")
    # Ctrl+Alt+Shift+P no PC -> Eva fica sabendo e não age
    a.ag.emergencia()
    assert await esperar(lambda: hub.pausado)
    n = len(a.p.chamadas)
    r = await ferr.computador_clicar(10, 10)
    assert "PAUSOU" in r and not [c for c in a.p.chamadas[n:] if c[0] == "botao"]
    e = (await a.web.get("/api/estado")).json()["computador"]["windows"]
    assert e["pausado_no_pc"] and "Ctrl+Alt+Shift+P" in e["motivo_pausa"]
    a.ag.retomar()
    assert await esperar(lambda: not hub.pausado)
    assert (await ferr.computador_clicar(10, 10)).startswith("Cliquei")


async def test_ponta_a_ponta_tela_ao_vivo_e_controle_remoto(ponta_a_ponta):
    from websockets.asyncio.client import connect
    a = ponta_a_ponta
    cookie = "; ".join(f"{k}={v}" for k, v in a.web.cookies.items())
    url = a.servidor.replace("http://", "ws://") + "/ws/tela"
    async with connect(url, additional_headers={"Cookie": cookie}, proxy=None) as ws:
        assert await esperar(lambda: a.ag.ao_vivo, 5)                  # o PC só transmite enquanto alguém assiste
        quadro = await asyncio.wait_for(ws.recv(), 10)
        assert isinstance(quadro, bytes) and quadro[:2] == b"\xff\xd8"
        # sem "Assumir controle": clique pela tela é recusado
        await ws.send(json.dumps({"tipo": "mouse", "acao": "clicar", "x": 250, "y": 250}))
        while True:
            m = await asyncio.wait_for(ws.recv(), 10)
            if isinstance(m, str):
                break
        assert "Assumir controle" in json.loads(m)["texto"]
        r = await a.web.post("/api/computador/controle", json={"gabriel": True})
        assert r.json()["controle_gabriel"] is True
        assert await esperar(lambda: a.ag.controle_gabriel)
        assert "assumiu o controle" in await ferr.computador_clicar(1, 1)   # a Eva espera
        await ws.send(json.dumps({"tipo": "mouse", "acao": "clicar", "x": 250, "y": 250}))
        assert await esperar(lambda: ("mover", 480, 270) in a.p.chamadas)
        await ws.send(json.dumps({"tipo": "texto", "texto": "digitado pelo Gabriel"}))
        assert await esperar(lambda: ("digitar", "digitado pelo Gabriel") in a.p.chamadas)
        await ws.send(json.dumps({"tipo": "tecla", "teclas": "enter"}))
        assert await esperar(lambda: a.p.chamadas.count(("combo", ("enter",))) >= 1)
        await a.web.post("/api/computador/controle", json={"gabriel": False})
        assert await esperar(lambda: not a.ag.controle_gabriel)
    assert await esperar(lambda: not a.ag.ao_vivo, 5)                    # fechou a aba: para de transmitir


async def test_ponta_a_ponta_reconecta_e_revoga(ponta_a_ponta):
    a = ponta_a_ponta
    conn = hub.conn
    await hub._no_loop(conn.fechar(1011, "reiniciando"))                 # queda (ex.: Render reiniciou)
    assert await esperar(lambda: hub.conectado and hub.conn is not conn and a.ag.conectado, 10)
    assert (await ferr.computador_tecla("esc")).startswith("Apertei")
    did = (await a.web.get("/api/pc/dispositivos")).json()[0]["id"]
    assert (await a.web.delete(f"/api/pc/dispositivos/{did}")).json()["ok"]
    a.th.join(10)
    assert a.fim == "revogado" and a.ui.revogado and not hub.conectado
