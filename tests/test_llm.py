"""Camada de LLM: resposta, streaming, thought_signature, fallback de modelo, retry, erros amigáveis."""
import json

import pytest

from eva.llm import LLMErro, cerebro, classificar

MSGS = [{"role": "system", "content": "Você é um teste."}, {"role": "user", "content": "oi"}]


async def test_resposta_simples_sem_streaming(fake):
    fake.adicionar({"texto": "Oi, Gabriel!"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "Oi, Gabriel!"
    assert r.modelo == "gemini-3.8-flash"
    assert r.mensagem["role"] == "assistant" and r.mensagem["content"] == "Oi, Gabriel!"
    assert r.uso["total_tokens"] == 15
    assert not fake.requisicoes[-1].get("stream")


async def test_resposta_simples_com_streaming(fake):
    fake.adicionar({"texto": "Texto que vem em vários pedaços pelo streaming."})
    tokens = []

    async def tok(t):
        tokens.append(t)
    r = await cerebro.chat(MSGS, ao_token=tok)
    assert len(tokens) > 3
    assert "".join(tokens) == r.texto == "Texto que vem em vários pedaços pelo streaming."
    assert fake.requisicoes[-1]["stream"] is True
    assert r.uso and r.uso["total_tokens"] == 15


async def test_extra_content_preservado_sem_streaming(fake):
    fake.adicionar({"tool_calls": [{"nome": "hora_atual", "args": {}, "assinatura": "SIG-ABC"}]})
    r = await cerebro.chat(MSGS, [{"type": "function", "function": {"name": "hora_atual", "parameters": {}}}])
    tc = r.mensagem["tool_calls"][0]
    assert tc["extra_content"]["google"]["thought_signature"] == "SIG-ABC"
    assert r.mensagem["content"] is None
    assert "content" in r.mensagem  # content explícito (null) junto com tool_calls


async def test_streaming_monta_tool_calls_com_extra_content(fake):
    fake.adicionar({"texto": "Vou ver.", "tool_calls": [
        {"nome": "buscar_web", "args": {"consulta": "clima em Florianópolis hoje"}, "assinatura": "SIG-1"},
        {"nome": "hora_atual", "args": {}}]})
    tokens = []

    async def tok(t):
        tokens.append(t)
    r = await cerebro.chat(MSGS, [{"type": "function", "function": {"name": "x", "parameters": {}}}], ao_token=tok)
    assert r.texto == "Vou ver."
    assert len(r.tool_calls) == 2
    a, b = r.tool_calls
    assert a["function"]["name"] == "buscar_web"
    assert json.loads(a["function"]["arguments"]) == {"consulta": "clima em Florianópolis hoje"}
    assert a["extra_content"]["google"]["thought_signature"] == "SIG-1"
    assert b["function"]["name"] == "hora_atual" and "extra_content" not in b
    assert r.mensagem["tool_calls"] == r.tool_calls


async def test_streaming_tool_calls_sem_index(fake):
    """Algumas versões do endpoint do Gemini mandam tool_calls sem 'index' no streaming."""
    fake.adicionar({"sem_index": True, "tool_calls": [{"nome": "hora_atual", "args": {"a": 1}, "assinatura": "S"}]})

    async def tok(t):
        pass
    r = await cerebro.chat(MSGS, [{"type": "function", "function": {"name": "x", "parameters": {}}}], ao_token=tok)
    assert len(r.tool_calls) == 1
    assert json.loads(r.tool_calls[0]["function"]["arguments"]) == {"a": 1}
    assert r.tool_calls[0]["extra_content"]["google"]["thought_signature"] == "S"


async def test_streaming_quebrado_cai_para_sem_streaming(fake):
    fake.adicionar({"texto": "Resposta completa depois da queda.", "stream_quebrado": True},
                   {"texto": "Resposta completa depois da queda."})
    eventos = []

    async def tok(t):
        eventos.append(("tok", t))

    async def reset():
        eventos.append(("reset", None))
    r = await cerebro.chat(MSGS, ao_token=tok, ao_reiniciar=reset)
    assert r.texto == "Resposta completa depois da queda."
    assert ("reset", None) in eventos
    # depois do reset, o texto completo é emitido de uma vez
    assert eventos[-1] == ("tok", "Resposta completa depois da queda.")
    assert fake.requisicoes[-1].get("stream") in (None, False)


async def test_fallback_de_modelo_em_404(fake):
    fake.modelos_disponiveis = {"gemini-3.5-flash-lite"}
    fake.adicionar({"texto": "Funcionei no lite."})
    cerebro.configurar(modelo="gemini-2.5-flash", fallbacks=[], backoff=0.01)
    r = await cerebro.chat(MSGS)
    assert r.texto == "Funcionei no lite."
    assert r.modelo == "gemini-3.5-flash-lite"
    tentados = [q["model"] for q in fake.requisicoes]
    assert tentados == ["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    est = cerebro.estado()
    assert est["modelo_em_uso"] == "gemini-3.5-flash-lite"
    assert {c["modelo"]: c["status"] for c in est["cadeia"]}["gemini-2.5-flash"] == "indisponivel"
    # próxima chamada já vai direto no modelo bom (o 404 ficou memorizado)
    fake.requisicoes.clear()
    fake.adicionar({"texto": "de novo"})
    await cerebro.chat(MSGS)
    assert [q["model"] for q in fake.requisicoes] == ["gemini-3.5-flash-lite"]


async def test_fallback_em_400_modelo_nao_encontrado(fake):
    fake.adicionar({"erro": 400, "msg": "The model gemini-3.8-flash is no longer available to new users."},
                   {"texto": "ok no segundo"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "ok no segundo" and r.modelo == "gemini-3.6-flash"


async def test_retry_em_429_respeita_retry_after(fake):
    esperas = []

    async def dormir(s):
        esperas.append(s)
    cerebro._sleep = dormir
    fake.adicionar({"erro": 429, "retry_after": 2, "msg": "Resource exhausted"}, {"texto": "passou"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "passou" and r.modelo == "gemini-3.8-flash"
    assert esperas == [2.0]


async def test_retry_em_503_com_backoff(fake):
    esperas = []

    async def dormir(s):
        esperas.append(s)
    cerebro._sleep = dormir
    fake.adicionar({"erro": 503, "msg": "The model is overloaded"}, {"erro": 500}, {"texto": "voltou"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "voltou" and r.modelo == "gemini-3.8-flash"
    assert len(esperas) == 2 and esperas[1] > esperas[0] * 1.2  # exponencial


async def test_429_persistente_cai_para_modelo_mais_leve(fake):
    async def dormir(s):
        pass
    cerebro._sleep = dormir
    fake.adicionar({"erro": 429, "retry_after": 1}, {"erro": 429, "retry_after": 1}, {"texto": "no próximo modelo"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "no próximo modelo"
    assert r.modelo == "gemini-3.6-flash"
    assert {c["modelo"]: c["status"] for c in cerebro.estado()["cadeia"]}["gemini-3.8-flash"] == "limite"


async def test_429_com_espera_longa_pula_direto(fake):
    fake.adicionar({"erro": 429, "msg": 'Quota exceeded. "retryDelay": "45s"'}, {"texto": "outro modelo"})
    r = await cerebro.chat(MSGS)
    assert r.modelo == "gemini-3.6-flash"


async def test_erro_amigavel_quando_tudo_falha(fake):
    async def dormir(s):
        pass
    cerebro._sleep = dormir
    fake.adicionar(*[{"erro": 429, "retry_after": 1}] * 20)
    with pytest.raises(LLMErro) as e:
        await cerebro.chat(MSGS)
    assert e.value.tipo == "limite"
    assert "limite do plano grátis" in e.value.amigavel
    assert "Error code" not in e.value.amigavel and "429" not in e.value.amigavel
    assert "429" in e.value.tecnico  # detalhe técnico guardado para o log


async def test_erro_chave_invalida(fake):
    fake.adicionar({"erro": 400, "msg": "API key not valid. Please pass a valid API key."})
    with pytest.raises(LLMErro) as e:
        await cerebro.chat(MSGS)
    assert e.value.tipo == "auth"
    assert "chave" in e.value.amigavel.lower()


async def test_todos_modelos_404_mensagem_amigavel_e_recupera(fake):
    fake.modelos_disponiveis = set()
    with pytest.raises(LLMErro) as e:
        await cerebro.chat(MSGS)
    assert e.value.tipo == "modelo"
    assert "Error code" not in e.value.amigavel
    # se os modelos voltarem, a cadeia se recupera (não fica tudo marcado pra sempre)
    fake.modelos_disponiveis = None
    fake.adicionar({"texto": "voltei"})
    r = await cerebro.chat(MSGS)
    assert r.texto == "voltei"


async def test_sem_chave():
    cerebro.configurar(api_key="")
    with pytest.raises(LLMErro) as e:
        await cerebro.chat(MSGS)
    assert e.value.tipo == "sem_chave"


async def test_erro_de_conexao_amigavel(fake):
    async def dormir(s):
        pass
    cerebro.configurar(base_url="http://127.0.0.1:1/", backoff=0.01, tentativas=2)
    cerebro._sleep = dormir
    with pytest.raises(LLMErro) as e:
        await cerebro.chat(MSGS)
    assert e.value.tipo == "conexao"
    assert "conectar" in e.value.amigavel


async def test_descobrir_modelos_marca_ausentes(fake):
    fake.modelos_listados = ["gemini-3.6-flash", "gemini-3.1-flash-lite", "gemini-embedding-001"]
    ids = await cerebro.descobrir_modelos()
    assert "gemini-3.6-flash" in ids
    assert cerebro.candidatos()[0] == "gemini-3.6-flash"
    fake.adicionar({"texto": "ok"})
    r = await cerebro.chat(MSGS)
    assert r.modelo == "gemini-3.6-flash"
    assert [q["model"] for q in fake.requisicoes] == ["gemini-3.6-flash"]


async def test_leve_prefere_modelos_lite():
    assert cerebro.candidatos(leve=True)[0].endswith("lite")


async def test_embeddings_e_fallback(fake):
    v = await cerebro.embed(["o time do Gabriel é o Avaí"])
    assert v and len(v[0]) == 64
    fake.embeddings_modo = "404"
    cerebro.reset()
    assert await cerebro.embed(["qualquer"]) is None


def test_classificar_erros_basicos():
    import httpx
    import openai
    req = httpx.Request("POST", "http://x")

    def erro(st, msg):
        resp = httpx.Response(st, request=req, json={"error": {"message": msg}})
        return openai.APIStatusError(msg, response=resp, body={"error": {"message": msg}})
    assert classificar(erro(404, "nope")) == "modelo"
    assert classificar(erro(429, "quota")) == "limite"
    assert classificar(erro(503, "overloaded")) == "transitorio"
    assert classificar(erro(400, "Function call is missing a thought_signature")) == "requisicao"
    assert classificar(erro(400, "models/gemini-2.5-flash is not found for API version")) == "modelo"
    assert classificar(erro(401, "bad key")) == "auth"


def test_schemas_compativeis_com_gemini():
    """Gemini recusa 'properties' vazio e 'required' vazio: ferramentas sem argumento vão sem 'parameters'."""
    from eva.tools import schemas
    for sc in schemas():
        f = sc["function"]
        if "parameters" in f:
            assert f["parameters"]["properties"], f["name"]
            assert f["parameters"].get("required", ["x"]), f["name"]
    nomes = {s["function"]["name"]: s["function"] for s in schemas()}
    assert "parameters" not in nomes["hora_atual"]
    assert "required" not in nomes["listar_arquivos"]["parameters"]


async def test_assinatura_no_delta_vai_para_a_primeira_chamada(fake, monkeypatch):
    """Se o streaming trouxer a assinatura no nível do delta, ela é movida para a 1ª tool_call."""
    from eva import llm as llm_mod
    import json as _j

    def gerar_chunks(modelo):
        base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": modelo}
        yield {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "extra_content": {"google": {"thought_signature": "DELTA-SIG"}},
                                                         "tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                                         "function": {"name": "hora_atual", "arguments": "{}"}}]},
                                    "finish_reason": "tool_calls"}]}

    class FakeStream:
        def __init__(self, modelo):
            from openai.types.chat import ChatCompletionChunk
            self.itens = [ChatCompletionChunk.model_validate(c) for c in gerar_chunks(modelo)]

        def __aiter__(self):
            async def it():
                for c in self.itens:
                    yield c
            return it()

    class FakeCompletions:
        async def create(self, **kw):
            return FakeStream(kw["model"])

    class FakeCliente:
        chat = type("C", (), {"completions": FakeCompletions()})()
    monkeypatch.setattr(cerebro, "_cliente", lambda: FakeCliente())

    async def tok(t):
        pass
    r = await cerebro.chat(MSGS, [{"type": "function", "function": {"name": "hora_atual"}}], ao_token=tok)
    assert r.tool_calls[0]["extra_content"]["google"]["thought_signature"] == "DELTA-SIG"
    assert _j.loads(r.tool_calls[0]["function"]["arguments"]) == {}
