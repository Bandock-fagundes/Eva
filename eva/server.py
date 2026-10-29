"""Servidor da E.V.A. (FastAPI): tela (PWA), API, WebSocket em tempo real, login por senha."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from . import audit, brain, config, lembretes, pc_hub
from .agent import desligar_tudo, ger, ligar
from .computador import ComputadorErro, pc
from .pc_hub import hub
from .config import HOST, ROOT, SENHA, USER_NAME, VERSAO
from .db import db
from .killswitch import kill_switch
from .lembretes import agendador
from .llm import cerebro
from .memory import CATEGORIAS, memory
from .permissions import VALIDOS, permissions
from .tools import TOOLS
from .tools.browser import fechar_navegador, nav

log = logging.getLogger("eva.servidor")

STATIC = ROOT / "static"
COOKIE = "eva_sessao"
COOKIE_IDADE = 60 * 60 * 24 * 365
LIVRES = {"/login", "/manifest.json", "/sw.js", "/icon-192.png", "/icon-512.png", "/api/saude", "/favicon.ico",
          "/api/pc/parear/confirmar"}   # o agente do PC troca o código de pareamento pelo token (tem freio próprio)
LOCAL = HOST in ("127.0.0.1", "localhost", "::1")

_assinador = URLSafeTimedSerializer(
    hashlib.sha256(f"eva-cookie:{config.SEGREDO}:{SENHA}".encode()).hexdigest(), salt="eva-sessao")
_falhas_ip: dict[str, deque] = defaultdict(deque)
_falhas_global: deque = deque()
JANELA_LOGIN = 600
MAX_FALHAS_IP = 8
MAX_FALHAS_GLOBAL = 40


# ---------------------------------------------------------------- autenticação

def criar_token() -> str:
    return _assinador.dumps({"u": USER_NAME.lower(), "t": int(time.time())})


def token_valido(token: str | None) -> bool:
    if not SENHA:
        return LOCAL
    if not token:
        return False
    try:
        _assinador.loads(token, max_age=COOKIE_IDADE)
        return True
    except BadSignature:
        return False


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()  # último = o que o proxy do Render viu
    return request.client.host if request.client else "?"


def _limitado(ip: str) -> bool:
    agora = time.time()
    for q in (_falhas_ip[ip], _falhas_global):
        while q and agora - q[0] > JANELA_LOGIN:
            q.popleft()
    return len(_falhas_ip[ip]) >= MAX_FALHAS_IP or len(_falhas_global) >= MAX_FALHAS_GLOBAL


def _registrar_falha(ip: str):
    agora = time.time()
    _falhas_ip[ip].append(agora)
    _falhas_global.append(agora)


def zerar_limites():
    _falhas_ip.clear()
    _falhas_global.clear()
    pc_hub.limite.zerar()


# ---------------------------------------------------------------- ciclo de vida

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    async def _avisar(ev):
        ger.transmitir(ev)
    agendador.ao_disparar = _avisar
    agendador.iniciar()
    if config.LLM_DESCOBRIR_MODELOS and cerebro.configurado:
        ger.em_fundo(cerebro.descobrir_modelos(), "descobrir_modelos")
    audit.log("servidor_iniciado", versao=VERSAO, banco="postgres" if db.pg else "sqlite")
    try:
        yield
    finally:
        await agendador.parar()
        await ger.encerrar()
        try:
            await fechar_navegador()
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="E.V.A.", version=VERSAO, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def porteiro(request: Request, call_next):
    """Só entra quem tem a senha. Sem EVA_SENHA, só funciona no próprio PC (127.0.0.1)."""
    path = request.url.path
    if not SENHA and not LOCAL and path != "/api/saude":
        return JSONResponse({"erro": "Configure a variável EVA_SENHA no servidor para proteger a Eva."}, status_code=503)
    if path in LIVRES or path.startswith("/static/") or token_valido(request.cookies.get(COOKIE)):
        resp = await call_next(request)
    elif path.startswith("/api"):
        resp = JSONResponse({"erro": "Não autenticado. Faça login."}, status_code=401)
    else:
        resp = RedirectResponse("/login", status_code=303)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if path.startswith("/api"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---------------------------------------------------------------- páginas

def _arquivo(nome: str, media_type: str | None = None, cache: str = "no-cache"):
    return FileResponse(STATIC / nome, media_type=media_type, headers={"Cache-Control": cache})


@app.get("/login")
async def login_page():
    return _arquivo("login.html")


@app.post("/login")
async def login(request: Request):
    ip = _ip(request)
    eh_json = "application/json" in request.headers.get("content-type", "")
    if _limitado(ip):
        audit.log("login_bloqueado", ip=ip)
        if eh_json:
            return JSONResponse({"ok": False, "erro": "Muitas tentativas. Espera uns minutos."}, status_code=429)
        return RedirectResponse("/login?erro=limite", status_code=303)
    try:
        corpo = await request.body()
        if eh_json:
            senha = str((json.loads(corpo or b"{}") or {}).get("senha", ""))
        else:
            senha = parse_qs(corpo.decode("utf-8", errors="replace")).get("senha", [""])[0]
    except (ValueError, AttributeError):
        senha = ""
    import hmac
    if SENHA and hmac.compare_digest(senha.encode(), SENHA.encode()):
        audit.log("login_ok", ip=ip)
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        r = JSONResponse({"ok": True}) if eh_json else RedirectResponse("/", status_code=303)
        r.set_cookie(COOKIE, criar_token(), max_age=COOKIE_IDADE, httponly=True, samesite="lax", secure=https, path="/")
        return r
    _registrar_falha(ip)
    audit.log("login_falhou", ip=ip)
    await asyncio.sleep(0.5)  # atrasa força bruta
    if eh_json:
        return JSONResponse({"ok": False, "erro": "Senha errada."}, status_code=401)
    return RedirectResponse("/login?erro=1", status_code=303)


@app.post("/logout")
async def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@app.get("/")
async def index():
    return _arquivo("index.html")


@app.get("/manifest.json")
async def manifest():
    return _arquivo("manifest.json", "application/manifest+json")


@app.get("/sw.js")
async def sw():
    return _arquivo("sw.js", "application/javascript")


@app.get("/icon-{n}.png")
async def icon(n: int):
    if n not in (192, 512):
        raise HTTPException(404)
    return _arquivo(f"icon-{n}.png", cache="public, max-age=86400")


@app.get("/favicon.ico")
async def favicon():
    return _arquivo("icon-192.png", "image/png", cache="public, max-age=86400")


@app.get("/static/{nome}")
async def estatico(nome: str):
    if "/" in nome or "\\" in nome or nome.startswith(".") or not (STATIC / nome).is_file():
        raise HTTPException(404)
    return _arquivo(nome)


# ---------------------------------------------------------------- API pública

@app.get("/api/saude")
async def saude(banco: int = 1):
    """Público. Use /api/saude?banco=0 em monitores (UptimeRobot) para não acordar o banco a cada ping."""
    if banco:
        banco = await asyncio.to_thread(db.ok, 300)
    else:
        banco = None
    return {"ok": banco is not False, "versao": VERSAO, "banco": banco, "chave_configurada": cerebro.configurado,
            "computador_configurado": pc.configurado}


# ---------------------------------------------------------------- API

@app.get("/api/estado")
async def estado():
    pc.checar_em_fundo()  # atualiza o status do computador sem travar a resposta
    e = ger.estado()
    llm = cerebro.estado()
    return {**e, "versao": VERSAO, "usuario": USER_NAME, "modelo": llm["modelo_em_uso"], "modelos": llm["cadeia"],
            "streaming": llm["streaming"], "embeddings": llm["embeddings"], "chave_configurada": cerebro.configurado,
            "banco": "postgres" if db.pg else "sqlite", "navegador_aberto": nav.aberto}  # inclui "computador"


@app.get("/api/historico")
async def historico(n: int = 80):
    return memory.history(max(1, min(500, n)))


@app.delete("/api/historico")
async def limpar_historico():
    memory.limpar_conversa()
    return {"ok": True}


class ChatIn(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)
    aprovar: Literal["negar", "permitir"] = "negar"
    timeout: float = Field(default=300, ge=5, le=900)


@app.post("/api/chat")
async def chat(c: ChatIn):
    """Roda uma tarefa e devolve a resposta (teste ponta a ponta). Aprovações: negadas por padrão."""
    try:
        t = await ger.enviar(c.texto.strip(), origem="api", politica=c.aprovar)
    except RuntimeError as e:
        return JSONResponse({"erro": str(e)}, status_code=409)
    try:
        await asyncio.wait_for(t.fim.wait(), timeout=c.timeout)
    except asyncio.TimeoutError:
        return JSONResponse({"erro": "A tarefa ainda está rodando (tempo esgotado aqui).", "tarefa": t.id,
                             "status": t.status, "texto_parcial": t.texto_parcial}, status_code=504)
    return {"resposta": t.resposta, "status": t.status, "modelo": t.modelo, "tarefa": t.id, "uso": t.uso,
            "eventos": [e for e in t.eventos if e.get("tipo") != "token"]}


@app.post("/api/parar")
async def parar():
    return {"ok": ger.parar("botão parar")}


class AprovacaoIn(BaseModel):
    id: str
    aprovado: bool


@app.post("/api/aprovacao")
async def aprovacao(a: AprovacaoIn):
    return {"ok": await ger.responder_aprovacao(a.id, a.aprovado)}


class RespostaIn(BaseModel):
    id: str
    texto: str = Field(max_length=5000)


@app.post("/api/resposta")
async def resposta(r: RespostaIn):
    return {"ok": await ger.responder_pergunta(r.id, r.texto)}


class Texto(BaseModel):
    texto: str = Field(max_length=50000)


@app.get("/api/persona")
async def get_persona():
    return {"texto": brain.persona_raw(), "padrao": not brain.persona_personalizada(), "usuario": USER_NAME}


@app.post("/api/persona")
async def set_persona(t: Texto):
    if not t.texto.strip():
        raise HTTPException(400, "A personalidade não pode ficar vazia.")
    db.set("persona", t.texto)
    audit.log("personalidade_alterada", tamanho=len(t.texto))
    return {"ok": True}


@app.post("/api/persona/restaurar")
async def restaurar_persona():
    db.delete_key("persona")
    audit.log("personalidade_restaurada")
    return {"ok": True, "texto": brain.persona_padrao()}


@app.post("/api/desligar")
async def desligar():
    n = await desligar_tudo("botão de desligar")
    return {"ok": True, "canceladas": n}


@app.post("/api/ligar")
async def ligar_api():
    ligar()
    return {"ok": True}


@app.get("/api/log")
async def log_api(pagina: int = 1, tamanho: int = 50, tipo: str | None = None, busca: str | None = None,
                  n: int | None = None):
    if n:
        tamanho = n
    return audit.pagina(pagina, tamanho, tipo or None, busca or None)


@app.get("/api/log/tipos")
async def log_tipos():
    return audit.tipos()


@app.get("/api/permissoes")
async def get_perms():
    return [{"ferramenta": nome, "rotulo": TOOLS[nome]["rotulo"], "valor": permissions.get(nome),
             "padrao": permissions.padrao(nome), "descricao": TOOLS[nome]["schema"]["function"]["description"],
             "sensivel": bool(TOOLS[nome].get("sensivel"))} for nome in TOOLS]


class Perm(BaseModel):
    ferramenta: str
    valor: str


@app.post("/api/permissoes")
async def set_perm(p: Perm):
    if p.ferramenta not in TOOLS:
        raise HTTPException(404, "Ferramenta desconhecida.")
    if p.valor not in VALIDOS:
        raise HTTPException(400, "Valor inválido (use permitir, perguntar ou bloquear).")
    permissions.set(p.ferramenta, p.valor)
    return {"ok": True}


@app.post("/api/permissoes/restaurar")
async def restaurar_perms():
    permissions.restaurar()
    return {"ok": True}


class MemoriaIn(BaseModel):
    conteudo: str = Field(min_length=1, max_length=2000)
    categoria: str = "geral"
    importancia: int = Field(default=3, ge=1, le=5)


class MemoriaEdit(BaseModel):
    conteudo: str | None = Field(default=None, max_length=2000)
    categoria: str | None = None
    importancia: int | None = Field(default=None, ge=1, le=5)


@app.get("/api/memorias")
async def memorias(categoria: str | None = None, busca: str | None = None):
    if busca:
        return await memory.search(busca, 50)
    return memory.all(categoria)


@app.get("/api/memorias/categorias")
async def categorias():
    return CATEGORIAS


@app.post("/api/memorias")
async def criar_memoria(m: MemoriaIn):
    mid, acao = await memory.lembrar(m.conteudo, m.categoria, m.importancia, origem="gabriel")
    if not mid:
        raise HTTPException(400, "Não salvei: parece dado sensível (senha, cartão, documento).")
    return {"ok": True, "id": mid, "acao": acao}


@app.put("/api/memorias/{mid}")
async def editar_memoria(mid: int, m: MemoriaEdit):
    if not await memory.editar(mid, m.conteudo, m.categoria, m.importancia):
        raise HTTPException(404, "Memória não encontrada.")
    return {"ok": True}


@app.delete("/api/memorias/{mid}")
async def apagar_memoria(mid: int):
    ok = memory.forget(mid)
    audit.log("memoria_apagada_pelo_usuario", id=mid, ok=ok)
    return {"ok": ok}


@app.get("/api/tela")
async def tela():
    """Último print: do computador da Eva (se houver um mais novo) ou do navegador antigo."""
    if pc.tela and (not nav.tela or pc.configurado):
        return Response(pc.tela, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Versao": f"pc-{pc.tela_versao}", "X-Fonte": "computador"})
    img = nav.tela
    if not img:
        return Response(status_code=204)
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Versao": str(nav.tela_versao)})


# ---------------------------------------------------------------- Computador da Eva

@app.get("/api/computador")
async def computador(verificar: bool = False):
    """Status do computador + links da tela ao vivo (com a senha da tela; só para quem está logado)."""
    if verificar and pc.configurado:
        await pc.disponivel(forcar=True)
    else:
        pc.checar_em_fundo()
    dispositivos = await asyncio.to_thread(pc_hub.listar_dispositivos)
    return {**pc.estado(), "vnc_url": pc.vnc_link(controle=False), "vnc_url_controle": pc.vnc_link(controle=True),
            "dispositivos": dispositivos, "download_url": config.PC_AGENTE_URL}


class ControleIn(BaseModel):
    gabriel: bool


@app.post("/api/computador/controle")
async def computador_controle(c: ControleIn):
    """'Assumir controle' (gabriel=true): a Eva para de mexer no computador até ele devolver."""
    if not pc.configurado:
        raise HTTPException(400, "O computador da Eva não está configurado.")
    pc.controle_gabriel = bool(c.gabriel)
    await hub.controle(pc.controle_gabriel)   # o ícone no PC mostra quem está no controle
    audit.log("computador_controle", quem=USER_NAME if c.gabriel else "Eva")
    ger.transmitir({"tipo": "computador", **pc.estado()})
    ger.transmitir(ger.estado_evento())
    return {"ok": True, **pc.estado()}


# ---------------------------------------------------------------- PC Windows do Gabriel (EVA-Agente)

@app.post("/api/pc/parear")
async def pc_parear():
    """'Conectar meu PC': gera um código de uso único (vale 10 minutos) para digitar no EVA-Agente."""
    d = await asyncio.to_thread(pc_hub.criar_codigo)
    ger.transmitir({"tipo": "computador", **pc.estado()})
    return {"ok": True, **d, "download_url": config.PC_AGENTE_URL}


@app.delete("/api/pc/parear")
async def pc_parear_cancelar():
    await asyncio.to_thread(pc_hub.cancelar_codigo)
    return {"ok": True}


class ParearIn(BaseModel):
    codigo: str = Field(min_length=4, max_length=20)
    nome: str = Field(default="", max_length=100)
    versao: str = Field(default="", max_length=40)


@app.post("/api/pc/parear/confirmar")
async def pc_parear_confirmar(p: ParearIn, request: Request):
    """Público (o agente ainda não tem token). Troca o código pelo token do dispositivo."""
    ip = _ip(request)
    if pc_hub.limite.bloqueado(ip):
        audit.log("pc_pareamento_bloqueado", ip=ip)
        return JSONResponse({"ok": False, "erro": "Muitas tentativas erradas. Espere 10 minutos e gere um código novo."},
                            status_code=429)
    r = await asyncio.to_thread(pc_hub.confirmar_codigo, p.codigo, p.nome, p.versao)
    if not r:
        pc_hub.limite.falhou(ip)
        audit.log("pc_pareamento_falhou", ip=ip)
        await asyncio.sleep(0.5)
        return JSONResponse({"ok": False, "erro": "Código errado ou vencido. Gere um novo no app da Eva (aba Tela → "
                                                  "Conectar meu PC)."}, status_code=401)
    ger.transmitir({"tipo": "computador", **pc.estado()})
    return {"ok": True, "token": r["token"], "dispositivo_id": r["id"], "nome": r["nome"], "usuario": USER_NAME}


@app.get("/api/pc/dispositivos")
async def pc_dispositivos():
    return await asyncio.to_thread(pc_hub.listar_dispositivos)


@app.delete("/api/pc/dispositivos/{did}")
async def pc_desconectar(did: int):
    """'Desconectar': revoga o token do PC (o agente precisa de um código novo para voltar)."""
    ok = await asyncio.to_thread(pc_hub.revogar_dispositivo, did)
    await hub.fechar_dispositivo(did, 4401, "Desconectado pelo app da Eva")
    ger.transmitir({"tipo": "computador", **pc.estado()})
    return {"ok": ok}


@app.get("/api/lembretes")
async def get_lembretes(todos: bool = False):
    return lembretes.listar(incluir_antigos=todos)


class LembreteIn(BaseModel):
    quando: str = Field(min_length=1, max_length=100)
    texto: str = Field(min_length=1, max_length=1000)


@app.post("/api/lembretes")
async def criar_lembrete(lm: LembreteIn):
    try:
        r = lembretes.criar(lm.quando, lm.texto)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "id": r["id"], "quando": r["quando"].isoformat(timespec="minutes")}


@app.delete("/api/lembretes/{lid}")
async def cancelar_lembrete(lid: int):
    return {"ok": lembretes.cancelar(lid)}


# ---------------------------------------------------------------- WebSocket

def _origem_ok(sock: WebSocket) -> bool:
    origem = sock.headers.get("origin")
    if not origem:
        return True  # apps nativos/scripts não mandam Origin
    host = sock.headers.get("x-forwarded-host") or sock.headers.get("host") or ""
    return urlparse(origem).netloc == host


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not token_valido(sock.cookies.get(COOKIE)) or not _origem_ok(sock):
        await sock.close(code=4401)
        return
    await sock.accept()
    fila: asyncio.Queue = asyncio.Queue(maxsize=5000)

    def ouvinte(ev: dict):
        try:
            fila.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    async def enviar():
        while True:
            ev = await fila.get()
            await sock.send_text(json.dumps(ev, ensure_ascii=False, default=str))

    async def pingar():
        while True:
            await asyncio.sleep(25)
            ouvinte({"tipo": "ping", "ts": time.time()})

    ger.adicionar_ouvinte(ouvinte)
    ouvinte(ger.estado_evento())
    t_enviar = asyncio.create_task(enviar())
    t_ping = asyncio.create_task(pingar())
    try:
        while True:
            bruto = await sock.receive_text()
            try:
                msg = json.loads(bruto)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            tipo = msg.get("tipo")
            if tipo == "ping":
                ouvinte({"tipo": "pong", "ts": time.time()})
            elif tipo == "mensagem":
                texto = str(msg.get("texto") or "").strip()[:20000]
                if texto:
                    try:
                        await ger.enviar(texto, origem="ws")
                    except RuntimeError as e:
                        ouvinte({"tipo": "erro", "texto": str(e)})
            elif tipo == "aprovacao":
                await ger.responder_aprovacao(str(msg.get("id")), bool(msg.get("aprovado")))
            elif tipo == "resposta":
                await ger.responder_pergunta(str(msg.get("id")), str(msg.get("texto") or ""))
            elif tipo == "parar":
                ger.parar("botão parar")
            elif tipo == "estado":
                ouvinte(ger.estado_evento())
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.info("WebSocket encerrado: %s", type(e).__name__)
    finally:
        ger.remover_ouvinte(ouvinte)
        t_enviar.cancel()
        t_ping.cancel()


# ---------------------------------------------------------------- WebSocket do PC Windows (EVA-Agente)

class _ConexaoWS(pc_hub.Conexao):
    def __init__(self, sock: WebSocket):
        self.sock = sock
        self.fechada = False

    async def enviar_texto(self, texto: str):
        await self.sock.send_text(texto)

    async def enviar_bytes(self, dados: bytes):
        await self.sock.send_bytes(dados)

    async def fechar(self, codigo: int = 1000, motivo: str = ""):
        if self.fechada:
            return
        self.fechada = True
        try:
            await self.sock.close(code=codigo, reason=(motivo or "")[:120])
        except Exception:  # noqa: BLE001
            pass


def _ip_ws(sock: WebSocket) -> str:
    xff = sock.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return sock.client.host if sock.client else "?"


@app.websocket("/ws/pc")
async def ws_pc(sock: WebSocket):
    """O EVA-Agente conecta aqui (conexão de SAÍDA do PC) com 'Authorization: Bearer <token do dispositivo>'."""
    ip = _ip_ws(sock)
    auth = sock.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    await sock.accept()
    if pc_hub.limite.bloqueado(ip):
        await sock.close(code=4429, reason="muitas tentativas")
        return
    disp = await asyncio.to_thread(pc_hub.dispositivo_por_token, token)
    if not disp:
        pc_hub.limite.falhou(ip)
        audit.log("pc_token_recusado", ip=ip)
        await asyncio.sleep(0.3)
        await sock.close(code=4401, reason="token invalido ou revogado")
        return
    try:
        ola = json.loads(await asyncio.wait_for(sock.receive_text(), timeout=20))
        if not isinstance(ola, dict) or ola.get("tipo") != "ola":
            raise ValueError("primeira mensagem precisa ser 'ola'")
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError, KeyError):
        try:
            await sock.close(code=4400, reason="protocolo")
        except Exception:  # noqa: BLE001
            pass
        return
    conn = _ConexaoWS(sock)
    await hub.conectar(conn, disp, ola)
    motivo = "conexão caiu"
    try:
        while not conn.fechada:
            msg = await asyncio.wait_for(sock.receive(), timeout=pc_hub.SEM_CONTATO_MAX)
            if msg.get("type") == "websocket.disconnect":
                motivo = f"agente saiu ({msg.get('code')})"
                break
            if msg.get("bytes") is not None:
                await hub.receber_bytes(conn, msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    d = json.loads(msg["text"])
                except ValueError:
                    continue
                if isinstance(d, dict):
                    await hub.receber_texto(conn, d)
    except asyncio.TimeoutError:
        motivo = "sem sinal do PC"
        await conn.fechar(4408, "sem sinal")
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.info("WebSocket do PC encerrado: %s", type(e).__name__)
    finally:
        await hub.desconectar(conn, motivo)


# ---------------------------------------------------------------- tela ao vivo do PC Windows (para o navegador)

async def _controle_remoto(msg: dict) -> str | None:
    """Cliques/teclas do Gabriel na imagem ao vivo (só com 'Assumir controle' ligado). Devolve erro ou None."""
    if not pc.controle_gabriel:
        return "Toque em “Assumir controle” antes de mexer no PC pela tela."
    if pc.backend() != "windows" or not hub.conectado:
        return "O PC não está conectado."
    if hub.largura and hub.altura:
        pc.largura, pc.altura = hub.largura, hub.altura
    tipo = msg.get("tipo")
    try:
        if tipo == "mouse":
            acao = str(msg.get("acao") or "clicar")
            if acao not in ("clicar", "duplo", "direito", "mover"):
                return "ação inválida"
            x, y = pc.para_pixels(msg.get("x"), msg.get("y"))
            await hub.pedir("mouse", {"acao": acao, "x": x, "y": y, "origem": "gabriel"}, timeout=10)
        elif tipo == "rolar":
            x, y = pc.para_pixels(msg.get("x", 500), msg.get("y", 500))
            dy = max(-10, min(10, int(msg.get("dy") or 0)))
            if dy:
                await hub.pedir("mouse", {"acao": "rolar", "x": x, "y": y, "dy": dy, "origem": "gabriel"}, timeout=10)
        elif tipo == "texto":
            texto = str(msg.get("texto") or "")[:2000]
            if texto:
                await hub.pedir("teclado", {"texto": texto, "origem": "gabriel"}, timeout=30)
        elif tipo == "tecla":
            teclas = str(msg.get("teclas") or "")[:60]
            if teclas:
                await hub.pedir("teclado", {"teclas": teclas, "origem": "gabriel"}, timeout=10)
        else:
            return None
    except ComputadorErro as e:
        return str(e).replace("ERRO: ", "").replace("ERRO do PC: ", "")
    except (TypeError, ValueError):
        return "dados inválidos"
    audit.log("pc_controle_remoto", tipo=tipo, acao=msg.get("acao"))
    return None


@app.websocket("/ws/tela")
async def ws_tela(sock: WebSocket):
    """A aba Tela abre esta conexão só enquanto está visível; o PC só manda quadros enquanto alguém assiste."""
    if not token_valido(sock.cookies.get(COOKIE)) or not _origem_ok(sock):
        await sock.close(code=4401)
        return
    await sock.accept()
    fila: asyncio.Queue = asyncio.Queue(maxsize=1)

    def novo_quadro(dados: bytes):
        if fila.full():  # internet lenta: descarta o quadro velho, manda só o mais novo
            try:
                fila.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            fila.put_nowait(("b", dados))
        except asyncio.QueueFull:
            pass

    trava = asyncio.Lock()

    async def enviar():
        while True:
            _, dados = await fila.get()
            async with trava:
                await sock.send_bytes(dados)

    eid = await hub.adicionar_espectador(novo_quadro)
    t_enviar = asyncio.create_task(enviar())
    try:
        while True:
            bruto = await sock.receive_text()
            try:
                msg = json.loads(bruto)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            erro = await _controle_remoto(msg)
            if erro:
                async with trava:
                    await sock.send_text(json.dumps({"tipo": "erro", "texto": erro}, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.info("WebSocket da tela encerrado: %s", type(e).__name__)
    finally:
        t_enviar.cancel()
        await hub.remover_espectador(eid)
