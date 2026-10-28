"""Loop do agente + gerenciador de tarefas.

- As tarefas rodam desacopladas do WebSocket: se a conexão cair, a tarefa continua; os eventos ficam
  guardados e quem reconectar recebe o estado completo (eventos, texto parcial, aprovações pendentes).
- Uma tarefa por vez (fila). Botão "parar" cancela na hora. Killswitch cancela tudo e bloqueia novas.
- Loop: pensa (LLM) -> ferramentas (com permissão) -> repete, até responder ou bater o limite de passos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import Callable

from . import audit, brain
from .config import APROVACAO_TIMEOUT, FERRAMENTA_LIMITE_CHARS, MAX_STEPS, PERGUNTA_TIMEOUT, USER_NAME
from .killswitch import kill_switch
from .llm import LLMErro, cerebro
from .memory import memory
from .permissions import permissions
from .tools import TOOLS, rotulo, schemas

log = logging.getLogger("eva.agente")

MAX_EVENTOS_TAREFA = 400


def truncar(texto: str, limite: int = FERRAMENTA_LIMITE_CHARS) -> str:
    """Corta textos longos mantendo começo e fim (onde costumam estar as partes úteis)."""
    texto = "" if texto is None else str(texto)
    if len(texto) <= limite:
        return texto
    cabeca = int(limite * 0.7)
    cauda = limite - cabeca - 80
    return (texto[:cabeca] + f"\n\n[… {len(texto) - cabeca - cauda} caracteres omitidos …]\n\n" + texto[-cauda:])


def _args_para_tela(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 600:
            v = v[:600] + "…"
        out[k] = v
    return out


class Tarefa:
    def __init__(self, texto: str, origem: str = "ws", politica: str = "perguntar"):
        self.id = uuid.uuid4().hex[:10]
        self.texto = texto
        self.origem = origem
        self.politica = politica  # perguntar | negar | permitir (aprovações)
        self.status = "na_fila"
        self.criada = time.time()
        self.eventos: list[dict] = []
        self.texto_parcial = ""
        self.resposta: str | None = None
        self.modelo: str | None = None
        self.pendentes: dict[str, dict] = {}
        self.task: asyncio.Task | None = None
        self.parar_pedido = False
        self.fim = asyncio.Event()
        self.uso = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def resumo(self) -> dict:
        return {
            "id": self.id, "texto": self.texto, "status": self.status, "origem": self.origem,
            "eventos": self.eventos[-MAX_EVENTOS_TAREFA:], "texto_parcial": self.texto_parcial,
            "pendentes": [p["evento"] for p in self.pendentes.values()], "resposta": self.resposta,
            "modelo": self.modelo,
        }


class Gerenciador:
    def __init__(self):
        self.reset()

    def reset(self):
        self.fila: deque[Tarefa] = deque()
        self.atual: Tarefa | None = None
        self.ultima: Tarefa | None = None
        self.ouvintes: set[Callable[[dict], None]] = set()
        self.fundo: set[asyncio.Task] = set()
        self._worker: asyncio.Task | None = None
        self._tem_tarefa: asyncio.Event | None = None
        self._loop = None

    # ---------- ouvintes (telas conectadas) ----------
    def adicionar_ouvinte(self, fn: Callable[[dict], None]):
        self.ouvintes.add(fn)

    def remover_ouvinte(self, fn):
        self.ouvintes.discard(fn)

    def transmitir(self, ev: dict):
        ev.setdefault("ts", time.time())
        for fn in list(self.ouvintes):
            try:
                fn(ev)
            except Exception as e:  # noqa: BLE001
                log.debug("ouvinte falhou: %s", e)

    async def emitir(self, ev: dict, t: Tarefa | None = None):
        if t is not None and t.parar_pedido and ev.get("tipo") != "fim" and t.task is asyncio.current_task():
            raise asyncio.CancelledError()  # garante a parada mesmo se um cancelamento foi "engolido"
        if t is not None:
            ev["tarefa"] = t.id
            if ev["tipo"] == "token":
                if ev.get("reset"):
                    t.texto_parcial = ""
                else:
                    t.texto_parcial += ev.get("texto", "")
            else:
                t.eventos.append(ev)
                if len(t.eventos) > MAX_EVENTOS_TAREFA * 2:
                    del t.eventos[: len(t.eventos) - MAX_EVENTOS_TAREFA]
        self.transmitir(ev)

    # ---------- fila ----------
    def _garantir_worker(self):
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._worker is None or self._worker.done():
            self._loop = loop
            self._tem_tarefa = asyncio.Event()
            if self.fila:
                self._tem_tarefa.set()
            self._worker = loop.create_task(self._trabalhar())

    async def enviar(self, texto: str, origem: str = "ws", politica: str = "perguntar") -> Tarefa:
        if kill_switch.off:
            raise RuntimeError("Estou desligada. Liga no botão lá em cima pra eu voltar.")
        t = Tarefa(texto, origem, politica)
        self._garantir_worker()
        self.fila.append(t)
        self._tem_tarefa.set()
        audit.log("tarefa_recebida", tarefa=t.id, origem=origem, texto=texto)
        self.transmitir(self.estado_evento())
        return t

    async def _trabalhar(self):
        while True:
            if not self.fila:
                self._tem_tarefa.clear()
                await self._tem_tarefa.wait()
                continue
            t = self.fila.popleft()
            if t.status != "na_fila":
                continue
            self.atual = t
            t.status = "rodando"
            t.task = asyncio.create_task(self._executar(t))
            await asyncio.wait({t.task})  # nunca propaga o cancelamento da tarefa para o worker
            self.ultima, self.atual = t, None
            self.transmitir(self.estado_evento())

    async def _executar(self, t: Tarefa):
        try:
            t.resposta = await agente.rodar(t, self)
            t.status = "concluida" if t.status == "rodando" else t.status
            await self.emitir({"tipo": "fim", "status": t.status, "modelo": t.modelo}, t)
        except asyncio.CancelledError:
            t.status = "cancelada"
            self._resolver_pendentes(t)
            audit.log("tarefa_cancelada", tarefa=t.id)
            await self.emitir({"tipo": "fim", "status": "cancelada", "texto": "Parei. ⏹"}, t)
        except Exception as e:  # noqa: BLE001 — nunca deixa um erro inesperado sem resposta
            t.status = "erro"
            log.exception("Erro inesperado na tarefa")
            audit.log("erro", tarefa=t.id, erro=f"{type(e).__name__}: {e}")
            t.resposta = "Deu um erro inesperado aqui dentro. Já registrei no log; tenta de novo?"
            await self.emitir({"tipo": "erro", "texto": t.resposta}, t)
            await self.emitir({"tipo": "fim", "status": "erro"}, t)
        finally:
            self._resolver_pendentes(t)
            t.fim.set()

    def _resolver_pendentes(self, t: Tarefa):
        for p in list(t.pendentes.values()):
            if not p["fut"].done():
                p["fut"].set_result(None)
        t.pendentes.clear()

    def parar(self, motivo: str = "botão parar") -> bool:
        t = self.atual
        if t and t.task and not t.task.done():
            audit.log("tarefa_parada", tarefa=t.id, motivo=motivo)
            t.parar_pedido = True
            self._resolver_pendentes(t)
            t.task.cancel()
            asyncio.get_running_loop().create_task(self._insistir_cancelamento(t))
            return True
        return False

    @staticmethod
    async def _insistir_cancelamento(t: Tarefa):
        """Algumas bibliotecas de rede protegem trechos contra cancelamento; insiste até a tarefa parar."""
        for _ in range(100):
            await asyncio.sleep(0.1)
            if t.task is None or t.task.done():
                return
            t.task.cancel()

    def parar_tudo(self, motivo: str = "desligada") -> int:
        n = 0
        while self.fila:
            t = self.fila.popleft()
            t.status = "cancelada"
            t.resposta = None
            t.fim.set()
            n += 1
        if self.parar(motivo):
            n += 1
        return n

    async def encerrar(self):
        """Desligamento do servidor: para tudo e encerra o worker e as tarefas de fundo."""
        self.parar_tudo("servidor desligando")
        loop = asyncio.get_running_loop()
        pendentes = [x for x in [self._worker, *(self.atual.task if self.atual else None,), *self.fundo]
                     if x and not x.done() and x.get_loop() is loop]
        for x in pendentes:
            x.cancel()
        if pendentes:
            await asyncio.wait(pendentes, timeout=5)

    # ---------- interação com o Gabriel ----------
    async def _esperar_gabriel(self, t: Tarefa, tipo: str, evento: dict, timeout: float):
        pid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        evento = {**evento, "tipo": tipo, "id": pid, "expira_em": time.time() + timeout}
        t.pendentes[pid] = {"tipo": tipo, "fut": fut, "evento": evento}
        await self.emitir(evento, t)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            await self.emitir({"tipo": tipo, "id": pid, "status": "expirada"}, t)
            return None
        finally:
            t.pendentes.pop(pid, None)

    async def pedir_aprovacao(self, t: Tarefa, ferramenta: str, args: dict, motivo: str | None) -> bool:
        if t.politica in ("negar", "permitir"):
            ok = t.politica == "permitir"
            audit.log("aprovacao", tarefa=t.id, ferramenta=ferramenta, aprovado=ok, automatica=True)
            return ok
        r = await self._esperar_gabriel(t, "aprovacao", {
            "ferramenta": ferramenta, "rotulo": rotulo(ferramenta), "args": _args_para_tela(args), "motivo": motivo,
        }, APROVACAO_TIMEOUT)
        ok = bool(r)
        audit.log("aprovacao", tarefa=t.id, ferramenta=ferramenta, aprovado=ok, expirou=r is None)
        return ok

    async def perguntar(self, t: Tarefa, pergunta: str, opcoes: list | None) -> str | None:
        if t.politica != "perguntar":
            return None
        r = await self._esperar_gabriel(t, "pergunta", {"pergunta": pergunta, "opcoes": list(opcoes or [])[:6]},
                                        PERGUNTA_TIMEOUT)
        return r if isinstance(r, str) else None

    def _achar_pendente(self, pid: str, tipo: str):
        for t in [self.atual, *self.fila]:
            if t and pid in t.pendentes and t.pendentes[pid]["tipo"] == tipo:
                return t, t.pendentes[pid]
        return None, None

    async def responder_aprovacao(self, pid: str, aprovado: bool) -> bool:
        t, p = self._achar_pendente(str(pid), "aprovacao")
        if p and not p["fut"].done():
            p["fut"].set_result(bool(aprovado))
            await self.emitir({"tipo": "aprovacao", "id": pid, "status": "permitido" if aprovado else "negado"}, t)
            return True
        return False

    async def responder_pergunta(self, pid: str, texto: str) -> bool:
        t, p = self._achar_pendente(str(pid), "pergunta")
        if p and not p["fut"].done():
            p["fut"].set_result(str(texto))
            await self.emitir({"tipo": "pergunta", "id": pid, "status": "respondida", "resposta": str(texto)}, t)
            return True
        return False

    # ---------- fundo (memória, resumo) ----------
    def em_fundo(self, coro, nome: str = "fundo"):
        async def _rodar():
            try:
                await coro
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                log.warning("Tarefa de fundo '%s' falhou: %s", nome, e)
                audit.log("erro_fundo", nome=nome, erro=f"{type(e).__name__}: {e}")
        task = asyncio.create_task(_rodar())
        self.fundo.add(task)
        task.add_done_callback(self.fundo.discard)
        return task

    async def aguardar_fundo(self, timeout: float = 30):
        if self.fundo:
            await asyncio.wait(list(self.fundo), timeout=timeout)

    # ---------- estado ----------
    def estado(self) -> dict:
        return {
            "desligada": kill_switch.off,
            "trabalhando": self.atual is not None,
            "tarefa": self.atual.resumo() if self.atual else None,
            "fila": [{"id": t.id, "texto": t.texto} for t in self.fila if t.status == "na_fila"],
            "modelo": cerebro.estado()["modelo_em_uso"],
        }

    def estado_evento(self) -> dict:
        return {"tipo": "estado", **self.estado()}


ger = Gerenciador()


class Agente:
    async def rodar(self, t: Tarefa, g: Gerenciador) -> str:
        async def emit(ev):
            await g.emitir(ev, t)

        if not cerebro.configurado:
            msg = LLMErro("sem_chave").amigavel
            await emit({"tipo": "erro", "texto": msg})
            return msg

        if t.origem != "continuacao":
            memory.add_message("user", t.texto)
        audit.log("mensagem_usuario", tarefa=t.id, texto=t.texto, origem=t.origem)
        msgs = await brain.montar_mensagens(t.texto)
        ferramentas = schemas()
        resposta_vazia = 0

        async def ao_token(txt):
            await emit({"tipo": "token", "texto": txt})

        async def ao_reiniciar():
            await emit({"tipo": "token", "reset": True})

        passo = 0
        while passo < MAX_STEPS:
            passo += 1
            if kill_switch.off:
                raise asyncio.CancelledError()
            await emit({"tipo": "pensando", "passo": passo})
            t.texto_parcial = ""
            try:
                r = await cerebro.chat(msgs, ferramentas, ao_token=ao_token, ao_reiniciar=ao_reiniciar)
            except LLMErro as e:
                audit.log("erro_llm", tarefa=t.id, tipo_erro=e.tipo, erro=e.tecnico[:1500], passo=passo)
                await emit({"tipo": "erro", "texto": e.amigavel, "tecnico_no_log": True})
                return e.amigavel
            t.modelo = r.modelo
            self._somar_uso(t, r.uso)
            audit.log("llm_chamada", tarefa=t.id, modelo=r.modelo, passo=passo, uso=r.uso,
                      duracao_s=round(r.duracao, 2), ferramentas=[tc["function"]["name"] for tc in r.tool_calls],
                      falhas_antes=r.tentativas or None)
            # a mensagem do assistente volta EXATAMENTE como veio (Gemini 3: thought_signature)
            msgs.append(r.mensagem)

            if not r.tool_calls:
                texto = (r.texto or "").strip()
                if not texto:
                    resposta_vazia += 1
                    if resposta_vazia <= 1:
                        msgs.pop()
                        passo -= 1
                        msgs.append({"role": "user", "content": "[sistema] Sua última resposta veio vazia. "
                                                                 f"Responda ao {USER_NAME} em texto."})
                        continue
                    texto = "Fiquei sem resposta dessa vez (o modelo devolveu vazio). Pode repetir de outro jeito?"
                memory.add_message("assistant", texto)
                audit.log("resposta_eva", tarefa=t.id, texto=texto, modelo=r.modelo, uso_total=t.uso)
                await emit({"tipo": "resposta", "texto": texto, "modelo": r.modelo})
                self._pos_resposta(g, t.texto, texto)
                return texto

            if r.texto and r.texto.strip():
                await emit({"tipo": "narracao", "texto": r.texto.strip()})
            for tc in r.tool_calls:
                resultado = await self.executar_ferramenta(tc, t, g)
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": resultado})

        # limite de passos: resume e pergunta se continua
        await emit({"tipo": "pensando", "passo": passo + 1, "limite": True})
        aviso = (f"[sistema] Você atingiu o limite de {MAX_STEPS} passos. NÃO use mais ferramentas agora. "
                 f"Resuma em poucas linhas o que já fez, o que falta, e pergunte ao {USER_NAME} se deve continuar.")
        try:
            r = await cerebro.chat(msgs + [{"role": "user", "content": aviso}], ferramentas, tool_choice="none",
                                   ao_token=ao_token, ao_reiniciar=ao_reiniciar)
            texto = (r.texto or "").strip()
            if r.tool_calls or not texto:
                raise LLMErro("requisicao", "resumo do limite veio sem texto")
        except LLMErro:
            await ao_reiniciar()
            texto = f"Cheguei no limite de {MAX_STEPS} passos sem terminar. Quer que eu continue de onde parei?"
        memory.add_message("assistant", texto)
        audit.log("limite_passos", tarefa=t.id, passos=MAX_STEPS)
        await emit({"tipo": "resposta", "texto": texto, "modelo": t.modelo, "limite": True})
        return texto

    @staticmethod
    def _somar_uso(t: Tarefa, uso: dict | None):
        if not uso:
            return
        for k in t.uso:
            try:
                t.uso[k] += int(uso.get(k) or 0)
            except (TypeError, ValueError):
                pass

    def _pos_resposta(self, g: Gerenciador, texto_usuario: str, resposta: str):
        async def _depois():
            await memory.extrair_fatos(texto_usuario, resposta, USER_NAME)
            await brain.atualizar_resumo()
        g.em_fundo(_depois(), "memoria_e_resumo")

    async def executar_ferramenta(self, tc: dict, t: Tarefa, g: Gerenciador) -> str:
        fn_info = tc.get("function") or {}
        nome = fn_info.get("name") or ""
        bruto = fn_info.get("arguments") or "{}"
        try:
            args = json.loads(bruto) if isinstance(bruto, str) else dict(bruto)
            if not isinstance(args, dict):
                raise ValueError("argumentos precisam ser um objeto JSON")
        except (ValueError, TypeError) as e:
            audit.log("acao_args_invalidos", tarefa=t.id, ferramenta=nome, args=str(bruto)[:500])
            return f"ERRO: os argumentos de '{nome}' não são um JSON válido ({e}). Mande de novo, corrigido."

        spec = TOOLS.get(nome)
        if spec is None:
            audit.log("acao_inexistente", tarefa=t.id, ferramenta=nome)
            return f"ERRO: a ferramenta '{nome}' não existe. Disponíveis: {', '.join(TOOLS)}."

        regra = permissions.get(nome)
        motivo = None
        if regra == "permitir" and spec.get("sensivel"):
            try:
                motivo = await spec["sensivel"](args)
            except Exception:  # noqa: BLE001
                motivo = None
            if motivo:
                regra = "perguntar"
        audit.log("acao_solicitada", tarefa=t.id, ferramenta=nome, args=args, regra=regra, motivo=motivo)
        await g.emitir({"tipo": "acao", "ferramenta": nome, "rotulo": spec["rotulo"], "args": _args_para_tela(args),
                        "regra": regra, "id": tc.get("id")}, t)

        if regra == "bloquear":
            audit.log("acao_bloqueada", tarefa=t.id, ferramenta=nome)
            res = f"BLOQUEADO: o {USER_NAME} bloqueou a ferramenta '{nome}'. Não tente contornar; ofereça outra saída."
            await g.emitir({"tipo": "resultado", "ferramenta": nome, "ok": False, "texto": "Bloqueado pelas permissões.",
                            "id": tc.get("id")}, t)
            return res
        if regra == "perguntar":
            ok = await g.pedir_aprovacao(t, nome, args, motivo)
            if not ok:
                await g.emitir({"tipo": "resultado", "ferramenta": nome, "ok": False, "texto": "Negado.",
                                "id": tc.get("id")}, t)
                return (f"NEGADO: o {USER_NAME} não aprovou '{nome}' (ou não respondeu em 5 minutos). "
                        "Não tente de novo nem contorne; explique e pergunte o que ele prefere.")
        if kill_switch.off:
            raise asyncio.CancelledError()

        inicio = time.monotonic()
        ok = True
        try:
            if spec.get("interna") and nome == "perguntar_ao_gabriel":
                resposta = await g.perguntar(t, str(args.get("pergunta", "")), args.get("opcoes"))
                if resposta is None:
                    resultado = (f"O {USER_NAME} não respondeu (ou não está disponível agora). Siga com a suposição "
                                 "mais razoável e diga qual foi, ou termine perguntando na resposta final.")
                else:
                    resultado = f"Resposta do {USER_NAME}: {resposta}"
            else:
                resultado = await asyncio.wait_for(spec["fn"](**args), timeout=spec.get("timeout") or 120)
                resultado = "(feito, sem retorno)" if resultado is None else str(resultado)
                if resultado.startswith("ERRO"):
                    ok = False
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            ok = False
            resultado = f"ERRO: '{nome}' demorou demais e foi interrompida. Tente algo mais simples ou outro caminho."
        except TypeError as e:
            ok = False
            resultado = f"ERRO: argumentos errados para '{nome}': {e}. Confira os parâmetros e tente de novo."
        except Exception as e:  # noqa: BLE001
            ok = False
            resultado = f"ERRO ao executar '{nome}': {type(e).__name__}: {str(e)[:800]}. Corrija ou tente outro caminho."
        resultado = truncar(resultado)
        audit.log("acao_resultado", tarefa=t.id, ferramenta=nome, ok=ok, duracao_s=round(time.monotonic() - inicio, 2),
                  resultado=resultado[:3000])
        await g.emitir({"tipo": "resultado", "ferramenta": nome, "ok": ok, "texto": resultado[:700],
                        "id": tc.get("id")}, t)
        if nome.startswith("navegador_"):
            from .tools.browser import nav
            await g.emitir({"tipo": "tela", "versao": nav.tela_versao}, t)
        return resultado


agente = Agente()


async def desligar_tudo(motivo: str = "botão de desligar") -> int:
    """Killswitch completo: bloqueia novas tarefas, cancela as atuais, fecha o navegador."""
    kill_switch.kill(motivo)
    n = ger.parar_tudo(motivo)
    try:
        from .tools.browser import fechar_navegador
        await fechar_navegador()
    except Exception as e:  # noqa: BLE001
        log.warning("Falha ao fechar navegador: %s", e)
    ger.transmitir(ger.estado_evento())
    return n


def ligar():
    kill_switch.revive()
    cerebro.reset()
    ger.transmitir(ger.estado_evento())
