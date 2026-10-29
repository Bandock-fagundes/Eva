"""Coração do EVA-Agente: conexão com a Eva, comandos, pausa/emergência, tela ao vivo. Sem nada de interface gráfica
(o ícone, o aviso na tela e o atalho ficam em app.py e falam com este núcleo por métodos seguros entre threads).

Ciclo: conecta (wss://.../ws/pc, token no cabeçalho) -> manda 'ola' -> recebe comandos e responde -> se cair,
reconecta com espera crescente (1 s, 2 s, 4 s… até 1 min, com variação aleatória).
Códigos de fechamento da Eva: 4401 = token inválido/revogado (precisa parear de novo), 4409 = outro PC conectou,
4429 = tentativas demais.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import random
import time
import urllib.request

from . import VERSAO, imagem
from .config import Config
from .executor import ErroAgente, Executor, descrever
from .pareamento import contexto_ssl, url_ws

OPS_ENTRADA = {"mouse", "teclado", "abrir", "focar", "clipboard_escrever"}
OPS_SEM_AVISO = {"info", "janelas", "clipboard_ler", "arquivos_listar"}   # só leitura rápida: sem aviso na tela
TEXTO_EVA = "Eva está usando o computador  •  Ctrl+Alt+Shift+P para parar"
TEXTO_GABRIEL = "Controle remoto pelo app da Eva  •  Ctrl+Alt+Shift+P para parar"


class Interface:
    """O que o núcleo avisa para a interface (ícone/aviso). A padrão não faz nada (testes, modo texto)."""

    def estado(self, estado: str, texto: str):
        """estado: conectando | conectado | agindo | pausado | desconectado | revogado | parado."""

    def banner(self, visivel: bool, texto: str = ""):
        """Aviso sempre visível 'Eva está usando o computador'."""

    def aviso(self, titulo: str, texto: str):
        """Notificação do Windows (balãozinho do ícone)."""

    def token_invalido(self, motivo: str):
        """O PC foi desconectado da Eva (token revogado): precisa de um código novo."""


class Agente:
    def __init__(self, cfg: Config, plataforma, interface: Interface | None = None,
                 registro: logging.Logger | None = None, casa=None, nome: str | None = None):
        self.cfg = cfg
        self.p = plataforma
        self.ui = interface or Interface()
        self.log = registro or logging.getLogger("eva_agente")
        self.executor = Executor(plataforma, casa=casa, registro=self.log)
        self.nome = (nome or cfg.nome or platform.node() or "PC")[:60]
        self.pausa_local: str | None = None
        self.pausa_remota: str | None = None
        self.controle_gabriel = False
        self.ao_vivo = False
        self.fps = 2.0
        self.conectado = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.fim_motivo: str | None = None
        self.backoff_base = 1.0
        self.backoff_max = 60.0
        self.intervalo_ping = 20.0
        self.intervalo_acordar = 600.0
        self.qualidade = 55
        self.largura_ao_vivo = 1280
        self.espera_ack = 5.0          # segundos esperando a Eva confirmar um quadro ao vivo
        self.atraso_banner = 2.0       # o aviso some X segundos depois da última ação
        self.estatisticas = {"quadros": 0, "acoes": 0, "conexoes": 0}
        self._ws = None
        self._parar: asyncio.Event | None = None
        self._acoes: set[asyncio.Task] = set()
        self._trava_entrada: asyncio.Lock | None = None
        self._trava_envio: asyncio.Lock | None = None
        self._frame_ok: asyncio.Event | None = None
        self._tarefa_vivo: asyncio.Task | None = None
        self._agindo = 0
        self._agindo_ate = 0.0
        self._origem_atual = "eva"
        self._esconder: asyncio.TimerHandle | None = None
        self._ultimo_pong = 0.0

    # ================================================================ controles (seguros entre threads)
    def _chamar(self, fn, *a):
        loop = self.loop
        if loop is not None and loop.is_running():
            try:
                if asyncio.get_running_loop() is loop:
                    fn(*a)
                    return
            except RuntimeError:
                pass
            loop.call_soon_threadsafe(fn, *a)
        else:
            fn(*a)

    def emergencia(self, motivo: str = "atalho de emergência (Ctrl+Alt+Shift+P)"):
        """Para TUDO na hora: cancela a ação em andamento, mata comandos, solta teclas e pausa a Eva."""
        self._chamar(self._pausar, motivo, True)

    def pausar(self, motivo: str = "pausada pelo ícone"):
        self._chamar(self._pausar, motivo, True)

    def retomar(self):
        self._chamar(self._retomar)

    def desconectar(self):
        """'Desconectar este PC' no ícone: pede para a Eva revogar o token e esquece o token aqui."""
        self._chamar(lambda: asyncio.ensure_future(self._desconectar()))

    def sair(self):
        self._chamar(self._sair)

    @property
    def pausado(self) -> bool:
        return bool(self.pausa_local or self.pausa_remota)

    def estado_atual(self) -> dict:
        return {"conectado": self.conectado, "pausa_local": self.pausa_local, "pausa_remota": self.pausa_remota,
                "controle_gabriel": self.controle_gabriel, "ao_vivo": self.ao_vivo, "agindo": self._agindo > 0,
                "servidor": self.cfg.servidor, "nome": self.nome}

    # ================================================================ pausa
    def _pausar(self, motivo: str, emergencia: bool = False):
        nova = self.pausa_local != motivo
        self.pausa_local = motivo
        for t in list(self._acoes):
            t.cancel()
        if self.loop is not None:
            asyncio.ensure_future(self.executor.parar())
        if nova:
            self.log.info("PAUSADA: %s", motivo)
            self.ui.aviso("Eva pausada", f"{motivo}. Para liberar: ícone da Eva → Retomar.")
        self._banner(False)
        self._atualizar_ui()
        if self.conectado:
            asyncio.ensure_future(self._enviar({"tipo": "estado", "pausado": True, "motivo": motivo,
                                                "emergencia": emergencia}))

    def _retomar(self):
        if self.pausa_local is None:
            return
        if self.executor.mouse_no_canto():
            self.ui.aviso("Ainda pausada", "Tire o mouse do canto da tela e clique em Retomar de novo.")
            return
        self.log.info("RETOMADA pelo Gabriel")
        self.pausa_local = None
        self._atualizar_ui()
        if self.conectado:
            asyncio.ensure_future(self._enviar({"tipo": "estado", "pausado": False}))

    def _sair(self):
        self.fim_motivo = self.fim_motivo or "sair"
        if self._parar is not None:
            self._parar.set()
        for t in list(self._acoes):
            t.cancel()
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    async def _desconectar(self):
        self.log.info("Desconectando este PC da Eva (pedido no ícone)")
        if self.conectado:
            try:
                await self._enviar({"tipo": "desconectar"})
                await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001
                pass
        self.fim_motivo = "desconectado"
        self._sair()

    # ================================================================ interface
    def _atualizar_ui(self):
        if self.pausa_local:
            self.ui.estado("pausado", f"Eva pausada: {self.pausa_local}")
        elif self.pausa_remota:
            self.ui.estado("pausado", f"Eva pausada: {self.pausa_remota}")
        elif not self.conectado:
            self.ui.estado("desconectado", "Sem conexão com a Eva")
        elif self._agindo > 0:
            self.ui.estado("agindo", "Você está controlando pelo app" if self._origem_atual == "gabriel"
                           else "Eva está usando o computador")
        elif self.controle_gabriel:
            self.ui.estado("conectado", "Conectado • você está no controle pelo app")
        else:
            self.ui.estado("conectado", "Conectado à Eva")

    def _banner(self, visivel: bool, texto: str = ""):
        try:
            self.ui.banner(visivel, texto)
        except Exception as e:  # noqa: BLE001
            self.log.debug("banner falhou: %s", e)

    def _inicio_acao(self, origem: str, avisar: bool):
        self._agindo += 1
        self._origem_atual = origem
        if self._esconder is not None:
            self._esconder.cancel()
            self._esconder = None
        if avisar:
            self._banner(True, TEXTO_GABRIEL if origem == "gabriel" else TEXTO_EVA)
        self._atualizar_ui()

    def _fim_acao(self):
        self._agindo = max(0, self._agindo - 1)
        self._agindo_ate = time.monotonic()
        if self._agindo == 0 and self.loop is not None:
            def esconder():
                self._esconder = None
                if self._agindo == 0:
                    self._banner(False)
                    self._atualizar_ui()
            self._esconder = self.loop.call_later(self.atraso_banner, esconder)

    # ================================================================ conexão
    async def rodar(self) -> str:
        """Fica conectado até sair(). Devolve o motivo do fim: 'sair' | 'revogado' | 'desconectado'."""
        self.loop = asyncio.get_running_loop()
        self._parar = asyncio.Event()
        self._trava_entrada = asyncio.Lock()
        self._trava_envio = asyncio.Lock()
        self._frame_ok = asyncio.Event()
        tentativa = 0
        try:
            while not self._parar.is_set():
                self.ui.estado("conectando", "Conectando à Eva…")
                inicio = time.monotonic()
                try:
                    codigo = await self._sessao()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.log.info("conexão falhou: %s: %s", type(e).__name__, e)
                    codigo = None
                self.conectado = False
                self.ao_vivo = False
                if self._parar.is_set():
                    break
                if codigo == 4401:
                    self.fim_motivo = "revogado"
                    self.log.info("A Eva recusou o token deste PC (desconectado no app ou token inválido)")
                    self.ui.estado("revogado", "Este PC foi desconectado da Eva")
                    self.ui.token_invalido("Este PC foi desconectado da Eva. Gere um código novo no app.")
                    break
                if time.monotonic() - inicio > 60:
                    tentativa = 0
                if codigo == 4409:
                    espera = 300.0
                    self.ui.aviso("Outro PC conectou", "Outro computador está usando a Eva agora. Tento de novo em "
                                                       "5 minutos.")
                elif codigo == 4429:
                    espera = 600.0
                else:
                    espera = min(self.backoff_max, self.backoff_base * (2 ** tentativa)) * (0.75 + random.random() * 0.5)
                tentativa = min(tentativa + 1, 10)
                self.ui.estado("desconectado", f"Sem conexão com a Eva. Tento de novo em {int(espera) or 1} s…")
                try:
                    await asyncio.wait_for(self._parar.wait(), timeout=espera)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.conectado = False
            for t in list(self._acoes):
                t.cancel()
            await self.executor.parar()
            self.ui.estado("parado", "Desligado")
        return self.fim_motivo or "sair"

    async def _sessao(self) -> int | None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        uri = url_ws(self.cfg.servidor)
        kw = dict(additional_headers={"Authorization": f"Bearer {self.cfg.token}"}, open_timeout=90,
                  ping_interval=20, ping_timeout=40, close_timeout=5, max_size=64 * 1024 * 1024,
                  user_agent_header=f"EVA-Agente/{VERSAO}")
        if uri.startswith("wss://"):
            kw["ssl"] = contexto_ssl()
        tarefas: list[asyncio.Task] = []
        ws = None
        try:
            async with connect(uri, **kw) as ws:
                self._ws = ws
                self.estatisticas["conexoes"] += 1
                w, h = self.p.tamanho_tela()
                await self._enviar({"tipo": "ola", "nome": self.nome, "versao": VERSAO,
                                    "tela": {"largura": w, "altura": h}, "pausado": bool(self.pausa_local),
                                    "motivo": self.pausa_local, "plataforma": getattr(self.p, "nome", "?")})
                self._ultimo_pong = time.monotonic()
                tarefas = [asyncio.ensure_future(self._batimento(ws)), asyncio.ensure_future(self._vigia_canto()),
                           asyncio.ensure_future(self._manter_acordada())]
                async for msg in ws:
                    await self._tratar(msg)
                return ws.close_code
        except ConnectionClosed as e:
            return e.rcvd.code if e.rcvd is not None else None
        except InvalidStatus as e:
            self.log.info("a Eva respondeu %s ao conectar (pode estar acordando)", e.response.status_code)
            return None
        finally:
            for t in tarefas:
                t.cancel()
            if self._tarefa_vivo is not None:
                self._tarefa_vivo.cancel()
                self._tarefa_vivo = None
            if self.conectado:
                self.log.info("desconectado da Eva")
            self.conectado = False
            self._ws = None
            self._atualizar_ui()

    async def _enviar(self, msg: dict):
        ws = self._ws
        if ws is None:
            return
        async with self._trava_envio:
            await ws.send(json.dumps(msg, ensure_ascii=False))

    async def _enviar_bytes(self, dados: bytes):
        ws = self._ws
        if ws is None:
            return
        async with self._trava_envio:
            await ws.send(dados)

    async def _batimento(self, ws):
        while True:
            await asyncio.sleep(self.intervalo_ping)
            if time.monotonic() - self._ultimo_pong > self.intervalo_ping * 3.5:
                self.log.info("a Eva parou de responder; reconectando")
                await ws.close(code=4000, reason="sem pong")
                return
            try:
                await self._enviar({"tipo": "ping", "ts": time.time()})
            except Exception:  # noqa: BLE001
                return

    async def _manter_acordada(self):
        """O Render grátis dorme sem visitas HTTP: um ping leve a cada 10 min enquanto o PC está conectado."""
        if not self.cfg.manter_eva_acordada:
            return
        url = self.cfg.servidor.rstrip("/") + "/api/saude?banco=0"

        def pingar():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"EVA-Agente/{VERSAO}"})
                with urllib.request.urlopen(req, timeout=30, context=contexto_ssl() if url.startswith("https") else None):
                    pass
            except Exception:  # noqa: BLE001
                pass
        while True:
            await asyncio.sleep(self.intervalo_acordar)
            await asyncio.to_thread(pingar)

    async def _vigia_canto(self):
        """Failsafe: enquanto a Eva age, se o Gabriel jogar o mouse num canto da tela, pausa tudo."""
        while True:
            await asyncio.sleep(0.25)
            if self.pausa_local or self._origem_atual == "gabriel":
                continue
            if self._agindo > 0 or time.monotonic() - self._agindo_ate < 1.5:
                if await asyncio.to_thread(self.executor.mouse_no_canto):
                    self._pausar("mouse no canto da tela (freio de emergência)", True)

    # ================================================================ mensagens
    async def _tratar(self, bruto):
        if isinstance(bruto, (bytes, bytearray)):
            return
        try:
            msg = json.loads(bruto)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        tipo = msg.get("tipo")
        if tipo == "cmd":
            t = asyncio.ensure_future(self._cmd(msg))
            self._acoes.add(t)
            t.add_done_callback(self._acoes.discard)
        elif tipo == "pong":
            self._ultimo_pong = time.monotonic()
        elif tipo == "frame_ok":
            self._frame_ok.set()
        elif tipo == "bem_vindo":
            self.conectado = True
            self._ultimo_pong = time.monotonic()
            self.log.info("conectado à Eva (%s) como '%s'", self.cfg.servidor, self.nome)
            self._atualizar_ui()
        elif tipo == "ao_vivo":
            self.ao_vivo = bool(msg.get("ligar"))
            try:
                self.fps = max(0.5, min(5.0, float(msg.get("fps") or self.fps)))
            except (TypeError, ValueError):
                pass
            if self.ao_vivo and (self._tarefa_vivo is None or self._tarefa_vivo.done()):
                self._tarefa_vivo = asyncio.ensure_future(self._vivo())
            self.log.info("tela ao vivo %s", "LIGADA (alguém está assistindo no app)" if self.ao_vivo else "desligada")
        elif tipo == "parar":
            motivo = str(msg.get("motivo") or "desligada no app")[:120]
            self.pausa_remota = motivo
            for t in list(self._acoes):
                t.cancel()
            await self.executor.parar()
            self._banner(False)
            self.log.info("PARADA REMOTA: %s", motivo)
            self._atualizar_ui()
        elif tipo == "liberar":
            if self.pausa_remota:
                self.log.info("liberada de novo no app")
            self.pausa_remota = None
            self._atualizar_ui()
        elif tipo == "controle":
            self.controle_gabriel = bool(msg.get("gabriel"))
            self.log.info("controle: %s", "Gabriel (pelo app)" if self.controle_gabriel else "Eva")
            self._atualizar_ui()
        elif tipo == "revogado":
            self.log.info("a Eva avisou: %s", msg.get("motivo"))

    async def _responder(self, pid, ok: bool, dados: dict | None = None, erro: str | None = None,
                         codigo: str | None = None):
        msg = {"tipo": "resp", "id": pid, "ok": ok}
        if ok:
            msg["dados"] = dados or {}
        else:
            msg["erro"] = erro
            msg["codigo"] = codigo or "falhou"
        try:
            await self._enviar(msg)
        except Exception as e:  # noqa: BLE001
            self.log.info("não consegui responder à Eva: %s", type(e).__name__)

    async def _cmd(self, msg: dict):
        pid = msg.get("id")
        op = str(msg.get("op") or "")
        args = msg.get("args") if isinstance(msg.get("args"), dict) else {}
        origem = "gabriel" if args.get("origem") == "gabriel" else "eva"
        quem = "Gabriel (app)" if origem == "gabriel" else "Eva"
        if self.pausado:
            motivo = self.pausa_local or self.pausa_remota
            self.log.info("RECUSADO (pausada) %s: %s", quem, descrever(op, args))
            await self._responder(pid, False, erro=f"pausada: {motivo}", codigo="pausado")
            return
        if origem == "gabriel" and not self.controle_gabriel:
            await self._responder(pid, False, erro="o controle está com a Eva", codigo="proibido")
            return
        if op in OPS_ENTRADA and origem == "eva" and await asyncio.to_thread(self.executor.mouse_no_canto):
            self._acoes.discard(asyncio.current_task())   # não cancela a própria resposta
            self._pausar("mouse no canto da tela (freio de emergência)", True)
            await self._responder(pid, False, erro="o Gabriel jogou o mouse no canto (freio de emergência)",
                                  codigo="pausado")
            return
        self._inicio_acao(origem, op not in OPS_SEM_AVISO)
        try:
            if op in ("tela", "info"):
                dados = await self.executor.executar(op, args)
            else:
                async with self._trava_entrada:
                    dados = await self.executor.executar(op, args)
            self.estatisticas["acoes"] += 1
            if op not in ("info", "tela"):
                self.log.info("%s: %s", quem, descrever(op, args))
            await self._responder(pid, True, dados)
        except ErroAgente as e:
            self.log.info("%s: %s -> ERRO %s", quem, descrever(op, args), e)
            await self._responder(pid, False, erro=str(e), codigo=e.codigo)
        except asyncio.CancelledError:
            self.log.info("%s: %s -> INTERROMPIDA", quem, descrever(op, args))
            await self._responder(pid, False, erro=f"interrompida: {self.pausa_local or self.pausa_remota or 'parada'}",
                                  codigo="pausado")
        except (TypeError, ValueError) as e:
            await self._responder(pid, False, erro=f"argumentos inválidos para {op}: {e}", codigo="invalido")
        except Exception as e:  # noqa: BLE001
            self.log.exception("falha inesperada em %s", op)
            await self._responder(pid, False, erro=f"falha inesperada no PC: {type(e).__name__}: {str(e)[:200]}")
        finally:
            self._fim_acao()

    # ================================================================ tela ao vivo
    async def _vivo(self):
        """Manda quadros JPEG (~2 por segundo) enquanto alguém assiste. Espera a Eva confirmar cada quadro antes de
        mandar o próximo (não entope a internet) e ajusta qualidade/tamanho conforme a demora."""
        ex = self.executor
        ultimo, ultimo_envio = None, 0.0
        while self.ao_vivo and self._ws is not None:
            t0 = time.monotonic()
            if self.pausa_local:  # pausado no PC: privacidade, não transmite
                await asyncio.sleep(0.5)
                continue
            try:
                img = await ex._cap(self.p.capturar)
                pos = await ex._em(self.p.posicao_mouse)
                marca = (await ex._cap(imagem.impressao_digital, img), tuple(pos))
                if marca == ultimo and t0 - ultimo_envio < 5:
                    await asyncio.sleep(max(0.1, 1 / self.fps))
                    continue
                jpeg, *_ = await ex._cap(imagem.processar, img, pos, self.largura_ao_vivo, self.qualidade)
            except ErroAgente as e:
                self.log.debug("quadro ao vivo falhou: %s", e)
                await asyncio.sleep(2)
                continue
            self._frame_ok.clear()
            t_envio = time.monotonic()
            try:
                await self._enviar_bytes(b"F" + jpeg)
            except Exception:  # noqa: BLE001
                return
            try:
                await asyncio.wait_for(self._frame_ok.wait(), timeout=self.espera_ack)
                demora = time.monotonic() - t_envio
            except asyncio.TimeoutError:
                demora = max(5.0, self.espera_ack)
            self._adaptar(demora)
            self.estatisticas["quadros"] += 1
            ultimo, ultimo_envio = marca, time.monotonic()
            await asyncio.sleep(max(0.05, 1 / self.fps - (time.monotonic() - t0)))

    def _adaptar(self, demora: float):
        if demora > 1.5:
            if self.qualidade > 30:
                self.qualidade = max(30, self.qualidade - 10)
            elif self.largura_ao_vivo > 800:
                self.largura_ao_vivo = max(800, self.largura_ao_vivo - 240)
        elif demora < 0.4:
            if self.largura_ao_vivo < 1280 and self.qualidade >= 50:
                self.largura_ao_vivo = min(1280, self.largura_ao_vivo + 240)
            elif self.qualidade < 70:
                self.qualidade = min(70, self.qualidade + 5)
