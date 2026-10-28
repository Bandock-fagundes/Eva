"""Servidor: login/cookie, rate limit, /api/saude, /api/chat, endpoints, WebSocket e reconexão."""
import time

import pytest
from fastapi.testclient import TestClient

from eva import server
from eva.memory import memory
from tests.conftest import SENHA_TESTE


@pytest.fixture
def cliente():
    with TestClient(server.app) as c:
        yield c


@pytest.fixture
def logado(cliente):
    r = cliente.post("/login", json={"senha": SENHA_TESTE})
    assert r.status_code == 200 and r.json()["ok"]
    return cliente


def _receber_ate(ws, tipo, limite=300, filtro=None):
    vistos = []
    for _ in range(limite):
        ev = ws.receive_json()
        vistos.append(ev)
        if ev["tipo"] == tipo and (filtro is None or filtro(ev)):
            return ev, vistos
    raise AssertionError(f"não chegou '{tipo}'. Vistos: {[v['tipo'] for v in vistos]}")


# ------------------------------------------------------------------ login

def test_saude_publica_sem_dados_sensiveis(cliente):
    r = cliente.get("/api/saude")
    assert r.status_code == 200
    d = r.json()
    assert d == {"ok": True, "versao": "1.0", "banco": True, "chave_configurada": True}
    assert cliente.get("/api/saude?banco=0").json()["banco"] is None


def test_api_exige_login(cliente):
    assert cliente.get("/api/estado").status_code == 401
    assert cliente.post("/api/chat", json={"texto": "oi"}).status_code == 401
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_errado_e_certo(cliente):
    r = cliente.post("/login", json={"senha": "errada"})
    assert r.status_code == 401 and r.json()["erro"] == "Senha errada."
    assert cliente.get("/api/estado").status_code == 401
    r = cliente.post("/login", data={"senha": SENHA_TESTE}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.cookies.get(server.COOKIE)
    assert cookie and SENHA_TESTE not in cookie
    assert cliente.get("/api/estado").status_code == 200


def test_cookie_falsificado_nao_entra(cliente):
    cliente.cookies.set(server.COOKIE, "eyJ1IjoiZ2FicmllbCJ9.falso.assinatura")
    assert cliente.get("/api/estado").status_code == 401
    assert server.token_valido(server.criar_token())
    assert not server.token_valido("lixo")


def test_rate_limit_de_login(cliente):
    for _ in range(server.MAX_FALHAS_IP):
        cliente.post("/login", json={"senha": "chute"})
    r = cliente.post("/login", json={"senha": SENHA_TESTE})
    assert r.status_code == 429
    assert "tentativas" in r.json()["erro"]


def test_logout(logado):
    logado.post("/logout")
    logado.cookies.clear()
    assert logado.get("/api/estado").status_code == 401


def test_paginas_estaticas(cliente):
    assert "E.V.A." in cliente.get("/login").text
    assert cliente.get("/sw.js").status_code == 200
    assert cliente.get("/manifest.json").json()["short_name"] == "E.V.A."
    assert cliente.get("/static/app.js").status_code == 200
    for caminho in ("/static/../eva/config.py", "/static/..%2Feva%2Fconfig.py", "/static/.env"):
        r = cliente.get(caminho)
        assert "LLM_API_KEY" not in r.text and "EVA_SENHA" not in r.text


# ------------------------------------------------------------------ /api/chat

def test_api_chat_resposta_simples(logado, fake):
    fake.adicionar({"texto": "Oi, Gabriel!"})
    r = logado.post("/api/chat", json={"texto": "Oi Eva"})
    assert r.status_code == 200
    d = r.json()
    assert d["resposta"] == "Oi, Gabriel!" and d["status"] == "concluida" and d["modelo"] == "gemini-3.8-flash"
    tipos = [e["tipo"] for e in d["eventos"]]
    assert "resposta" in tipos and "fim" in tipos and "token" not in tipos


def test_api_chat_com_ferramenta(logado, fake):
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "S1"}]},
                   {"texto": "Agora são X horas."})
    d = logado.post("/api/chat", json={"texto": "que horas são?"}).json()
    assert d["resposta"] == "Agora são X horas."
    assert any(e["tipo"] == "acao" and e["ferramenta"] == "hora_atual" for e in d["eventos"])


