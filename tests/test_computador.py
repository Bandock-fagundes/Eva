"""Computador da Eva: ferramentas computador_*, grade 0–1000, print injetado no modelo (e poda), permissões,
killswitch, "assumir controle", escolha computador x navegador antigo e API da aba Tela.
Usa o agente do PC falso (tests/fake_pc.py) e o Gemini falso (tests/fake_llm.py)."""
import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from eva import agent as agent_mod
from eva import brain
from eva.computador import (MARCA_PODADA, MARCA_TELA, ComputadorErro, eh_mensagem_tela, mensagem_tela, pc,
                            podar_telas)
from eva.killswitch import kill_switch
from eva.permissions import permissions
from eva.tools import TOOLS
from eva.tools import computador as ferr
from tests.conftest import SENHA_TESTE
from tests.fake_pc import JPEG_1PX


def _rotas(est, rota):
    return [c for c in est.chamadas if c["rota"] == rota]


def _ferramentas_oferecidas(req):
    return {t["function"]["name"] for t in req.get("tools", [])}


def _imagens(msgs):
    return [m for m in msgs if isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])]


# ---------------------------------------------------------------- grade de coordenadas

def test_grade_para_pixels():
    pc.largura, pc.altura = 1280, 800
    assert pc.para_pixels(0, 0) == (0, 0)
    assert pc.para_pixels(1000, 1000) == (1279, 799)
    assert pc.para_pixels(500, 500) == (640, 400)
    assert pc.para_pixels(250, 750) == (320, 599)
    assert pc.para_pixels(-50, 2000) == (0, 799)          # fora da grade: prende na borda
    assert pc.para_pixels("100", "200") == (128, 160)     # número como texto também vale
    assert pc.para_grade(640, 400) == (500, 501)
    with pytest.raises(ComputadorErro):
        pc.para_pixels("meio", 10)


async def test_grade_segue_a_resolucao_real(pc_fake):
    pc_fake.largura, pc_fake.altura = 1920, 1080
    await pc.info()
    assert (pc.largura, pc.altura) == (1920, 1080)
    assert pc.para_pixels(1000, 1000) == (1919, 1079)
    r = await ferr.computador_clicar(500, 500)
    assert _rotas(pc_fake, "/mouse")[-1]["corpo"] == {"acao": "clicar", "x": 960, "y": 540}
    assert "pixel (960, 540)" in r


# ---------------------------------------------------------------- cada ferramenta

async def test_clicar_tipos(pc_fake):
    await ferr.computador_clicar(100, 200)
    await ferr.computador_clicar(100, 200, "duplo")
    await ferr.computador_clicar(100, 200, "direito")
    await ferr.computador_clicar(100, 200, "mover")
    acoes = [c["corpo"]["acao"] for c in _rotas(pc_fake, "/mouse")]
    assert acoes == ["clicar", "duplo", "direito", "mover"]
    assert _rotas(pc_fake, "/mouse")[0]["corpo"] == {"acao": "clicar", "x": 128, "y": 160}


async def test_digitar_unicode_limpar_e_enter(pc_fake):
    r = await ferr.computador_digitar("Olá, ação ✓ 😀", enter=True, limpar=True)
    corpos = [c["corpo"] for c in _rotas(pc_fake, "/teclado")]
    assert corpos[0] == {"teclas": "ctrl+a BackSpace"}
    assert corpos[1] == {"texto": "Olá, ação ✓ 😀", "teclas": "Return"}
    assert "13 caractere(s) e apertei Enter" in r
    assert "Nova aba - Chromium" in r  # a janela ativa volta para o modelo
    assert (await ferr.computador_digitar("x" * 5001)).startswith("ERRO")


async def test_tecla_rolar_arrastar(pc_fake):
    await ferr.computador_tecla("ctrl+l")
    assert _rotas(pc_fake, "/teclado")[-1]["corpo"] == {"teclas": "ctrl+l"}
    await ferr.computador_rolar("baixo")
    await ferr.computador_rolar("cima", 3, 100, 100)
    await ferr.computador_rolar("direita", 50)
    m = [c["corpo"] for c in _rotas(pc_fake, "/mouse")]
    assert m[0] == {"acao": "rolar", "x": 640, "y": 400, "dy": 5, "dx": 0}
    assert m[1] == {"acao": "rolar", "x": 128, "y": 80, "dy": -3, "dx": 0}
    assert m[2]["dx"] == 15 and m[2]["dy"] == 0          # máximo de 15 cliques
    assert (await ferr.computador_rolar("lado")).startswith("ERRO")
    await ferr.computador_arrastar(0, 0, 1000, 500)
    assert _rotas(pc_fake, "/mouse")[-1]["corpo"] == {"acao": "arrastar", "x": 0, "y": 0, "x2": 1279, "y2": 400}


