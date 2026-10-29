"""Ponte com o PC Windows do Gabriel (programa EVA-Agente, pasta agente_windows/).

- O agente no PC abre uma conexão WebSocket de SAÍDA para a Eva (wss://.../ws/pc): nenhuma porta é aberta no PC,
  e funciona atrás de qualquer roteador. Pela mesma conexão a Eva manda comandos (cada um com um id) e o agente
  responde; o agente também manda quadros da tela ao vivo (mensagens binárias) que a Eva repassa para as abas
  Tela abertas (/ws/tela).
- Pareamento: o app gera um código de uso único que vale 10 minutos; o agente troca o código por um token longo.
  No banco fica só o hash (sha256) do token (tabela dispositivos). "Desconectar" revoga o token.
- Só um PC fica conectado por vez: se outro conectar, o anterior é desligado (código 4409).

Protocolo (JSON, campo "tipo"):
  agente -> Eva: ola {nome, versao, tela, pausado} | resp {id, ok, dados|erro, codigo} | ping | estado {pausado, motivo}
                 | desconectar ;  binário: b"F" + JPEG (quadro ao vivo)
  Eva -> agente: cmd {id, op, args} | pong | frame_ok | ao_vivo {ligar, fps} | parar {motivo} | liberar
                 | controle {gabriel}
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Awaitable, Callable

from . import audit
from .db import db
from .killswitch import kill_switch

log = logging.getLogger("eva.pc_hub")

CODIGO_VALIDADE = 600          # 10 minutos
CODIGO_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # sem 0/O, 1/I/L (fácil de digitar)
PREFIXO_TOKEN = "evapc_"
SEM_CONTATO_MAX = 90.0         # segundos sem nenhuma mensagem do agente = conexão morta
MAX_FALHAS_IP = 10
MAX_FALHAS_GLOBAL = 40
JANELA_FALHAS = 600


class ComputadorErro(Exception):
    """Erro com mensagem pronta (PT-BR) para devolver ao modelo/usuário."""


def _agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hash_token(token: str) -> str:
    return hashlib.sha256(("eva-pc:" + (token or "")).encode()).hexdigest()


def normalizar_codigo(codigo: str) -> str:
    return "".join(c for c in str(codigo or "").upper() if c.isalnum())


# ---------------------------------------------------------------- pareamento e dispositivos (banco)

def criar_codigo() -> dict:
    """Novo código de pareamento (o anterior deixa de valer). Ex.: 'K7PX-4MQ2'."""
    bruto = "".join(secrets.choice(CODIGO_ALFABETO) for _ in range(8))
    expira = time.time() + CODIGO_VALIDADE
    db.set("pc_codigo", json.dumps({"hash": hash_token(bruto), "expira": expira}))
    hub.codigo_expira = expira
    audit.log("pc_codigo_criado")
    return {"codigo": f"{bruto[:4]}-{bruto[4:]}", "expira_em": expira, "validade_s": CODIGO_VALIDADE}


def cancelar_codigo():
    db.delete_key("pc_codigo")
    hub.codigo_expira = 0.0


def codigo_pendente() -> float | None:
    """Há um código válido esperando? Devolve quando expira. (Guardado em memória: não acorda o banco à toa.)"""
    if hub.codigo_expira is None:
        try:
            d = json.loads(db.get("pc_codigo", "") or "{}")
            hub.codigo_expira = float(d.get("expira") or 0)
        except Exception:  # noqa: BLE001
            return None
    exp = hub.codigo_expira or 0.0
    return exp if exp > time.time() else None


def confirmar_codigo(codigo: str, nome: str, versao: str = "") -> dict | None:
    """Troca o código (uso único) por um token. Devolve {id, token, nome} ou None se inválido/expirado."""
    try:
        d = json.loads(db.get("pc_codigo", "") or "{}")
    except ValueError:
        d = {}
    norm = normalizar_codigo(codigo)
    if not d or len(norm) != 8 or float(d.get("expira") or 0) < time.time():
        return None
    if not secrets.compare_digest(hash_token(norm), str(d.get("hash", ""))):
        return None
    db.delete_key("pc_codigo")  # uso único
    hub.codigo_expira = 0.0
    token = PREFIXO_TOKEN + secrets.token_urlsafe(32)
    nome = (str(nome or "").strip() or "PC do Gabriel")[:60]
    did = db.insert("INSERT INTO dispositivos(nome, token_hash, criado, ultimo_contato, versao, revogado) "
                    "VALUES(?,?,?,?,?,0)", (nome, hash_token(token), _agora_iso(), None, str(versao or "")[:20]))
    audit.log("pc_pareado", dispositivo=did, nome=nome, versao=versao)
    hub.recarregar()
    return {"id": did, "token": token, "nome": nome}


def dispositivo_por_token(token: str) -> dict | None:
    if not token or not str(token).startswith(PREFIXO_TOKEN):
        return None
    return db.one("SELECT id, nome, versao, criado, ultimo_contato FROM dispositivos WHERE token_hash=? AND revogado=0",
                  (hash_token(token),))


def listar_dispositivos() -> list[dict]:
    linhas = db.all("SELECT id, nome, versao, criado, ultimo_contato FROM dispositivos WHERE revogado=0 ORDER BY id")
    for r in linhas:
        r["conectado"] = hub.conectado and (hub.dispositivo or {}).get("id") == r["id"]
    return linhas


def revogar_dispositivo(did: int, motivo: str = "Gabriel desconectou") -> bool:
    n = db.run("UPDATE dispositivos SET revogado=1 WHERE id=? AND revogado=0", (int(did),))
    if n:
        audit.log("pc_desconectado", dispositivo=did, motivo=motivo)
    hub.recarregar()
    return bool(n)


def tocar_dispositivo(did: int, versao: str | None = None):
    try:
        if versao:
            db.run("UPDATE dispositivos SET ultimo_contato=?, versao=? WHERE id=?", (_agora_iso(), str(versao)[:20], did))
        else:
            db.run("UPDATE dispositivos SET ultimo_contato=? WHERE id=?", (_agora_iso(), did))
    except Exception as e:  # noqa: BLE001 — banco fora do ar não derruba a conexão do PC
        log.warning("não consegui atualizar o último contato do PC: %s", e)


class LimiteFalhas:
    """Freio contra força bruta no pareamento e no token do PC."""

    def __init__(self):
        self.ip: dict[str, deque] = defaultdict(deque)
        self.glob: deque = deque()

    def _limpar(self, q: deque):
        agora = time.time()
        while q and agora - q[0] > JANELA_FALHAS:
            q.popleft()

    def bloqueado(self, ip: str) -> bool:
        self._limpar(self.ip[ip])
        self._limpar(self.glob)
        return len(self.ip[ip]) >= MAX_FALHAS_IP or len(self.glob) >= MAX_FALHAS_GLOBAL

    def falhou(self, ip: str):
        agora = time.time()
        self.ip[ip].append(agora)
        self.glob.append(agora)

    def zerar(self):
        self.ip.clear()
        self.glob.clear()


limite = LimiteFalhas()


# ---------------------------------------------------------------- conexão com o agente

class Conexao:
    """O que o hub precisa de uma conexão (o servidor embrulha o WebSocket do Starlette nisso)."""

    async def enviar_texto(self, texto: str): ...
    async def enviar_bytes(self, dados: bytes): ...
    async def fechar(self, codigo: int = 1000, motivo: str = ""): ...


class PCHub:
    def __init__(self):
        self.ao_mudar: Callable[[], None] | None = None          # avisa as telas (definido em agent.py)
        self.ao_emergencia: Callable[[str], None] | None = None  # para a tarefa da Eva (definido em agent.py)
        self.reset()

    def reset(self):
        self.conn: Conexao | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.dispositivo: dict | None = None
        self.versao = ""
        self.largura, self.altura = 0, 0
        self.pausado = False
        self.motivo_pausa: str | None = None
        self.conectado_em = 0.0
        self.ultimo_contato = 0.0
        self.pendentes: dict[str, asyncio.Future] = {}
        self._envio: asyncio.Lock | None = None
        self.espectadores: dict[str, Callable[[bytes], None]] = {}
        self.quadro: bytes | None = None
        self.quadro_versao = 0
        self.quadro_ts = 0.0
        self.fps = 2.0
        self._tem_dispositivo: bool | None = None
        self.codigo_expira: float | None = None   # cache do código de pareamento pendente

    # ---------- estado ----------
    @property
    def conectado(self) -> bool:
        return self.conn is not None and (time.time() - self.ultimo_contato) < SEM_CONTATO_MAX

    @property
    def tem_dispositivo(self) -> bool:
        if self._tem_dispositivo is None:
            try:
                self._tem_dispositivo = db.one("SELECT id FROM dispositivos WHERE revogado=0 LIMIT 1") is not None
            except Exception:  # noqa: BLE001
                return False
        return self._tem_dispositivo

    def recarregar(self):
        self._tem_dispositivo = None

    def estado(self) -> dict:
        d = self.dispositivo or {}
        return {
            "pareado": self.tem_dispositivo or self.conectado,
            "conectado": self.conectado,
            "nome": d.get("nome"),
            "dispositivo_id": d.get("id"),
            "versao_agente": self.versao or None,
            "pausado_no_pc": self.pausado,
            "motivo_pausa": self.motivo_pausa,
            "ultimo_contato": self.ultimo_contato or None,
            "conectado_em": self.conectado_em or None,
            "assistindo": len(self.espectadores),
            "quadro_versao": self.quadro_versao,
            "codigo_pendente": bool(codigo_pendente()) if not self.conectado else False,
        }

    def _mudou(self):
        if self.ao_mudar:
            try:
                self.ao_mudar()
            except Exception as e:  # noqa: BLE001
                log.debug("ao_mudar falhou: %s", e)

    # ---------- ciclo da conexão ----------
    async def conectar(self, conn: Conexao, dispositivo: dict, ola: dict):
        antiga = self.conn
        if antiga is not None and antiga is not conn:
            self._falhar_pendentes("o PC foi reconectado")
            try:
                await antiga.fechar(4409, "outro PC conectou na Eva")
            except Exception:  # noqa: BLE001
                pass
        self.conn = conn
        self.loop = asyncio.get_running_loop()
        self._envio = asyncio.Lock()
        self.dispositivo = {"id": dispositivo["id"], "nome": str(ola.get("nome") or dispositivo.get("nome") or "PC")[:60]}
        self.versao = str(ola.get("versao") or "")[:20]
        self._atualizar_tela(ola.get("tela"))
        self.pausado = bool(ola.get("pausado"))
        self.motivo_pausa = str(ola.get("motivo") or "")[:120] or None
        self.conectado_em = self.ultimo_contato = time.time()
        await asyncio.to_thread(tocar_dispositivo, dispositivo["id"], self.versao)
        audit.log("pc_conectado", dispositivo=dispositivo["id"], nome=self.dispositivo["nome"], versao=self.versao,
                  tela=f"{self.largura}x{self.altura}")
        await self._enviar({"tipo": "bem_vindo", "nome": self.dispositivo["nome"]})
        if kill_switch.off:
            await self._enviar({"tipo": "parar", "motivo": "A Eva está desligada no app."})
        from .computador import pc  # evita import circular
        if pc.controle_gabriel:
            await self._enviar({"tipo": "controle", "gabriel": True})
        if self.espectadores:
            await self._enviar({"tipo": "ao_vivo", "ligar": True, "fps": self.fps})
        self._mudou()

    async def desconectar(self, conn: Conexao, motivo: str = "conexão caiu"):
        if conn is not self.conn:
            return
        did = (self.dispositivo or {}).get("id")
        self._falhar_pendentes("o PC desconectou")
        self.conn = None
        self.loop = None
        self.pausado = False
        self.motivo_pausa = None
        self.quadro = None
        audit.log("pc_saiu", dispositivo=did, motivo=motivo)
        if did:
            await asyncio.to_thread(tocar_dispositivo, did)
        self._mudou()

    def _falhar_pendentes(self, motivo: str):
        for fut in list(self.pendentes.values()):
            if not fut.done():
                fut.set_exception(ComputadorErro(f"ERRO: {motivo} no meio da ação. Confira a tela antes de repetir."))
        self.pendentes.clear()

    def _atualizar_tela(self, tela: Any):
        if not isinstance(tela, dict):
            return
        try:
            w, h = int(tela.get("largura") or 0), int(tela.get("altura") or 0)
        except (TypeError, ValueError):
            return
        if w > 0 and h > 0:
            self.largura, self.altura = w, h

    async def fechar_dispositivo(self, did: int, codigo: int = 4401, motivo: str = "PC desconectado da Eva"):
        if self.conn is not None and (self.dispositivo or {}).get("id") == did:
            conn = self.conn
            await self._no_loop(self._enviar({"tipo": "revogado", "motivo": motivo}))
            await self._no_loop(conn.fechar(codigo, motivo))
            await self._no_loop(self.desconectar(conn, motivo))

    # ---------- mensagens que chegam do agente ----------
    async def receber_texto(self, conn: Conexao, msg: dict):
        if conn is not self.conn:
            return
        self.ultimo_contato = time.time()
        tipo = msg.get("tipo")
        if tipo == "resp":
            fut = self.pendentes.pop(str(msg.get("id")), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
        elif tipo == "ping":
            await self._enviar({"tipo": "pong", "ts": time.time()})
        elif tipo == "estado":
            antes = (self.pausado, self.motivo_pausa)
            self.pausado = bool(msg.get("pausado"))
            self.motivo_pausa = str(msg.get("motivo") or "")[:120] or None
            self._atualizar_tela(msg.get("tela"))
            if (self.pausado, self.motivo_pausa) != antes:
                audit.log("pc_pausa", pausado=self.pausado, motivo=self.motivo_pausa)
                if self.pausado and msg.get("emergencia") and self.ao_emergencia:
                    try:
                        self.ao_emergencia(self.motivo_pausa or "pausa no PC")
                    except Exception as e:  # noqa: BLE001
                        log.warning("ao_emergencia falhou: %s", e)
                self._mudou()
        elif tipo == "desconectar":
            did = (self.dispositivo or {}).get("id")
            if did:
                await asyncio.to_thread(revogar_dispositivo, did, "desconectado pelo ícone do PC")
            await conn.fechar(4401, "desconectado")
            await self.desconectar(conn, "desconectado pelo PC")

    async def receber_bytes(self, conn: Conexao, dados: bytes):
        if conn is not self.conn or not dados:
            return
        self.ultimo_contato = time.time()
        if dados[:1] == b"F" and len(dados) > 3:
            self.quadro = bytes(dados[1:])
            self.quadro_versao += 1
            self.quadro_ts = time.time()
            for fn in list(self.espectadores.values()):
                try:
                    fn(self.quadro)
                except Exception as e:  # noqa: BLE001
                    log.debug("espectador falhou: %s", e)
            await self._enviar({"tipo": "frame_ok", "v": self.quadro_versao})

    # ---------- envio ----------
    async def _enviar(self, msg: dict):
        conn = self.conn
        if conn is None:
            raise ComputadorErro("ERRO: o PC do Gabriel não está conectado à Eva agora.")
        lock = self._envio or asyncio.Lock()
        async with lock:
            await conn.enviar_texto(json.dumps(msg, ensure_ascii=False))

    async def _no_loop(self, coro: Awaitable):
        """Roda no loop da conexão (os testes, e só eles, usam a Eva de outro loop/thread)."""
        loop = self.loop
        try:
            atual = asyncio.get_running_loop()
        except RuntimeError:
            atual = None
        if loop is None or atual is loop or loop.is_closed():
            return await coro
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))

    async def avisar(self, msg: dict) -> bool:
        """Manda uma mensagem sem esperar resposta. Falso se não há PC conectado."""
        if self.conn is None:
            return False
        try:
            await self._no_loop(self._enviar(msg))
            return True
        except Exception as e:  # noqa: BLE001
            log.info("não consegui avisar o PC: %s", e)
            return False

    async def pedir(self, op: str, args: dict | None = None, timeout: float = 30.0) -> dict:
        """Manda um comando e espera a resposta do agente. Erros viram ComputadorErro (mensagem em PT-BR)."""
        if kill_switch.off:
            raise ComputadorErro("ERRO: a Eva está desligada (botão de desligar). Nenhuma ação no computador.")
        if not self.conectado:
            raise ComputadorErro("ERRO: o PC do Gabriel não está conectado à Eva agora (o EVA-Agente está aberto? "
                                 "o PC está ligado e com internet?).")
        if self.pausado:
            raise ComputadorErro(f"ERRO: o Gabriel PAUSOU a Eva no PC dele ({self.motivo_pausa or 'pelo ícone'}). "
                                 "Não tente de novo: avise o que você ia fazer e espere ele retomar.")
        return await self._no_loop(self._pedir(op, args or {}, timeout))

    async def _pedir(self, op: str, args: dict, timeout: float) -> dict:
        pid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pendentes[pid] = fut
        try:
            await self._enviar({"tipo": "cmd", "id": pid, "op": op, "args": args})
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise ComputadorErro("ERRO: o PC demorou demais para responder. Confira a tela antes de tentar de novo.") from None
        except ComputadorErro:
            raise
        except Exception as e:  # noqa: BLE001 — conexão caiu no envio
            raise ComputadorErro(f"ERRO: não consegui falar com o PC ({type(e).__name__}).") from None
        finally:
            self.pendentes.pop(pid, None)
        if not resp.get("ok"):
            erro = str(resp.get("erro") or "falhou")[:800]
            if resp.get("codigo") == "pausado":
                self.pausado = True
                self.motivo_pausa = self.motivo_pausa or "pausado no PC"
                self._mudou()
                raise ComputadorErro(f"ERRO: o Gabriel PAUSOU a Eva no PC dele ({erro}). Não tente de novo: avise e "
                                     "espere ele retomar.")
            raise ComputadorErro(f"ERRO do PC: {erro}")
        dados = resp.get("dados") or {}
        self._atualizar_tela(dados.get("tela"))
        return dados

    # ---------- comandos especiais ----------
    async def parar(self, motivo: str = "A Eva foi desligada no app.") -> bool:
        return await self.avisar({"tipo": "parar", "motivo": motivo})

    async def liberar(self) -> bool:
        return await self.avisar({"tipo": "liberar"})

    async def controle(self, gabriel: bool) -> bool:
        return await self.avisar({"tipo": "controle", "gabriel": bool(gabriel)})

    # ---------- tela ao vivo ----------
    async def adicionar_espectador(self, fn: Callable[[bytes], None]) -> str:
        eid = uuid.uuid4().hex[:8]
        primeiro = not self.espectadores
        self.espectadores[eid] = fn
        if primeiro:
            await self.avisar({"tipo": "ao_vivo", "ligar": True, "fps": self.fps})
        if self.quadro and time.time() - self.quadro_ts < 30:
            try:
                fn(self.quadro)
            except Exception:  # noqa: BLE001
                pass
        return eid

    async def remover_espectador(self, eid: str):
        if self.espectadores.pop(eid, None) is not None and not self.espectadores:
            await self.avisar({"tipo": "ao_vivo", "ligar": False})


hub = PCHub()


def decodificar_imagem(dados: dict) -> bytes:
    b64 = dados.get("imagem_b64") or ""
    try:
        img = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError):
        img = b""
    if not img:
        raise ComputadorErro("ERRO: o PC mandou um print vazio (a tela pode estar bloqueada ou numa janela de "
                             "administrador/UAC).")
    return img
