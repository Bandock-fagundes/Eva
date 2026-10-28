"""Camada de LLM — o 'cérebro' da Eva conversando com qualquer API compatível com OpenAI (padrão: Gemini).

Responsabilidades:
- Cadeia de modelos com fallback: se um modelo der 404/"não existe", marca como indisponível e tenta o próximo.
- Retry com backoff exponencial + jitter em 429/5xx/timeout/conexão, respeitando retry-after.
  429 persistente -> cai para um modelo mais leve (cada modelo grátis tem cota própria).
- Gemini 3: a mensagem do assistente com tool_calls é devolvida EXATAMENTE como veio
  (inclui extra_content.google.thought_signature em cada tool_call), senão a API recusa o próximo passo.
- Streaming de texto (com montagem correta de tool_calls + extra_content) e queda para não-streaming.
- Erros finais viram uma mensagem amigável em PT-BR (LLMErro.amigavel); o detalhe técnico vai para o log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import config

log = logging.getLogger("eva.llm")

MODELOS_PADRAO = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
EMBED_PADRAO = ["gemini-embedding-001", "text-embedding-004"]
EMBED_DIMENSOES = 768  # gemini-embedding-001 é Matryoshka: dá pra cortar o vetor e renormalizar

INDISPONIVEL_POR = 3600.0   # segundos que um modelo "não encontrado" fica fora da cadeia
COOLDOWN_LIMITE = 60.0      # segundos que um modelo com 429 persistente fica de molho

AMIGAVEL = {
    "sem_chave": "Falta configurar a chave da API do cérebro (EVA_LLM_API_KEY). Sem ela eu não consigo pensar.",
    "auth": "A chave da API do cérebro foi recusada (inválida ou sem permissão). Confere a EVA_LLM_API_KEY.",
    "limite": "Não consegui falar com o cérebro agora (limite do plano grátis). Tenta em 1 minuto.",
    "modelo": "Nenhum modelo de IA da minha lista está disponível pra essa chave agora. "
              "Confere o EVA_LLM_MODEL (ou a chave) — já registrei os detalhes no log.",
    "transitorio": "O servidor do cérebro está instável agora. Tenta de novo em alguns segundos.",
    "timeout": "O cérebro demorou demais pra responder. Tenta de novo (se puder, com um pedido menor).",
    "conexao": "Não consegui me conectar ao cérebro (problema de rede no servidor). Tenta daqui a pouco.",
    "requisicao": "O cérebro recusou esse pedido por um problema técnico. Já registrei no log; "
                  "tenta reformular ou me avisa.",
    "desconhecido": "Deu um problema inesperado falando com o cérebro. Já registrei no log; tenta de novo.",
}


class LLMErro(Exception):
    """Erro final da camada de LLM, com texto amigável (PT-BR) e detalhe técnico separado."""

    def __init__(self, tipo: str, tecnico: str = ""):
        self.tipo = tipo if tipo in AMIGAVEL else "desconhecido"
        self.amigavel = AMIGAVEL[self.tipo]
        self.tecnico = tecnico
        super().__init__(self.amigavel)


@dataclass
class Resposta:
    mensagem: dict                      # mensagem do assistente pronta para reenviar (com campos extras)
    texto: str
    tool_calls: list[dict]
    modelo: str
    uso: dict | None = None
    finish_reason: str | None = None
    duracao: float = 0.0
    tentativas: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- utilidades

def _status(e: Exception) -> int | None:
    return getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)


def _corpo(e: Exception) -> str:
    partes = [str(e)]
    body = getattr(e, "body", None)
    if body is not None:
        try:
            partes.append(json.dumps(body, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            partes.append(str(body))
    return " ".join(partes)[:2000]


_RE_MODELO = re.compile(
    r"(model|modelo)[^\n]{0,120}?(not found|no longer|not available|unavailable|not supported|does not exist|"
    r"unknown|deprecated|is not found)|"
    r"(not found|no longer available|unknown model|invalid model)[^\n]{0,60}?model", re.I)
_RE_CHAVE = re.compile(r"api[ _-]?key|API_KEY_INVALID|permission|unauthenticated|PERMISSION_DENIED", re.I)


def classificar(e: Exception) -> str:
    """Devolve: modelo | auth | limite | transitorio | timeout | conexao | requisicao | desconhecido."""
    import openai

    if isinstance(e, _RespostaVazia):
        return "transitorio"
    if isinstance(e, openai.APITimeoutError) or isinstance(e, asyncio.TimeoutError):
        return "timeout"
    if isinstance(e, openai.APIConnectionError):
        return "conexao"
    st = _status(e)
    texto = _corpo(e)
    if st == 404:
        return "modelo"
    if st in (401, 403):
        return "auth"
    if st == 429:
        return "limite"
    if st in (500, 502, 503, 504) or (st is not None and st >= 500):
        return "transitorio"
    if st in (400, 422):
        if _RE_MODELO.search(texto):
            return "modelo"
        if _RE_CHAVE.search(texto) and "thought" not in texto.lower():
            return "auth"
        return "requisicao"
    if isinstance(e, openai.APIError):
        return "transitorio"
    return "desconhecido"


def _retry_after(e: Exception) -> float | None:
    """Lê retry-after do cabeçalho ou o 'retryDelay' do corpo do Gemini (ex.: "17s")."""
    resp = getattr(e, "response", None)
    try:
        h = resp.headers.get("retry-after") if resp is not None else None
        if h:
            return max(0.0, float(h))
    except (TypeError, ValueError):
        pass
    m = re.search(r'retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', _corpo(e))
    if m:
        return float(m.group(1))
    return None


def _mesclar(dst: dict, src: dict):
    """Mescla dicionários em profundidade (para campos extras vindos em pedaços no streaming)."""
    for k, v in (src or {}).items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _mesclar(dst[k], v)
        else:
            dst[k] = v


def _sem_none(v):
    if isinstance(v, dict):
        return {k: _sem_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_sem_none(x) for x in v]
    return v


ASSINATURA_CONTINGENCIA = "skip_thought_signature_validator"


def reparar_assinaturas(mensagens: list[dict]) -> list[dict]:
    """Em cada passo com tool_calls, se a 1ª chamada não tem assinatura, recebe a de contingência.
    Altera as mensagens no lugar (o agente reaproveita a lista nos próximos passos)."""
    for m in mensagens:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            primeira = m["tool_calls"][0]
            g = ((primeira.get("extra_content") or {}).get("google") or {})
            if not g.get("thought_signature"):
                primeira["extra_content"] = {"google": {"thought_signature": ASSINATURA_CONTINGENCIA}}
    return mensagens


def normalizar_vetor(v: list[float], dims: int = EMBED_DIMENSOES) -> list[float]:
    v = list(v[:dims])
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 5) for x in v]


# ---------------------------------------------------------------- cliente

class LLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, modelo: str | None = None,
                 fallbacks: list[str] | None = None, timeout: float | None = None,
                 tentativas: int | None = None, backoff: float | None = None):
        self.configurar(base_url=base_url, api_key=api_key, modelo=modelo, fallbacks=fallbacks,
                        timeout=timeout, tentativas=tentativas, backoff=backoff)

    def configurar(self, base_url=None, api_key=None, modelo=None, fallbacks=None, timeout=None,
                   tentativas=None, backoff=None, streaming=None, prazo_total=None, embed_modelo=None):
        self.base_url = base_url or config.LLM_BASE_URL
        self.api_key = config.LLM_API_KEY if api_key is None else api_key
        self.modelo_preferido = modelo or config.LLM_MODEL
        extras = config.LLM_FALLBACKS if fallbacks is None else fallbacks
        self.timeout = timeout or config.LLM_TIMEOUT
        self.tentativas = max(1, tentativas or config.LLM_TENTATIVAS)
        self.backoff = config.LLM_BACKOFF if backoff is None else backoff
        self.streaming = config.LLM_STREAMING if streaming is None else streaming
        self.prazo_total = prazo_total or config.LLM_PRAZO_TOTAL
        self.cadeia = list(dict.fromkeys([self.modelo_preferido, *extras, *MODELOS_PADRAO]))
        self.embed_cadeia = list(dict.fromkeys([embed_modelo or config.EMBED_MODEL, *EMBED_PADRAO]))
        self.indisponiveis: dict[str, tuple[float, str]] = {}   # modelo -> (até quando, motivo)
        self.cooldown: dict[str, float] = {}
        self.embed_indisponiveis: set[str] = set()
        self.embed_pausa_ate = 0.0
        self.modelos_listados: list[str] | None = None
        self.modelo_em_uso: str | None = None
        self.embed_modelo_em_uso: str | None = None
        self.ultimo_erro: dict | None = None
        self.falhas_stream = 0
        self._clientes: dict[int, Any] = {}
        self._sleep = asyncio.sleep

    # um cliente HTTP por event loop (evita 'Event loop is closed' em testes/reinícios)
    def _cliente(self):
        from openai import AsyncOpenAI
        import weakref
        loop = asyncio.get_running_loop()
        chave = id(loop)
        item = self._clientes.get(chave)
        if item is None or item[0]() is not loop:  # id de loop pode ser reaproveitado depois que um loop morre
            if len(self._clientes) > 8:
                self._clientes.clear()
            c = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key or "sem-chave",
                            timeout=self.timeout, max_retries=0)
            item = (weakref.ref(loop), c)
            self._clientes[chave] = item
        return item[1]

    @property
    def configurado(self) -> bool:
        return bool(self.api_key)

    # ---------- estado da cadeia ----------
    def _disponivel(self, m: str) -> bool:
        ate = self.indisponiveis.get(m)
        if ate and ate[0] > time.time():
            return False
        if ate:
            self.indisponiveis.pop(m, None)
        return True

    def candidatos(self, leve: bool = False) -> list[str]:
        cadeia = list(self.cadeia)
        if leve:  # tarefas de fundo baratas: começa pelos modelos "lite" (cota separada)
            cadeia = [m for m in cadeia if "lite" in m] + [m for m in cadeia if "lite" not in m]
        vivos = [m for m in cadeia if self._disponivel(m)]
        if not vivos:  # tudo marcado como indisponível: zera e tenta de novo (pode ter sido engano)
            self.indisponiveis.clear()
            vivos = cadeia
        agora = time.time()
        livres = [m for m in vivos if self.cooldown.get(m, 0) <= agora]
        de_molho = sorted((m for m in vivos if m not in livres), key=lambda m: self.cooldown.get(m, 0))
        return livres + de_molho

    def marcar_indisponivel(self, modelo: str, motivo: str):
        self.indisponiveis[modelo] = (time.time() + INDISPONIVEL_POR, motivo[:300])
        log.warning("Modelo %s indisponível: %s", modelo, motivo[:200])

    def estado(self) -> dict:
        agora = time.time()
        return {
            "modelo_em_uso": self.modelo_em_uso or (self.candidatos()[0] if self.cadeia else None),
            "cadeia": [
                {"modelo": m,
                 "status": ("indisponivel" if m in self.indisponiveis and self.indisponiveis[m][0] > agora
                            else "limite" if self.cooldown.get(m, 0) > agora else "ok")}
                for m in self.cadeia
            ],
            "streaming": self.streaming and self.falhas_stream < 3,
            "embeddings": not (len(self.embed_indisponiveis) >= len(self.embed_cadeia)),
        }

    # ---------- descoberta de modelos ----------
    async def descobrir_modelos(self) -> list[str] | None:
        """GET /models: marca como indisponíveis os modelos da cadeia que a chave não enxerga."""
        if not self.configurado:
            return None
        try:
            pagina = await asyncio.wait_for(self._cliente().models.list(), timeout=20)
            ids = [str(m.id).removeprefix("models/") for m in (pagina.data or [])]
        except Exception as e:  # noqa: BLE001
            log.info("Não deu pra listar modelos (%s) — sigo com a cadeia padrão.", type(e).__name__)
            return None
        self.modelos_listados = ids
        conhecidos = [m for m in self.cadeia if m in ids]
        if conhecidos:  # só confia na lista se ela conhece pelo menos um modelo da cadeia
            for m in self.cadeia:
                if m not in ids:
                    self.marcar_indisponivel(m, "não aparece em /models")
        emb = [m for m in self.embed_cadeia if m in ids]
        if emb:
            self.embed_cadeia = emb + [m for m in self.embed_cadeia if m not in emb]
        return ids

    # ---------- chamada principal ----------
    async def chat(self, mensagens: list[dict], ferramentas: list[dict] | None = None, *,
                   ao_token: Callable[[str], Awaitable[None]] | None = None,
                   ao_reiniciar: Callable[[], Awaitable[None]] | None = None,
                   leve: bool = False, tool_choice: str | None = None,
                   max_tokens: int | None = None, temperatura: float | None = None) -> Resposta:
        if not self.configurado:
            raise LLMErro("sem_chave", "EVA_LLM_API_KEY vazia")
        inicio = time.monotonic()
        prazo = inicio + self.prazo_total
        tentativas_log: list[dict] = []
        ultimo_tipo, ultimo_tecnico = "desconhecido", ""
        assinatura_reparada = False
        for modelo in self.candidatos(leve):
            n429 = 0
            for tentativa in range(self.tentativas):
                if time.monotonic() > prazo:
                    break
                try:
                    r = await self._uma_chamada(modelo, mensagens, ferramentas, ao_token, ao_reiniciar,
                                                tool_choice, max_tokens, temperatura)
                    r.duracao = time.monotonic() - inicio
                    r.tentativas = tentativas_log
                    if not leve or not self.modelo_em_uso:
                        self.modelo_em_uso = modelo
                    self.cooldown.pop(modelo, None)
                    return r
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    tipo = classificar(e)
                    tecnico = f"{type(e).__name__}: {_corpo(e)}"
                    tentativas_log.append({"modelo": modelo, "tentativa": tentativa + 1, "tipo": tipo,
                                           "erro": tecnico[:500]})
                    ultimo_tipo, ultimo_tecnico = tipo, tecnico
                    self.ultimo_erro = {"tipo": tipo, "modelo": modelo, "erro": tecnico[:500], "quando": time.time()}
                    log.warning("LLM %s tentativa %d falhou [%s]: %s", modelo, tentativa + 1, tipo, tecnico[:300])
                    if tipo == "modelo":
                        self.marcar_indisponivel(modelo, tecnico)
                        break
                    if tipo == "requisicao" and "thought" in tecnico.lower() and not assinatura_reparada:
                        # rede de segurança (Gemini 3): completa assinaturas faltando com o valor que a
                        # documentação do Google indica para pular a validação, e tenta de novo uma vez
                        mensagens = reparar_assinaturas(mensagens)
                        assinatura_reparada = True
                        log.warning("Gemini recusou por thought_signature; reenviando com assinatura de contingência.")
                        continue
                    if tipo in ("auth", "requisicao", "desconhecido"):
                        raise LLMErro(tipo, tecnico) from e
                    if tipo == "limite":
                        n429 += 1
                        espera = _retry_after(e)
                        if espera is None:
                            espera = self._espera(tentativa)
                        if n429 >= 2 or espera > 20 or tentativa == self.tentativas - 1:
                            self.cooldown[modelo] = time.time() + max(COOLDOWN_LIMITE, espera or 0)
                            break  # vai para o próximo modelo (mais leve)
                        await self._dormir(espera, prazo)
                        continue
                    if tipo == "timeout" and tentativa >= 1:
                        break  # timeout repetido: tenta outro modelo em vez de esperar mais 90s
                    if tentativa < self.tentativas - 1:
                        await self._dormir(self._espera(tentativa), prazo)
        raise LLMErro(ultimo_tipo, ultimo_tecnico or "nenhum modelo respondeu")

    def _espera(self, tentativa: int) -> float:
        base = self.backoff * (2 ** tentativa)
        return min(30.0, base + random.uniform(0, base * 0.5 + 0.001))

    async def _dormir(self, s: float, prazo: float):
        s = max(0.0, min(s, prazo - time.monotonic()))
        if s > 0:
            await self._sleep(s)

    async def _uma_chamada(self, modelo, mensagens, ferramentas, ao_token, ao_reiniciar, tool_choice,
                           max_tokens, temperatura) -> Resposta:
        import openai

        kwargs: dict[str, Any] = {"model": modelo, "messages": mensagens}
        if ferramentas:
            kwargs["tools"] = ferramentas
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if temperatura is not None:  # Gemini 3: o Google recomenda manter a temperatura padrão (1.0)
            kwargs["temperature"] = temperatura
        if config.LLM_RACIOCINIO:
            kwargs["reasoning_effort"] = config.LLM_RACIOCINIO

        if ao_token and self.streaming and self.falhas_stream < 3:
            emitiu = False

            async def _tok(t: str):
                nonlocal emitiu
                emitiu = True
                await ao_token(t)

            try:
                return await self._chamada_stream(kwargs, _tok)
            except asyncio.CancelledError:
                raise
            except (openai.APIStatusError, openai.APIConnectionError):
                # 404/429/5xx/rede: a lógica de retry/fallback decide; o texto parcial é descartado na tela
                if emitiu and ao_reiniciar:
                    await ao_reiniciar()
                raise
            except Exception as e:  # noqa: BLE001 — streaming quebrou no meio: cai para não-streaming
                self.falhas_stream += 1
                log.warning("Streaming falhou (%s: %s); tentando sem streaming.", type(e).__name__, str(e)[:200])
                if emitiu and ao_reiniciar:
                    await ao_reiniciar()
                r = await self._chamada_simples(kwargs)
                if r.texto:
                    await ao_token(r.texto)
                return r
        return await self._chamada_simples(kwargs)

    async def _chamada_simples(self, kwargs) -> Resposta:
        resp = await self._cliente().chat.completions.create(**kwargs)
        if not getattr(resp, "choices", None):
            raise _RespostaVazia("resposta sem 'choices'")
        ch = resp.choices[0]
        msg = ch.message
        # model_dump(exclude_none) preserva campos extras (ex.: extra_content.google.thought_signature)
        d = msg.model_dump(exclude_none=True)
        d["role"] = "assistant"
        tool_calls = d.get("tool_calls") or []
        if tool_calls:
            d.setdefault("content", None)
            for tc in tool_calls:
                tc.setdefault("type", "function")
                if not tc.get("id"):
                    tc["id"] = "call_" + uuid.uuid4().hex[:12]
        else:
            d.pop("tool_calls", None)
            d.setdefault("content", "")
        uso = resp.usage.model_dump(exclude_none=True) if getattr(resp, "usage", None) else None
        return Resposta(mensagem=d, texto=msg.content or "", tool_calls=tool_calls,
                        modelo=kwargs["model"], uso=uso, finish_reason=ch.finish_reason)

    async def _chamada_stream(self, kwargs, ao_token) -> Resposta:
        stream = await self._cliente().chat.completions.create(**kwargs, stream=True)
        textos: list[str] = []
        calls: dict[Any, dict] = {}
        ordem: list[Any] = []
        extras_msg: dict = {}
        uso, finish = None, None
        ultima_chave = None
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                uso = chunk.usage.model_dump(exclude_none=True)
            if not chunk.choices:
                continue
            ch = chunk.choices[0]
            delta = ch.delta
            if delta is not None:
                if delta.content:
                    textos.append(delta.content)
                    await ao_token(delta.content)
                if delta.model_extra:
                    _mesclar(extras_msg, _sem_none(delta.model_extra))
                for tc in delta.tool_calls or []:
                    if tc.index is not None:
                        chave = ("i", tc.index)
                    elif tc.id and ("id", tc.id) in calls:
                        chave = ("id", tc.id)
                    elif tc.id or ultima_chave is None:
                        chave = ("id", tc.id or uuid.uuid4().hex)
                    else:
                        chave = ultima_chave
                    ultima_chave = chave
                    if chave not in calls:
                        calls[chave] = {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
                        ordem.append(chave)
                    cur = calls[chave]
                    if tc.id:
                        cur["id"] = tc.id
                    if tc.type:
                        cur["type"] = tc.type
                    fn = tc.function
                    if fn is not None:
                        if fn.name:
                            if not cur["function"]["name"]:
                                cur["function"]["name"] = fn.name
                            elif fn.name != cur["function"]["name"]:
                                cur["function"]["name"] += fn.name
                        if fn.arguments:
                            cur["function"]["arguments"] += fn.arguments
                        if fn.model_extra:
                            _mesclar(cur["function"], _sem_none(fn.model_extra))
                    if tc.model_extra:
                        _mesclar(cur, _sem_none(tc.model_extra))  # extra_content / thought_signature
            if ch.finish_reason:
                finish = ch.finish_reason
        texto = "".join(textos)
        tool_calls = []
        for chave in ordem:
            c = calls[chave]
            if not c["id"]:
                c["id"] = "call_" + uuid.uuid4().hex[:12]
            if not c["function"]["arguments"]:
                c["function"]["arguments"] = "{}"
            tool_calls.append(c)
        assin = ((extras_msg.get("extra_content") or {}).get("google") or {}).get("thought_signature")
        if assin and tool_calls and "extra_content" not in tool_calls[0]:
            # se a assinatura vier no nível do delta, ela pertence à 1ª chamada de ferramenta
            tool_calls[0]["extra_content"] = {"google": {"thought_signature": assin}}
        msg: dict = {"role": "assistant", "content": texto if (texto or not tool_calls) else None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        for k, v in extras_msg.items():
            msg.setdefault(k, v)
        if not texto and not tool_calls and finish is None:
            raise _RespostaVazia("stream terminou sem conteúdo")
        return Resposta(mensagem=msg, texto=texto, tool_calls=tool_calls, modelo=kwargs["model"],
                        uso=uso, finish_reason=finish)

    # ---------- embeddings ----------
    async def embed(self, textos: list[str]) -> list[list[float]] | None:
        """Vetores normalizados (768 dims) ou None se nenhum modelo de embedding funcionar."""
        if not self.configurado or not textos or time.time() < self.embed_pausa_ate:
            return None
        for modelo in self.embed_cadeia:
            if modelo in self.embed_indisponiveis:
                continue
            try:
                resp = await asyncio.wait_for(
                    self._cliente().embeddings.create(model=modelo, input=[t[:6000] for t in textos]), timeout=30)
                vetores = [normalizar_vetor(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]
                if len(vetores) != len(textos):
                    raise ValueError("quantidade de vetores diferente da de textos")
                self.embed_modelo_em_uso = modelo
                return vetores
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                tipo = classificar(e)
                log.info("Embedding %s falhou [%s]: %s", modelo, tipo, str(e)[:200])
                if tipo in ("modelo", "requisicao"):
                    self.embed_indisponiveis.add(modelo)
                    continue
                self.embed_pausa_ate = time.time() + 120  # limite/instabilidade: usa palavras-chave por 2 min
                return None
        self.embed_pausa_ate = time.time() + 600
        return None

    def reset(self):
        """Limpa marcações (usado em testes e no botão 'ligar')."""
        self.indisponiveis.clear()
        self.cooldown.clear()
        self.embed_indisponiveis.clear()
        self.embed_pausa_ate = 0.0
        self.falhas_stream = 0
        self.modelo_em_uso = None
        self.ultimo_erro = None


class _RespostaVazia(Exception):
    pass


cerebro = LLM()