async def test_comando_abrir_ver_tela(pc_fake):
    r = await ferr.computador_comando("ls ~", 999)
    assert _rotas(pc_fake, "/comando")[-1]["corpo"] == {"cmd": "ls ~", "timeout": 300}
    assert "(código de saída 0)" in r and "saida de: ls ~" in r
    r = await ferr.computador_abrir(url="https://exemplo.com")
    assert _rotas(pc_fake, "/abrir")[-1]["corpo"] == {"url": "https://exemplo.com"}
    assert "Abri https://exemplo.com" in r
    await ferr.computador_abrir(app="planilha")
    assert _rotas(pc_fake, "/abrir")[-1]["corpo"] == {"app": "planilha"}
    assert (await ferr.computador_abrir()).startswith("ERRO")
    r = await ferr.computador_abrir(app="inexistente")
    assert r.startswith("ERRO do computador (400)")
    r = await ferr.computador_ver_tela()
    assert "Tela 1280x800" in r and "(500, 501)" in r


async def test_erros_amigaveis():
    pc.configurar(url="http://127.0.0.1:9", token="x" * 30)   # porta fechada
    r = await ferr.computador_clicar(1, 1)
    assert r.startswith("ERRO: não consegui falar com o computador")
    assert pc.online is False
    pc.configurar(url="", token="")
    assert "não está configurado" in await ferr.computador_tecla("enter")


async def test_token_errado(pc_fake):
    pc.token = "token-errado"
    pc._clientes.clear()
    assert "recusou o token" in await ferr.computador_clicar(1, 1)


# ---------------------------------------------------------------- mensagens com print e poda

def test_mensagem_tela_e_poda():
    m = mensagem_tela(JPEG_1PX, 1280, 800)
    assert m["role"] == "user" and eh_mensagem_tela(m)
    url = m["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,") and base64.b64decode(url.split(",", 1)[1]) == JPEG_1PX
    assist = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"},
         "extra_content": {"google": {"thought_signature": "SIG"}}}]}
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "oi"}]
    for _ in range(4):
        msgs += [assist, {"role": "tool", "tool_call_id": "c1", "content": "ok"}, mensagem_tela(JPEG_1PX, 1, 1)]
    assert podar_telas(msgs, 2) == 2
    assert len(_imagens(msgs)) == 2
    assert [m["content"] for m in msgs if m.get("content") == MARCA_PODADA] == [MARCA_PODADA] * 2
    assert all(m is assist for m in msgs if m["role"] == "assistant")   # assinaturas intactas
    assert podar_telas(msgs, 0) == 2 and not _imagens(msgs)


