"""Núcleo do agente contra uma Eva falsa: conexão, comandos, pausa/emergência, parada remota, tela ao vivo,
reconexão, token revogado, aviso na tela e controle do Gabriel."""
import asyncio
import base64
import sys

import pytest

from eva_agente import VERSAO
from eva_agente.config import Config
from eva_agente.nucleo import TEXTO_EVA, Agente, Interface
from eva_agente.plataforma import PlataformaFalsa
from testes_util import Registro
from eva_falsa import EvaFalsa, esperar


@pytest.fixture
async def eva():
    e = await EvaFalsa().iniciar()
    yield e
    await e.fechar()


@pytest.fixture
async def ligado(eva, tmp_path):
    p = PlataformaFalsa(1920, 1080)
    ui = Registro()
    ag = Agente(Config(servidor=eva.endereco, token=eva.token, manter_eva_acordada=False), p, ui,
                casa=tmp_path, nome="PC-TESTE")
    ag.backoff_base = 0.05
    ag.atraso_banner = 0.1
    ag.executor.pausa_menu = ag.executor.pausa_busca = 0
    tarefa = asyncio.ensure_future(ag.rodar())
    assert await esperar(lambda: eva.ws is not None and ag.conectado)
    yield ag, p, ui, eva, tarefa
    ag.sair()
    await asyncio.wait_for(tarefa, 10)
    ag.executor.fechar()


async def test_conecta_e_responde(ligado):
    ag, p, ui, eva, _ = ligado
    assert eva.auths[0] == f"Bearer {eva.token}"
    ola = eva.olas[0]
    assert ola["tipo"] == "ola" and ola["nome"] == "PC-TESTE" and ola["versao"] == VERSAO
    assert ola["tela"] == {"largura": 1920, "altura": 1080} and ola["pausado"] is False
    r = await eva.pedir("info")
    assert r["ok"] and r["dados"]["tela"]["largura"] == 1920
    r = await eva.pedir("mouse", {"acao": "clicar", "x": 960, "y": 540})
    assert r["ok"] and ("botao", "esquerdo", "clicar") in p.chamadas
    r = await eva.pedir("tela", {"largura": 640})
    assert r["ok"] and base64.b64decode(r["dados"]["imagem_b64"])[:2] == b"\xff\xd8"
    r = await eva.pedir("mouse", {"acao": "clicar", "x": 5000, "y": 1})
    assert not r["ok"] and r["codigo"] == "invalido" and "fora da tela" in r["erro"]
    r = await eva.pedir("op_que_nao_existe")
    assert not r["ok"] and r["codigo"] == "invalido"
    assert ("conectado", "Conectado à Eva") in ui.estados


async def test_aviso_na_tela_durante_acao(ligado):
    ag, p, ui, eva, _ = ligado
    await eva.pedir("teclado", {"texto": "oi"})
    assert (True, TEXTO_EVA) in ui.banners
    assert await esperar(lambda: ui.banners[-1][0] is False, 2)
    n = len(ui.banners)
    await eva.pedir("info")          # só leitura: sem aviso
    assert len(ui.banners) == n


async def test_emergencia_pausa_e_retomar(ligado):
    ag, p, ui, eva, _ = ligado
    ag.emergencia()
    assert await esperar(lambda: any(m.get("tipo") == "estado" for m in eva.msgs))
    est = [m for m in eva.msgs if m["tipo"] == "estado"][-1]
    assert est["pausado"] is True and est["emergencia"] is True and "Ctrl+Alt+Shift+P" in est["motivo"]
    r = await eva.pedir("mouse", {"acao": "clicar", "x": 10, "y": 10})
    assert not r["ok"] and r["codigo"] == "pausado"
    assert ("botao", "esquerdo", "clicar") not in p.chamadas
    assert ui.estados[-1][0] == "pausado" and ("soltar_tudo",) in p.chamadas
    ag.retomar()
    assert await esperar(lambda: eva.msgs[-1] == {"tipo": "estado", "pausado": False})
    assert (await eva.pedir("mouse", {"acao": "clicar", "x": 10, "y": 10}))["ok"]


async def test_emergencia_interrompe_comando_em_andamento(ligado):
    ag, p, ui, eva, _ = ligado
    cmd = "ping -n 30 127.0.0.1" if sys.platform == "win32" else "sleep 30"
    pedido = asyncio.ensure_future(eva.pedir("comando", {"cmd": cmd, "timeout": 60}, timeout=15))
    assert await esperar(lambda: bool(ag.executor.processos))
    ag.emergencia()
    r = await asyncio.wait_for(pedido, 10)
    assert not r["ok"] and r["codigo"] == "pausado"
    assert await esperar(lambda: not ag.executor.processos)


async def test_mouse_no_canto_e_freio(ligado):
    ag, p, ui, eva, _ = ligado
    p.mouse = (0, 0)   # o Gabriel jogou o mouse no canto
    r = await eva.pedir("teclado", {"texto": "x"})
    assert not r["ok"] and r["codigo"] == "pausado" and "canto" in r["erro"]
    assert ag.pausa_local and "canto" in ag.pausa_local
    assert ("digitar", "x") not in p.chamadas
    ag.retomar()                       # ainda no canto: não retoma
    await asyncio.sleep(0.05)
    assert ag.pausa_local
    p.mouse = (500, 500)
    ag.retomar()
    assert await esperar(lambda: ag.pausa_local is None)