def test_api_chat_aprovacao_negada_por_padrao(logado, fake):
    fake.adicionar({"tool_calls": [{"nome": "executar_comando", "args": {"comando": "ls"}}]}, {"texto": "não rodei"})
    d = logado.post("/api/chat", json={"texto": "roda ls"}).json()
    assert d["resposta"] == "não rodei"
    assert not any(e["tipo"] == "aprovacao" for e in d["eventos"])
    assert any(e["tipo"] == "resultado" and e["texto"] == "Negado." for e in d["eventos"])


def test_api_chat_erro_amigavel(logado, fake):
    fake.modelos_disponiveis = set()
    d = logado.post("/api/chat", json={"texto": "oi"}).json()
    assert "Error code" not in d["resposta"] and "modelo" in d["resposta"].lower()


def test_api_chat_desligada(logado, fake):
    logado.post("/api/desligar")
    r = logado.post("/api/chat", json={"texto": "oi"})
    assert r.status_code == 409 and "desligada" in r.json()["erro"]
    assert logado.get("/api/estado").json()["desligada"] is True
    logado.post("/api/ligar")
    fake.adicionar({"texto": "voltei"})
    assert logado.post("/api/chat", json={"texto": "oi"}).json()["resposta"] == "voltei"


def test_estado_mostra_modelo_em_uso(logado, fake):
    fake.modelos_disponiveis = {"gemini-3.6-flash"}
    fake.adicionar({"texto": "ok"})
    logado.post("/api/chat", json={"texto": "oi"})
    e = logado.get("/api/estado").json()
    assert e["modelo"] == "gemini-3.6-flash" and e["versao"] == "1.0"
    status = {m["modelo"]: m["status"] for m in e["modelos"]}
    assert status["gemini-3.8-flash"] == "indisponivel" and status["gemini-3.6-flash"] == "ok"


# ------------------------------------------------------------------ endpoints

def test_historico(logado, fake):
    fake.adicionar({"texto": "resposta"})
    logado.post("/api/chat", json={"texto": "pergunta"})
    h = logado.get("/api/historico").json()
    assert [(x["papel"], x["conteudo"]) for x in h] == [("user", "pergunta"), ("assistant", "resposta")]


def test_persona_editar_e_restaurar(logado):
    p = logado.get("/api/persona").json()
    assert p["padrao"] and "E.V.A." in p["texto"]
    logado.post("/api/persona", json={"texto": "Você é a Eva pirata. {usuario} é o capitão."})
    p2 = logado.get("/api/persona").json()
    assert not p2["padrao"] and "pirata" in p2["texto"]
    r = logado.post("/api/persona/restaurar").json()
    assert "E.V.A." in r["texto"]
    assert logado.get("/api/persona").json()["padrao"]
    assert logado.post("/api/persona", json={"texto": "   "}).status_code == 400


def test_persona_editada_vai_pro_prompt(logado, fake):
    logado.post("/api/persona", json={"texto": "Você é a Eva pirata. {usuario} é o capitão."})
    fake.adicionar({"texto": "Arrr"})
    logado.post("/api/chat", json={"texto": "oi"})
    assert "Gabriel é o capitão" in fake.requisicoes[-1]["messages"][0]["content"]


def test_permissoes(logado):
    lista = logado.get("/api/permissoes").json()
    por_nome = {p["ferramenta"]: p for p in lista}
    assert por_nome["executar_comando"]["valor"] == "perguntar"
    assert por_nome["escrever_arquivo"]["valor"] == "perguntar"
    assert por_nome["esquecer"]["valor"] == "perguntar"
    assert por_nome["navegador_clicar"]["sensivel"] is True
    assert logado.post("/api/permissoes", json={"ferramenta": "buscar_web", "valor": "bloquear"}).json()["ok"]
    assert {p["ferramenta"]: p["valor"] for p in logado.get("/api/permissoes").json()}["buscar_web"] == "bloquear"
    assert logado.post("/api/permissoes", json={"ferramenta": "buscar_web", "valor": "talvez"}).status_code == 400
    assert logado.post("/api/permissoes", json={"ferramenta": "nao_existe", "valor": "permitir"}).status_code == 404
    logado.post("/api/permissoes/restaurar")
    assert {p["ferramenta"]: p["valor"] for p in logado.get("/api/permissoes").json()}["buscar_web"] == "permitir"


