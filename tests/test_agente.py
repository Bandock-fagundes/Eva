"""Loop do agente: ferramentas, thought_signature multi-turno, permissões, killswitch, limite, perguntas."""
import asyncio
import json

import pytest

from eva import agent as agent_mod
from eva import audit
from eva.agent import ger, truncar
from eva.killswitch import kill_switch
from eva.memory import memory
from eva.permissions import permissions
from eva.tools import TOOLS, tool


def _tipos(evs):
    return [e["tipo"] for e in evs]


async def test_resposta_simples(fake, eventos, rodar_tarefa):
    fake.adicionar({"texto": "Oi, Gabriel! Tudo certo?"})
    t = await rodar_tarefa("Oi Eva")
    assert t.status == "concluida"
    assert t.resposta == "Oi, Gabriel! Tudo certo?"
    tipos = _tipos(eventos)
    assert "pensando" in tipos and "token" in tipos and "resposta" in tipos and tipos.index("fim") > tipos.index("resposta")
    hist = memory.history()
    assert [h["papel"] for h in hist] == ["user", "assistant"]
    # system prompt tem persona + hora + regras
    sistema = fake.requisicoes[0]["messages"][0]["content"]
    assert "E.V.A." in sistema and "horário de Brasília" in sistema and "buscar_web" in sistema


async def test_loop_com_ferramenta_e_thought_signature(fake, eventos, rodar_tarefa):
    """Gemini 3: o fake recusa (400) se a assinatura não voltar exatamente igual no passo seguinte."""
    fake.adicionar(
        {"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "ASSINATURA-SECRETA-1"}]},
        {"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "ASSINATURA-2"},
                        {"nome": "listar_lembretes", "args": {}}]},  # chamadas paralelas: só a 1ª tem assinatura
        {"texto": "São tantas horas."},
    )
    t = await rodar_tarefa("que horas são?")
    assert t.status == "concluida", t.eventos
    assert t.resposta == "São tantas horas."
    assert len(fake.requisicoes) == 3
    ultima = fake.requisicoes[-1]["messages"]
    assist = [m for m in ultima if m["role"] == "assistant" and m.get("tool_calls")]
    assert assist[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "ASSINATURA-SECRETA-1"
    assert assist[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "ASSINATURA-2"
    assert "extra_content" not in assist[1]["tool_calls"][1]
    tools_msgs = [m for m in ultima if m["role"] == "tool"]
    assert len(tools_msgs) == 3 and all(m["tool_call_id"] for m in tools_msgs)
    assert "horário de Brasília" in tools_msgs[0]["content"]
    assert _tipos(eventos).count("acao") == 3 and _tipos(eventos).count("resultado") == 3


def _sem_extra_content(monkeypatch):
    """Simula a assinatura se perdendo (como a v0.2 fazia ao reconstruir a mensagem do assistente)."""
    from eva.llm import LLM
    original = LLM._chamada_simples

    async def sem_extra(self, kwargs):
        r = await original(self, kwargs)
        for tc in r.mensagem.get("tool_calls", []):
            tc.pop("extra_content", None)
        return r
    monkeypatch.setattr(LLM, "_chamada_simples", sem_extra)
    agent_mod.cerebro.streaming = False


async def test_assinatura_perdida_e_reparada_com_contingencia(fake, rodar_tarefa, monkeypatch):
    _sem_extra_content(monkeypatch)
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "X"}]}, {"texto": "recuperei"})
    t = await rodar_tarefa("que horas são?")
    assert t.resposta == "recuperei"
    # 1ª tentativa recusada (400 por falta de assinatura), 2ª com a assinatura de contingência
    enviadas = [m for m in fake.requisicoes[-1]["messages"] if m.get("tool_calls")]
    assert enviadas[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"


async def test_assinatura_faltando_vira_erro_amigavel(fake, eventos, rodar_tarefa, monkeypatch):
    _sem_extra_content(monkeypatch)
    fake.aceitar_contingencia = False
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "X"}]}, {"texto": "nunca"})
    t = await rodar_tarefa("que horas são?")
    assert "problema técnico" in t.resposta
    assert "Error code" not in t.resposta
    assert any(e.get("tipo") == "erro" for e in t.eventos)
    erros = [i for i in audit.pagina(1, 50, "erro_llm")["itens"]]
    assert erros and "thought_signature" in erros[0]["erro"]