@pytest.mark.parametrize("streaming", [True, False])
async def test_print_injetado_depois_de_cada_acao(pc_fake, fake, eventos, rodar_tarefa, streaming):
    """Fluxo completo: o modelo age 4 vezes; depois de cada passo recebe um print novo (mensagem de usuário com
    imagem logo após as respostas das ferramentas); só os 2 mais recentes ficam; thought_signature preservada."""
    pc.configurar(url=pc.url, token=pc.token, vnc_url=pc.vnc_base, vnc_senha=pc.vnc_senha)
    from eva.llm import cerebro
    cerebro.streaming = streaming
    fake.adicionar(
        {"tool_calls": [{"nome": "computador_abrir", "args": {"url": "https://g1.globo.com"}, "assinatura": "SIG-1"}]},
        {"tool_calls": [{"nome": "computador_clicar", "args": {"x": 500, "y": 300}, "assinatura": "SIG-2"}]},
        {"tool_calls": [{"nome": "computador_digitar", "args": {"texto": "notícias", "enter": True},
                         "assinatura": "SIG-3"},
                        {"nome": "computador_ver_tela", "args": {}}]},           # 2 ferramentas no mesmo passo
        {"tool_calls": [{"nome": "computador_rolar", "args": {"direcao": "baixo"}, "assinatura": "SIG-4"}]},
        {"texto": "Pronto: abri o g1 e pesquisei notícias."},
    )
    t = await rodar_tarefa("abre o g1 no computador e pesquisa notícias")
    assert t.status == "concluida", t.eventos
    assert t.resposta == "Pronto: abri o g1 e pesquisei notícias."
    reqs = fake.requisicoes
    assert len(reqs) == 5
    # 1ª chamada: ferramentas do computador oferecidas e navegador antigo escondido; prompt com o guia de uso
    nomes = _ferramentas_oferecidas(reqs[0])
    assert {"computador_clicar", "computador_ver_tela", "computador_comando"} <= nomes
    assert not any(n.startswith("navegador_") for n in nomes)
    assert "grade 0–1000" in reqs[0]["messages"][0]["content"] and "Assumir controle" in reqs[0]["messages"][0]["content"]
    # 2ª chamada: tool -> print (nessa ordem), print é JPEG em base64
    m2 = reqs[1]["messages"]
    assert m2[-2]["role"] == "tool" and eh_mensagem_tela(m2[-1])
    assert base64.b64decode(m2[-1]["content"][1]["image_url"]["url"].split(",", 1)[1]) == JPEG_1PX
    # passo com 2 ferramentas: as duas respostas de ferramenta vêm antes de UM print só
    m4 = reqs[3]["messages"]
    assert [m["role"] for m in m4[-3:]] == ["tool", "tool", "user"] and eh_mensagem_tela(m4[-1])
    # última chamada: só 2 prints guardados, os antigos viraram texto
    ultima = reqs[-1]["messages"]
    assert len(_imagens(ultima)) == 2
    assert sum(1 for m in ultima if m.get("content") == MARCA_PODADA) == 2
    assist = [m for m in ultima if m["role"] == "assistant" and m.get("tool_calls")]
    assert [a["tool_calls"][0]["extra_content"]["google"]["thought_signature"] for a in assist] == \
        ["SIG-1", "SIG-2", "SIG-3", "SIG-4"]
    # o agente do PC recebeu as ações certas e 4 prints (um por passo)
    assert _rotas(pc_fake, "/abrir")[0]["corpo"] == {"url": "https://g1.globo.com"}
    assert _rotas(pc_fake, "/mouse")[0]["corpo"] == {"acao": "clicar", "x": 640, "y": 240}
    assert pc_fake.prints == 4 and pc.tela == JPEG_1PX
    telas = [e for e in eventos if e["tipo"] == "tela" and e.get("fonte") == "computador"]
    assert len(telas) == 4
    assert pc.acao_atual is None  # limpa no fim da tarefa
    # nada de imagem vai para o histórico salvo
    from eva.memory import memory
    assert all(isinstance(h["conteudo"], str) and "base64" not in h["conteudo"] for h in memory.history())


async def test_print_falhou_nao_derruba(pc_fake, fake, rodar_tarefa):
    pc_fake.falhar_tela = True
    fake.adicionar({"tool_calls": [{"nome": "computador_clicar", "args": {"x": 1, "y": 1}}]}, {"texto": "ok"})
    t = await rodar_tarefa("clica")
    assert t.status == "concluida"
    ult = fake.requisicoes[-1]["messages"][-1]
    assert ult["role"] == "user" and "Não consegui tirar o print" in ult["content"]


# ---------------------------------------------------------------- computador x navegador antigo

async def test_sem_computador_oferece_navegador(fake, rodar_tarefa):
    fake.adicionar({"texto": "oi"})
    await rodar_tarefa("oi")
    nomes = _ferramentas_oferecidas(fake.requisicoes[0])
    assert "navegador_abrir" in nomes and not any(n.startswith("computador_") for n in nomes)
    assert "Teu computador" not in fake.requisicoes[0]["messages"][0]["content"]


async def test_computador_fora_do_ar_volta_pro_navegador(pc_fake, fake, rodar_tarefa):
    pc_fake.saude_ok = False
    fake.adicionar({"texto": "oi"})
    await rodar_tarefa("usa o computador")
    nomes = _ferramentas_oferecidas(fake.requisicoes[0])
    assert "navegador_abrir" in nomes and "computador_clicar" not in nomes
    assert "DESLIGADO ou fora do ar" in fake.requisicoes[0]["messages"][0]["content"]


async def test_prompt_com_computador():
    s = await brain.build_system_prompt("x", None, "online")
    assert "Teu computador" in s and "computador_*" in s and "navegador_abrir" not in s
    assert "NUNCA digite senhas" in s
    s = await brain.build_system_prompt("x", None, None)
    assert "navegador_abrir" in s and "Teu computador" not in s


# ---------------------------------------------------------------- permissões