def test_memorias_crud(logado):
    r = logado.post("/api/memorias", json={"conteudo": "O Gabriel gosta de rap geek.", "categoria": "preferencias"})
    mid = r.json()["id"]
    lista = logado.get("/api/memorias").json()
    assert lista[0]["conteudo"] == "O Gabriel gosta de rap geek." and lista[0]["origem"] == "gabriel"
    assert logado.put(f"/api/memorias/{mid}", json={"conteudo": "O Gabriel escreve rap geek.", "importancia": 5}).json()["ok"]
    m = memory.obter(mid)
    assert m["conteudo"] == "O Gabriel escreve rap geek." and m["importancia"] == 5
    assert logado.get("/api/memorias?busca=rap").json()[0]["id"] == mid
    assert logado.delete(f"/api/memorias/{mid}").json()["ok"]
    assert logado.get("/api/memorias").json() == []
    assert logado.put("/api/memorias/9999", json={"conteudo": "x"}).status_code == 404
    assert logado.post("/api/memorias", json={"conteudo": "minha senha é 123"}).status_code == 400


def test_log_paginado_e_filtro(logado, fake):
    for i in range(3):
        fake.adicionar({"texto": f"r{i}"})
        logado.post("/api/chat", json={"texto": f"msg {i}"})
    d = logado.get("/api/log?pagina=1&tamanho=5").json()
    assert len(d["itens"]) == 5 and d["total"] > 5
    d2 = logado.get("/api/log?pagina=2&tamanho=5").json()
    assert {i["id"] for i in d["itens"]}.isdisjoint({i["id"] for i in d2["itens"]})
    f = logado.get("/api/log?tipo=resposta_eva").json()
    assert f["total"] == 3 and all(i["tipo"] == "resposta_eva" for i in f["itens"])
    b = logado.get("/api/log?busca=msg 1").json()
    assert b["total"] >= 1
    assert "resposta_eva" in logado.get("/api/log/tipos").json()


def test_lembretes_api(logado):
    r = logado.post("/api/lembretes", json={"quando": "em 2 horas", "texto": "estudar"})
    assert r.json()["ok"]
    lid = r.json()["id"]
    lista = logado.get("/api/lembretes").json()
    assert lista[0]["texto"] == "estudar"
    assert logado.post("/api/lembretes", json={"quando": "ontem de tarde", "texto": "x"}).status_code == 400
    assert logado.delete(f"/api/lembretes/{lid}").json()["ok"]
    assert logado.get("/api/lembretes").json() == []


def test_tela_vazia(logado):
    assert logado.get("/api/tela").status_code == 204


# ------------------------------------------------------------------ WebSocket

def test_ws_sem_login_e_fechado(cliente):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_ws_origem_estranha_recusada(logado):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with logado.websocket_connect("/ws", headers={"origin": "https://site-malicioso.com"}) as ws:
            ws.receive_json()


def test_ws_fluxo_completo(logado, fake):
    fake.adicionar({"texto": "Vou ver a hora.", "tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "W1"}]},
                   {"texto": "São **tantas** horas."})
    with logado.websocket_connect("/ws") as ws:
        inicial = ws.receive_json()
        assert inicial["tipo"] == "estado" and inicial["desligada"] is False
        ws.send_json({"tipo": "ping"})
        _receber_ate(ws, "pong")
        ws.send_json({"tipo": "mensagem", "texto": "que horas são?"})
        resp, vistos = _receber_ate(ws, "resposta")
        tipos = [v["tipo"] for v in vistos]
        for t in ("pensando", "token", "narracao", "acao", "resultado"):
            assert t in tipos, t
        assert resp["texto"] == "São **tantas** horas." and resp["modelo"] == "gemini-3.8-flash"
        fim, _ = _receber_ate(ws, "fim")
        assert fim["status"] == "concluida"


def test_ws_aprovacao_e_pergunta(logado, fake):
    fake.adicionar({"tool_calls": [{"nome": "escrever_arquivo", "args": {"caminho": "ws.txt", "conteudo": "via ws"}}]},
                   {"tool_calls": [{"nome": "perguntar_ao_gabriel", "args": {"pergunta": "Mais alguma coisa?"}}]},
                   {"texto": "Pronto."})
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"tipo": "mensagem", "texto": "escreve um arquivo"})
        ap, _ = _receber_ate(ws, "aprovacao", filtro=lambda e: "ferramenta" in e)
        assert ap["ferramenta"] == "escrever_arquivo" and ap["args"]["caminho"] == "ws.txt"
        ws.send_json({"tipo": "aprovacao", "id": ap["id"], "aprovado": True})
        pq, _ = _receber_ate(ws, "pergunta", filtro=lambda e: "pergunta" in e)
        ws.send_json({"tipo": "resposta", "id": pq["id"], "texto": "não, valeu"})
        resp, _ = _receber_ate(ws, "resposta")
        assert resp["texto"] == "Pronto."
    from eva.config import WORKSPACE
    assert (WORKSPACE / "ws.txt").read_text() == "via ws"
    tool_msgs = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"]
    assert "não, valeu" in tool_msgs[1]