async def test_loop_sem_streaming_tambem_preserva(fake, rodar_tarefa):
    agent_mod.cerebro.streaming = False
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "NS-1"}]}, {"texto": "feito"})
    t = await rodar_tarefa("hora")
    assert t.resposta == "feito"


async def test_ferramenta_inexistente_e_args_invalidos(fake, rodar_tarefa):
    fake.adicionar(
        {"tool_calls": [{"nome": "voar_para_lua", "args": {}}]},
        {"tool_calls": [{"nome": "hora_atual", "args_brutos": "{isso nao e json"}]},
        {"texto": "Corrigi."},
    )
    t = await rodar_tarefa("faz algo")
    assert t.resposta == "Corrigi."
    msgs = fake.requisicoes[-1]["messages"]
    tool_res = [m["content"] for m in msgs if m["role"] == "tool"]
    assert "não existe" in tool_res[0]
    assert "JSON válido" in tool_res[1]


async def test_erro_de_ferramenta_volta_como_texto(fake, rodar_tarefa):
    @tool("ferramenta_quebrada", "teste", {}, rotulo="Quebrando")
    async def ferramenta_quebrada():
        raise RuntimeError("explodiu aqui")
    permissions.overrides["ferramenta_quebrada"] = "permitir"
    try:
        fake.adicionar({"tool_calls": [{"nome": "ferramenta_quebrada", "args": {}}]}, {"texto": "Vi o erro e segui."})
        t = await rodar_tarefa("testa")
        assert t.resposta == "Vi o erro e segui."
        res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
        assert res.startswith("ERRO") and "explodiu aqui" in res
    finally:
        TOOLS.pop("ferramenta_quebrada", None)


def test_truncar_inteligente():
    texto = "INICIO " + "x" * 20000 + " FIM"
    t = truncar(texto, 1000)
    assert len(t) <= 1100
    assert t.startswith("INICIO") and t.endswith("FIM") and "omitidos" in t
    assert truncar("curto", 1000) == "curto"


async def test_resultado_grande_e_truncado(fake, rodar_tarefa):
    @tool("ferramenta_grande", "teste", {})
    async def ferramenta_grande():
        return "COMECO" + "y" * 50000 + "FINAL"
    permissions.overrides["ferramenta_grande"] = "permitir"
    try:
        fake.adicionar({"tool_calls": [{"nome": "ferramenta_grande", "args": {}}]}, {"texto": "ok"})
        await rodar_tarefa("grande")
        res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
        assert len(res) < 9000 and res.startswith("COMECO") and res.endswith("FINAL")
    finally:
        TOOLS.pop("ferramenta_grande", None)


# ------------------------------------------------------------------ permissões

async def _aprovar_quando_pedir(eventos, aprovado: bool):
    for _ in range(200):
        ap = [e for e in eventos if e["tipo"] == "aprovacao" and "ferramenta" in e]
        if ap:
            return await ger.responder_aprovacao(ap[0]["id"], aprovado)
        await asyncio.sleep(0.02)
    raise AssertionError("aprovação nunca foi pedida")


async def test_permissao_perguntar_aprovada(fake, eventos):
    fake.adicionar({"tool_calls": [{"nome": "escrever_arquivo", "args": {"caminho": "nota.txt", "conteudo": "oi"}}]},
                   {"texto": "Escrevi."})
    t = await ger.enviar("escreve uma nota")
    assert await _aprovar_quando_pedir(eventos, True)
    await asyncio.wait_for(t.fim.wait(), 10)
    assert t.resposta == "Escrevi."
    from eva.config import WORKSPACE
    assert (WORKSPACE / "nota.txt").read_text() == "oi"
    assert any(e["tipo"] == "aprovacao" and e.get("status") == "permitido" for e in t.eventos)


async def test_permissao_perguntar_negada(fake, eventos):
    fake.adicionar({"tool_calls": [{"nome": "executar_comando", "args": {"comando": "echo perigoso > x.txt"}}]},
                   {"texto": "Tudo bem, não rodei."})
    t = await ger.enviar("roda um comando")
    assert await _aprovar_quando_pedir(eventos, False)
    await asyncio.wait_for(t.fim.wait(), 10)
    res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert res.startswith("NEGADO")
    from eva.config import WORKSPACE
    assert not (WORKSPACE / "x.txt").exists()