async def test_parada_remota_e_liberar(ligado):
    ag, p, ui, eva, _ = ligado
    await eva.mandar({"tipo": "parar", "motivo": "A Eva foi desligada no app."})
    assert await esperar(lambda: ag.pausa_remota is not None)
    r = await eva.pedir("teclado", {"texto": "oi"})
    assert r["codigo"] == "pausado" and ("soltar_tudo",) in p.chamadas
    await eva.mandar({"tipo": "liberar"})
    assert await esperar(lambda: ag.pausa_remota is None)
    assert (await eva.pedir("teclado", {"texto": "oi"}))["ok"]


async def test_controle_do_gabriel(ligado):
    ag, p, ui, eva, _ = ligado
    r = await eva.pedir("mouse", {"acao": "clicar", "x": 5, "y": 5, "origem": "gabriel"})
    assert not r["ok"] and r["codigo"] == "proibido"
    await eva.mandar({"tipo": "controle", "gabriel": True})
    assert await esperar(lambda: ag.controle_gabriel)
    r = await eva.pedir("mouse", {"acao": "clicar", "x": 5, "y": 5, "origem": "gabriel"})
    assert r["ok"]
    assert any(e[0] == "conectado" and "você está no controle" in e[1] for e in ui.estados)


async def test_tela_ao_vivo(ligado):
    ag, p, ui, eva, _ = ligado
    await eva.mandar({"tipo": "ao_vivo", "ligar": True, "fps": 5})
    assert await esperar(lambda: len(eva.quadros) >= 1, 5)
    q = eva.quadros[0]
    assert q[:1] == b"F" and q[1:3] == b"\xff\xd8"
    # tela parada: não reenvia o mesmo quadro (economiza internet)
    n = len(eva.quadros)
    await asyncio.sleep(0.6)
    assert len(eva.quadros) == n
    p.cor = (200, 10, 10)               # a tela mudou
    assert await esperar(lambda: len(eva.quadros) > n, 3)
    # pausado no PC: não transmite (privacidade)
    ag.emergencia()
    await asyncio.sleep(0.3)
    n = len(eva.quadros)
    p.cor = (1, 200, 1)
    await asyncio.sleep(0.6)
    assert len(eva.quadros) == n
    ag.retomar()
    assert await esperar(lambda: len(eva.quadros) > n, 3)
    await eva.mandar({"tipo": "ao_vivo", "ligar": False})
    await asyncio.sleep(0.3)
    n = len(eva.quadros)
    p.cor = (9, 9, 200)
    await asyncio.sleep(0.6)
    assert len(eva.quadros) == n


async def test_ao_vivo_adapta_qualidade(ligado):
    ag, p, ui, eva, _ = ligado
    eva.ack = False          # internet "lenta": a Eva não confirma os quadros
    ag.espera_ack = 0.1
    q0 = ag.qualidade
    await eva.mandar({"tipo": "ao_vivo", "ligar": True, "fps": 5})
    for i in range(4):
        p.cor = (i * 40, 0, 0)
        await asyncio.sleep(0.3)
    assert ag.qualidade < q0
    ag._adaptar(0.1)
    ag._adaptar(0.1)
    assert ag.qualidade > 30


async def test_reconecta_sozinho(ligado):
    ag, p, ui, eva, _ = ligado
    await eva.ws.close(1011, "reiniciando")
    assert await esperar(lambda: eva.conexoes == 2 and ag.conectado, 5)
    assert (await eva.pedir("info"))["ok"]
    assert any(e[0] == "desconectado" for e in ui.estados)


async def test_desconectar_pelo_icone(ligado):
    ag, p, ui, eva, tarefa = ligado
    ag.desconectar()
    assert await asyncio.wait_for(tarefa, 5) == "desconectado"
    assert {"tipo": "desconectar"} in eva.msgs


async def test_token_revogado(eva, tmp_path):
    ui = Registro()
    ag = Agente(Config(servidor=eva.endereco, token="evapc_token-errado", manter_eva_acordada=False),
                PlataformaFalsa(), ui, casa=tmp_path)
    assert await asyncio.wait_for(ag.rodar(), 10) == "revogado"
    assert ui.revogados and ui.estados[-1][0] == "parado"
    assert any(e[0] == "revogado" for e in ui.estados)


async def test_outro_pc_espera_mais(eva, tmp_path):
    eva.recusar = 4409
    ui = Registro()
    ag = Agente(Config(servidor=eva.endereco, token=eva.token, manter_eva_acordada=False), PlataformaFalsa(), ui,
                casa=tmp_path)
    t = asyncio.ensure_future(ag.rodar())
    assert await esperar(lambda: any("5 minutos" in a[1] for a in ui.avisos), 5)
    assert any("300 s" in e[1] for e in ui.estados)
    ag.sair()
    assert await asyncio.wait_for(t, 5) == "sair"


def test_interface_padrao_nao_faz_nada():
    i = Interface()
    i.estado("conectado", "x")
    i.banner(True, "x")
    i.aviso("a", "b")
    i.token_invalido("x")