def test_ws_parar(logado, fake):
    fake.adicionar({"texto": "demorado", "dormir": 3})
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"tipo": "mensagem", "texto": "algo lento"})
        _receber_ate(ws, "pensando")
        ws.send_json({"tipo": "parar"})
        fim, vistos = _receber_ate(ws, "fim")
        assert fim["status"] == "cancelada", vistos


def test_reconexao_tarefa_continua_sem_socket(logado, fake):
    """Fecha o WebSocket no meio da tarefa: ela continua, e ao reconectar a tela recebe o estado/histórico."""
    fake.adicionar({"texto": "Resposta que chegou com a tela fechada.", "dormir": 0.6})
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"tipo": "mensagem", "texto": "faz isso enquanto eu fecho o app"})
        _receber_ate(ws, "pensando")
    # socket fechado; reconecta enquanto ainda roda
    with logado.websocket_connect("/ws") as ws2:
        est = ws2.receive_json()
        assert est["tipo"] == "estado"
        assert est["trabalhando"] is True
        assert est["tarefa"]["texto"] == "faz isso enquanto eu fecho o app"
        assert any(e["tipo"] == "pensando" for e in est["tarefa"]["eventos"])  # replay
        resp, _ = _receber_ate(ws2, "resposta")
        assert resp["texto"] == "Resposta que chegou com a tela fechada."


def test_reconexao_depois_de_terminar_tem_historico(logado, fake):
    fake.adicionar({"texto": "Terminei sozinha.", "dormir": 0.2})
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"tipo": "mensagem", "texto": "tarefa"})
        _receber_ate(ws, "pensando")
    for _ in range(100):
        h = logado.get("/api/historico").json()
        if h and h[-1]["papel"] == "assistant":
            break
        time.sleep(0.05)
    assert h[-1]["conteudo"] == "Terminei sozinha."
    with logado.websocket_connect("/ws") as ws2:
        est = ws2.receive_json()
        assert est["trabalhando"] is False


def test_ws_aprovacao_pendente_reaparece_ao_reconectar(logado, fake):
    fake.adicionar({"tool_calls": [{"nome": "executar_comando", "args": {"comando": "echo oi"}}]}, {"texto": "rodei"})
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"tipo": "mensagem", "texto": "roda echo"})
        ap, _ = _receber_ate(ws, "aprovacao", filtro=lambda e: "ferramenta" in e)
    with logado.websocket_connect("/ws") as ws2:
        est = ws2.receive_json()
        pend = est["tarefa"]["pendentes"]
        assert pend and pend[0]["id"] == ap["id"]
        ws2.send_json({"tipo": "aprovacao", "id": ap["id"], "aprovado": True})
        resp, _ = _receber_ate(ws2, "resposta")
        assert resp["texto"] == "rodei"


def test_lembrete_chega_no_websocket(logado):
    import asyncio
    from datetime import timedelta
    from eva import lembretes, tempo
    from eva.db import db
    r = lembretes.criar("em 1 minuto", "tomar água")
    db.run("UPDATE lembretes SET quando=? WHERE id=?", (tempo.para_utc_iso(tempo.agora() - timedelta(seconds=1)), r["id"]))
    lembretes.agendador.invalidar()
    with logado.websocket_connect("/ws") as ws:
        ws.receive_json()
        logado.portal.call(lembretes.agendador.verificar) if hasattr(logado, "portal") and logado.portal else \
            asyncio.run(lembretes.agendador.verificar())
        ev, _ = _receber_ate(ws, "lembrete")
        assert "tomar água" in ev["texto"]