async def test_permissao_bloquear(fake, rodar_tarefa):
    permissions.set("buscar_web", "bloquear")
    fake.adicionar({"tool_calls": [{"nome": "buscar_web", "args": {"consulta": "x"}}]}, {"texto": "Bloqueado, entendi."})
    t = await rodar_tarefa("pesquisa x")
    res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert res.startswith("BLOQUEADO")
    assert t.resposta == "Bloqueado, entendi."
    assert audit.pagina(1, 10, "acao_bloqueada")["total"] == 1


async def test_aprovacao_expira_vira_negado(fake, eventos, monkeypatch):
    monkeypatch.setattr(agent_mod, "APROVACAO_TIMEOUT", 0.2)
    fake.adicionar({"tool_calls": [{"nome": "esquecer", "args": {"id": 1}}]}, {"texto": "Ok, sem resposta."})
    t = await ger.enviar("esquece")
    await asyncio.wait_for(t.fim.wait(), 10)
    res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert res.startswith("NEGADO")
    assert any(e["tipo"] == "aprovacao" and e.get("status") == "expirada" for e in eventos)


async def test_politica_api_nega_automaticamente(fake, rodar_tarefa):
    fake.adicionar({"tool_calls": [{"nome": "executar_comando", "args": {"comando": "ls"}}]}, {"texto": "negado ok"})
    t = await rodar_tarefa("ls", politica="negar")
    assert t.resposta == "negado ok"
    assert not any(e["tipo"] == "aprovacao" for e in t.eventos)


async def test_sensivel_escala_para_perguntar(fake, eventos):
    async def sensivel(args):
        return "botão de compra" if "comprar" in args.get("alvo", "") else None

    @tool("clicar_teste", "teste", {"alvo": {"type": "string"}}, sensivel=sensivel)
    async def clicar_teste(alvo):
        return f"cliquei {alvo}"
    permissions.overrides["clicar_teste"] = "permitir"
    try:
        fake.adicionar({"tool_calls": [{"nome": "clicar_teste", "args": {"alvo": "menu"}}]},
                       {"tool_calls": [{"nome": "clicar_teste", "args": {"alvo": "Finalizar e comprar"}}]},
                       {"texto": "fim"})
        t = await ger.enviar("clica")
        assert await _aprovar_quando_pedir(eventos, False)
        await asyncio.wait_for(t.fim.wait(), 10)
        aps = [e for e in t.eventos if e["tipo"] == "aprovacao" and "ferramenta" in e]
        assert len(aps) == 1 and aps[0]["motivo"] == "botão de compra"
        res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"]
        assert res[0] == "cliquei menu" and res[1].startswith("NEGADO")
    finally:
        TOOLS.pop("clicar_teste", None)


# ------------------------------------------------------------------ killswitch / parar / fila

async def _esperar(cond, timeout=5):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condição não ocorreu")


async def test_killswitch_cancela_tarefa_em_andamento(fake, eventos):
    @tool("ferramenta_lenta", "teste", {})
    async def ferramenta_lenta():
        await asyncio.sleep(30)
        return "nunca"
    permissions.overrides["ferramenta_lenta"] = "permitir"
    try:
        fake.adicionar({"tool_calls": [{"nome": "ferramenta_lenta", "args": {}}]})
        t = await ger.enviar("faz algo demorado")
        t2 = await ger.enviar("segunda tarefa na fila")
        await _esperar(lambda: any(e["tipo"] == "acao" for e in eventos))
        n = await agent_mod.desligar_tudo("teste")
        assert n == 2
        await asyncio.wait_for(t.fim.wait(), 3)
        assert t.status == "cancelada" and t2.status == "cancelada"
        assert kill_switch.off
        with pytest.raises(RuntimeError):
            await ger.enviar("não deveria entrar")
        agent_mod.ligar()
        assert not kill_switch.off
        fake.adicionar({"texto": "voltei"})
        t3 = await ger.enviar("oi de novo")
        await asyncio.wait_for(t3.fim.wait(), 5)
        assert t3.resposta == "voltei"
    finally:
        TOOLS.pop("ferramenta_lenta", None)