def test_permissoes_padrao():
    assert permissions.padrao("computador_ver_tela") == "permitir"
    assert permissions.padrao("computador_clicar") == "permitir"
    assert permissions.padrao("computador_rolar") == "permitir"
    assert permissions.padrao("computador_abrir") == "permitir"
    assert permissions.padrao("computador_digitar") == "permitir"
    assert permissions.padrao("computador_comando") == "perguntar"
    assert TOOLS["computador_digitar"]["sensivel"] and TOOLS["computador_clicar"]["sensivel"]


async def test_comando_pede_aprovacao(pc_fake, fake, rodar_tarefa):
    fake.adicionar({"tool_calls": [{"nome": "computador_comando", "args": {"comando": "rm -rf ~/Downloads"}}]},
                   {"texto": "Tudo bem, não apaguei."})
    t = await rodar_tarefa("limpa os downloads", politica="negar")
    assert t.resposta == "Tudo bem, não apaguei."
    assert not _rotas(pc_fake, "/comando") and pc_fake.prints == 0   # negado: não roda e não tira print
    tool_msg = [m for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith("NEGADO")


async def test_comando_aprovado(pc_fake, fake, eventos):
    fake.adicionar({"tool_calls": [{"nome": "computador_comando", "args": {"comando": "df -h"}}]}, {"texto": "feito"})
    t = await agent_mod.ger.enviar("vê o disco", origem="teste")
    for _ in range(200):
        ap = [e for e in eventos if e["tipo"] == "aprovacao" and e.get("ferramenta")]
        if ap:
            break
        await asyncio.sleep(0.02)
    assert ap[0]["ferramenta"] == "computador_comando"
    await agent_mod.ger.responder_aprovacao(ap[0]["id"], True)
    await asyncio.wait_for(t.fim.wait(), 10)
    assert _rotas(pc_fake, "/comando")[0]["corpo"]["cmd"] == "df -h" and pc_fake.prints == 1


@pytest.mark.parametrize("janela,texto,pede", [
    ("Nova aba - Chromium", "receita de bolo", False),
    ("Nova aba - Chromium", "4111 1111 1111 1111", True),       # cartão (Luhn)
    ("Nova aba - Chromium", "meu cpf 529.982.247-25", True),     # CPF válido
    ("Nova aba - Chromium", "liga pra 48 98863-4153", False),    # telefone não dispara
    ("Fazer login nas Contas do Google - Chromium", "gabriel@gmail.com", True),
    ("Pagamento - Loja - Chromium", "Rua X, 10", True),
])
async def test_digitar_sensivel(pc_fake, janela, texto, pede):
    pc_fake.janela = janela
    motivo = await TOOLS["computador_digitar"]["sensivel"]({"texto": texto})
    assert bool(motivo) is pede, motivo


async def test_clicar_e_enter_em_pagamento_pedem_aprovacao(pc_fake, fake, rodar_tarefa):
    pc_fake.janela = "Checkout - Finalizar compra - Chromium"
    assert await TOOLS["computador_clicar"]["sensivel"]({"x": 1, "y": 1})
    assert await TOOLS["computador_clicar"]["sensivel"]({"x": 1, "y": 1, "tipo": "mover"}) is None
    assert await TOOLS["computador_tecla"]["sensivel"]({"teclas": "enter"})
    assert await TOOLS["computador_tecla"]["sensivel"]({"teclas": "ctrl+l"}) is None
    fake.adicionar({"tool_calls": [{"nome": "computador_clicar", "args": {"x": 800, "y": 900}}]},
                   {"texto": "Não confirmei a compra."})
    t = await rodar_tarefa("finaliza", politica="negar")
    assert t.resposta == "Não confirmei a compra." and not _rotas(pc_fake, "/mouse")


async def test_ferramenta_bloqueada(pc_fake, fake, rodar_tarefa):
    permissions.set("computador_clicar", "bloquear")
    fake.adicionar({"tool_calls": [{"nome": "computador_clicar", "args": {"x": 1, "y": 1}}]}, {"texto": "ok"})
    await rodar_tarefa("clica")
    assert not _rotas(pc_fake, "/mouse") and pc_fake.prints == 0


# ---------------------------------------------------------------- killswitch e controle do Gabriel

async def test_killswitch_para_o_computador(pc_fake):
    n = await agent_mod.desligar_tudo("teste")
    assert n == 0 and kill_switch.off
    assert _rotas(pc_fake, "/parar")            # mandou parar comandos e soltar mouse/teclas
    r = await ferr.computador_clicar(10, 10)
    assert r.startswith("ERRO: a Eva está desligada") and not _rotas(pc_fake, "/mouse")
    agent_mod.ligar()
    assert (await ferr.computador_clicar(10, 10)).startswith("Cliquei")


async def test_killswitch_no_meio_da_tarefa(pc_fake, fake, rodar_tarefa, eventos):
    fake.adicionar({"tool_calls": [{"nome": "computador_clicar", "args": {"x": 1, "y": 1}}]},
                   {"tool_calls": [{"nome": "computador_clicar", "args": {"x": 2, "y": 2}}], "dormir": 3},
                   {"texto": "não devia chegar aqui"})
    t = await agent_mod.ger.enviar("clica duas vezes", origem="teste")
    for _ in range(300):
        if pc_fake.prints >= 1:
            break
        await asyncio.sleep(0.02)
    await agent_mod.desligar_tudo("teste")
    await asyncio.wait_for(t.fim.wait(), 10)
    assert t.status == "cancelada"
    assert len(_rotas(pc_fake, "/mouse")) == 1 and _rotas(pc_fake, "/parar")


async def test_gabriel_no_controle(pc_fake, fake, rodar_tarefa):
    pc.controle_gabriel = True
    assert "assumiu o controle" in await ferr.computador_clicar(1, 1)
    fake.adicionar({"tool_calls": [{"nome": "computador_digitar", "args": {"texto": "oi"}}]}, {"texto": "espero"})
    await rodar_tarefa("digita oi")
    assert not _rotas(pc_fake, "/teclado") and pc_fake.prints == 0
    tool_msg = [m for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert "assumiu o controle" in tool_msg["content"]


# ---------------------------------------------------------------- API (aba Tela)

@pytest.fixture
def cliente():
    from eva import server
    with TestClient(server.app) as c:
        assert c.post("/login", json={"senha": SENHA_TESTE}).status_code == 200
        yield c


def test_api_computador_nao_configurado(cliente):
    d = cliente.get("/api/computador").json()
    assert d["configurado"] is False and d["vnc_url"] is None
    assert cliente.get("/api/estado").json()["computador"]["configurado"] is False
    assert cliente.post("/api/computador/controle", json={"gabriel": True}).status_code == 400


def test_api_computador_links_e_controle(pc_fake, cliente):
    d = cliente.get("/api/computador?verificar=1").json()
    assert d["configurado"] and d["online"] and d["ao_vivo"]
    u = d["vnc_url"]
    assert u.startswith("https://1-2-3-4.sslip.io/vnc/vnc.html?autoconnect=true")
    assert "view_only=true" in u and "resize=scale" in u
    consulta, frag = u.split("#", 1)
    assert "senhaVnc" not in consulta                      # senha só no fragmento (não vai pro servidor)
    assert frag == "password=senhaVnc0123456789abcdef&path=vnc%2Fws%2FsenhaVnc0123456789abcdef"
    assert "view_only=false" in d["vnc_url_controle"]
    e = cliente.get("/api/estado").json()["computador"]
    assert e["online"] is True and e["controle_gabriel"] is False
    r = cliente.post("/api/computador/controle", json={"gabriel": True}).json()
    assert r["controle_gabriel"] is True and pc.controle_gabriel
    cliente.post("/api/computador/controle", json={"gabriel": False})
    assert pc.controle_gabriel is False
    assert cliente.get("/api/permissoes").json()  # tela de permissões continua funcionando
    nomes = [p["ferramenta"] for p in cliente.get("/api/permissoes").json()]
    assert "computador_comando" in nomes


def test_vnc_url_deduzida_do_pc_url():
    pc.configurar(url="https://9-9-9-9.sslip.io/pc", token="t" * 30, vnc_url="", vnc_senha="abc12345")
    assert pc.vnc_url() == "https://9-9-9-9.sslip.io/vnc/"
    assert "path=vnc%2Fws%2Fabc12345" in pc.vnc_link()
    pc.configurar(url="http://desktop:8765", token="t" * 30, vnc_url="", vnc_senha="abc12345")
    assert pc.vnc_url() is None and pc.vnc_link() is None


async def test_api_tela_mostra_print_do_computador(pc_fake, cliente):
    await ferr.computador_ver_tela()
    await pc.capturar()
    r = cliente.get("/api/tela")
    assert r.status_code == 200 and r.content == JPEG_1PX and r.headers["x-fonte"] == "computador"


def test_ui_tem_tela_ao_vivo(cliente):
    html = cliente.get("/").text
    assert 'id="pcFrame"' in html and "Assumir controle" in html and "AO VIVO" in html
    assert "ainda não está conectado" in html
    js = cliente.get("/static/app.js").text
    assert "vnc_url_controle" in js and "/api/computador/controle" in js
