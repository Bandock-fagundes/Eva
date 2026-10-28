"""Servidor FALSO compatível com OpenAI, imitando o endpoint OpenAI-compat do Gemini 3.

- Responde de acordo com um roteiro (fila de respostas) definido pelo teste.
- Como o Gemini 3, exige que cada tool_call devolvida no histórico traga o MESMO
  extra_content.google.thought_signature que ele mandou — senão responde 400.
- Simula 404 de modelo inexistente, 429 com retry-after, 503, streaming (com tool_calls
  em pedaços), streaming quebrado no meio, /models e /embeddings.
- Chamadas de fundo (extrator de memória, resumidor) têm roteiros próprios, sem consumir a fila principal.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


def _vetor_falso(texto: str, dims: int = 64) -> list[float]:
    """Embedding determinístico: bag-of-words com hash (textos parecidos -> vetores parecidos)."""
    v = [0.0] * dims
    for w in re.findall(r"\w+", texto.lower()):
        if len(w) < 3:
            continue
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % dims] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Roteiro:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.respostas: list = []
        self.requisicoes: list[dict] = []        # chamadas do agente
        self.requisicoes_fundo: list[dict] = []  # extrator/resumidor
        self.modelos_disponiveis: set[str] | None = None  # None = todos
        self.modelos_listados = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                                 "gemini-3.1-flash-lite", "gemini-embedding-001"]
        self.assinaturas: dict[str, str] = {}
        self.extrator = lambda req: {"fatos": []}
        self.resumidor = lambda req: "RESUMO FALSO: conversaram sobre várias coisas."
        self.embeddings_modo = "ok"   # ok | 404 | 500
        self.embeddings_chamadas = 0
        self.padrao = "Resposta padrão do fake."
        self.atraso = 0.0
        self.aceitar_contingencia = True

    def adicionar(self, *itens):
        with self.lock:
            self.respostas.extend(itens)

    def proxima(self, req):
        with self.lock:
            item = self.respostas.pop(0) if self.respostas else {"texto": self.padrao}
        return item(req) if callable(item) else item


roteiro = Roteiro()


def _erro(status: int, msg: str, gstatus: str, headers=None):
    return JSONResponse({"error": {"code": status, "message": msg, "status": gstatus}}, status_code=status,
                        headers=headers or {})


def _validar_assinaturas(body: dict):
    for m in body.get("messages", []):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            esperado = roteiro.assinaturas.get(tc.get("id"))
            if esperado is None:
                continue
            veio = (((tc.get("extra_content") or {}).get("google") or {}).get("thought_signature"))
            if veio != esperado and not (roteiro.aceitar_contingencia and veio == "skip_thought_signature_validator"):
                return _erro(400, "Function call is missing a thought_signature in functionCall parts. This is "
                                  "required for tools to work correctly.", "INVALID_ARGUMENT")
    return None


def _montar_tool_calls(spec: dict) -> list[dict]:
    calls = []
    for i, c in enumerate(spec.get("tool_calls", [])):
        cid = c.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        tc = {"id": cid, "type": "function",
              "function": {"name": c["nome"], "arguments": c.get("args_brutos") or json.dumps(c.get("args", {}))}}
        # como o Gemini 3: só a PRIMEIRA chamada (de um passo) traz a assinatura, a menos que o teste diga outra coisa
        assinatura = c.get("assinatura", f"sig-{uuid.uuid4().hex[:10]}" if i == 0 else None)
        if assinatura:
            tc["extra_content"] = {"google": {"thought_signature": assinatura}}
            roteiro.assinaturas[cid] = assinatura
        calls.append(tc)
    return calls


def _chunk(modelo, delta, finish=None):
    return "data: " + json.dumps({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
                                  "model": modelo, "choices": [{"index": 0, "delta": delta,
                                                                "finish_reason": finish}]}) + "\n\n"


@app.get("/models")
async def modelos():
    return {"object": "list", "data": [{"id": f"models/{m}", "object": "model", "owned_by": "google"}
                                       for m in roteiro.modelos_listados]}


@app.post("/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    roteiro.embeddings_chamadas += 1
    if roteiro.embeddings_modo == "404":
        return _erro(404, f"models/{body.get('model')} is not found", "NOT_FOUND")
    if roteiro.embeddings_modo == "500":
        return _erro(500, "internal", "INTERNAL")
    entradas = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {"object": "list", "model": body["model"],
            "data": [{"object": "embedding", "index": i, "embedding": _vetor_falso(t)} for i, t in enumerate(entradas)],
            "usage": {"prompt_tokens": 1, "total_tokens": 1}}


@app.post("/chat/completions")
async def chat(request: Request):
    body = await request.json()
    modelo = body.get("model")
    sistema = next((m.get("content") or "" for m in body.get("messages", []) if m.get("role") == "system"), "")

    if roteiro.modelos_disponiveis is not None and modelo not in roteiro.modelos_disponiveis:
        roteiro.requisicoes.append({**body, "_rejeitado": 404})
        return _erro(404, f"models/{modelo} is not found for API version v1beta, or is not supported for "
                          "generateContent. Call ListModels to see the list of available models.", "NOT_FOUND")

    # chamadas de fundo
    if sistema.startswith("EXTRATOR_DE_MEMORIA"):
        roteiro.requisicoes_fundo.append(body)
        spec = {"texto": json.dumps(roteiro.extrator(body), ensure_ascii=False)}
    elif sistema.startswith("RESUMIDOR_DE_CONVERSA"):
        roteiro.requisicoes_fundo.append(body)
        spec = {"texto": roteiro.resumidor(body)}
    else:
        erro_assin = _validar_assinaturas(body)
        roteiro.requisicoes.append(body)
        if erro_assin is not None:
            return erro_assin
        spec = roteiro.proxima(body)

    if roteiro.atraso:
        import asyncio
        await asyncio.sleep(roteiro.atraso)
    if spec.get("dormir"):
        import asyncio
        await asyncio.sleep(spec["dormir"])

    if "erro" in spec:
        st = spec["erro"]
        headers = {"retry-after": str(spec["retry_after"])} if "retry_after" in spec else None
        nomes = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
                 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 503: "UNAVAILABLE"}
        return _erro(st, spec.get("msg", "erro simulado"), nomes.get(st, "ERROR"), headers)

    texto = spec.get("texto")
    tool_calls = _montar_tool_calls(spec) if spec.get("tool_calls") else []
    uso = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    if body.get("stream"):
        def gerar():
            yield _chunk(modelo, {"role": "assistant"})
            if texto:
                partes = [texto[i:i + 7] for i in range(0, len(texto), 7)]
                if spec.get("stream_quebrado"):
                    yield _chunk(modelo, {"content": partes[0]})
                    yield "data: {isso não é json\n\n"
                    return
                for p in partes:
                    yield _chunk(modelo, {"content": p})
            for idx, tc in enumerate(tool_calls):
                args = tc["function"]["arguments"]
                meio = len(args) // 2
                primeiro = {"id": tc["id"], "type": "function",
                            "function": {"name": tc["function"]["name"], "arguments": args[:meio]}}
                if not spec.get("sem_index"):
                    primeiro["index"] = idx
                if "extra_content" in tc:
                    primeiro["extra_content"] = tc["extra_content"]
                yield _chunk(modelo, {"tool_calls": [primeiro]})
                resto = {"function": {"arguments": args[meio:]}}
                if not spec.get("sem_index"):
                    resto["index"] = idx
                yield _chunk(modelo, {"tool_calls": [resto]})
            yield _chunk(modelo, {}, "tool_calls" if tool_calls else "stop")
            yield "data: " + json.dumps({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0,
                                         "model": modelo, "choices": [], "usage": uso}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gerar(), media_type="text/event-stream")

    msg = {"role": "assistant", "content": texto}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": modelo,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": uso}


def iniciar(porta: int):
    import uvicorn
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="error", lifespan="off"))
    th = threading.Thread(target=servidor.run, daemon=True)
    th.start()
    for _ in range(200):
        if servidor.started:
            return servidor
        time.sleep(0.05)
    raise RuntimeError("servidor falso não subiu")