async def test_parar_cancela_na_hora(fake, eventos):
    fake.adicionar({"texto": "lento", "dormir": 5})
    t = await ger.enviar("algo")
    await _esperar(lambda: any(e["tipo"] == "pensando" for e in eventos))
    assert ger.parar()
    await asyncio.wait_for(t.fim.wait(), 2)
    assert t.status == "cancelada"
    assert any(e["tipo"] == "fim" and e["status"] == "cancelada" for e in eventos)


async def test_fila_uma_por_vez(fake, eventos):
    fake.adicionar({"texto": "primeira", "dormir": 0.3}, {"texto": "segunda"})
    t1 = await ger.enviar("1")
    t2 = await ger.enviar("2")
    await asyncio.sleep(0.05)
    assert ger.atual is t1 and t2.status == "na_fila"
    await asyncio.wait_for(t2.fim.wait(), 5)
    assert (t1.resposta, t2.resposta) == ("primeira", "segunda")
    ordem = [e.get("texto") for e in eventos if e["tipo"] == "resposta"]
    assert ordem == ["primeira", "segunda"]


async def test_limite_de_passos_resume_e_pergunta(fake, rodar_tarefa, monkeypatch):
    monkeypatch.setattr(agent_mod, "MAX_STEPS", 3)
    fake.adicionar(*[{"tool_calls": [{"nome": "hora_atual", "args": {}}]}] * 3,
                   {"texto": "Fiz 3 consultas de hora. Falta X. Continuo?"})
    t = await rodar_tarefa("tarefa infinita")
    assert t.resposta == "Fiz 3 consultas de hora. Falta X. Continuo?"
    ult = fake.requisicoes[-1]
    assert ult.get("tool_choice") == "none"
    assert "limite de 3 passos" in ult["messages"][-1]["content"]


async def test_resposta_vazia_tenta_de_novo(fake, rodar_tarefa):
    fake.adicionar({"texto": ""}, {"texto": "Agora sim."})
    t = await rodar_tarefa("oi")
    assert t.resposta == "Agora sim."


async def test_perguntar_ao_gabriel(fake, eventos):
    fake.adicionar({"tool_calls": [{"nome": "perguntar_ao_gabriel",
                                    "args": {"pergunta": "Pizza ou sushi?", "opcoes": ["pizza", "sushi"]}}]},
                   {"texto": "Sushi então!"})
    t = await ger.enviar("escolhe o jantar")
    for _ in range(200):
        pq = [e for e in eventos if e["tipo"] == "pergunta" and "pergunta" in e]
        if pq:
            break
        await asyncio.sleep(0.02)
    assert pq[0]["pergunta"] == "Pizza ou sushi?" and pq[0]["opcoes"] == ["pizza", "sushi"]
    assert await ger.responder_pergunta(pq[0]["id"], "sushi")
    await asyncio.wait_for(t.fim.wait(), 5)
    res = [m["content"] for m in fake.requisicoes[-1]["messages"] if m["role"] == "tool"][0]
    assert "sushi" in res
    assert t.resposta == "Sushi então!"


async def test_auditoria_registra_tudo(fake, rodar_tarefa):
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}}]}, {"texto": "ok"})
    await rodar_tarefa("hora")
    tipos = audit.tipos()
    for t in ("tarefa_recebida", "mensagem_usuario", "llm_chamada", "acao_solicitada", "acao_resultado", "resposta_eva"):
        assert t in tipos, t
    chamada = audit.pagina(1, 5, "llm_chamada")["itens"][0]
    assert chamada["modelo"] == "gemini-3.8-flash" and chamada["uso"]["total_tokens"] == 15


async def test_erro_llm_vira_mensagem_amigavel_no_chat(fake, eventos, rodar_tarefa):
    fake.adicionar({"erro": 401, "msg": "API key invalid"})
    t = await rodar_tarefa("oi")
    erro = [e for e in eventos if e["tipo"] == "erro"][0]
    assert "chave" in erro["texto"].lower() and "401" not in erro["texto"]
    assert t.status == "concluida"
    assert json.dumps(erro).find("Error code") == -1
