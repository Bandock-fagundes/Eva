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

from . import audit, brain, config, lembretes
from .agent import desligar_tudo, ger, ligar
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
LIVRES = {"/login", "/manifest.json", "/sw.js", "/icon-192.png", "/icon-512.png", "/api/saude", "/favicon.ico"}
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
    return {"ok": banco is not False, "versao": VERSAO, "banco": banco, "chave_configurada": cerebro.configurado}


# ---------------------------------------------------------------- API

@app.get("/api/estado")
async def estado():
    e = ger.estado()
    llm = cerebro.estado()
    return {**e, "versao": VERSAO, "usuario": USER_NAME, "modelo": llm["modelo_em_uso"], "modelos": llm["cadeia"],
            "streaming": llm["streaming"], "embeddings": llm["embeddings"], "chave_configurada": cerebro.configurado,
            "banco": "postgres" if db.pg else "sqlite", "navegador_aberto": nav.aberto}


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
    img = nav.tela
    if not img:
        return Response(status_code=204)
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Versao": str(nav.tela_versao)})


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
