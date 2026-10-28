"""Servidor local (só no seu PC, 127.0.0.1). Tela em http://localhost:8000"""
import asyncio, hashlib, uuid
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from . import audit
from .agent import agent
from .config import ROOT, LLM_MODEL, SENHA, USER_NAME
from .brain import persona_raw
from .db import db
from .killswitch import kill_switch
from .memory import memory
from .permissions import permissions
from .tools import TOOLS
from .tools.browser import fechar_navegador, _state as browser_state

app = FastAPI(title="E.V.A.")
TASKS: set = set()
PENDENTES: dict = {}   # id -> Future (aprovações esperando o Gabriel)
SOCKETS: set = set()


STATIC = ROOT / "static"
TOKEN = hashlib.sha256(("eva:" + SENHA).encode()).hexdigest()
LIVRES = {"/login", "/manifest.json", "/sw.js", "/icon-192.png", "/icon-512.png"}


def autorizado(cookies) -> bool:
    return not SENHA or cookies.get("eva") == TOKEN


@app.middleware("http")
async def porteiro(request: Request, call_next):
    """Só entra quem tem a senha (quando EVA_SENHA está definida, ex.: na nuvem)."""
    if request.url.path in LIVRES or autorizado(request.cookies):
        return await call_next(request)
    if request.url.path.startswith("/api"):
        return Response(status_code=401)
    return RedirectResponse("/login")


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/login")
async def login(request: Request):
    from urllib.parse import parse_qs
    senha = parse_qs((await request.body()).decode()).get("senha", [""])[0]
    if SENHA and senha == SENHA:
        audit.log("login_ok", ip=request.client.host if request.client else "")
        r = RedirectResponse("/", status_code=303)
        r.set_cookie("eva", TOKEN, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
        return r
    audit.log("login_falhou", ip=request.client.host if request.client else "")
    return RedirectResponse("/login?erro=1", status_code=303)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.get("/icon-{n}.png")
async def icon(n: int):
    return FileResponse(STATIC / f"icon-{n}.png")


@app.get("/api/tela")
async def tela():
    img = browser_state.get("tela")
    return Response(img, media_type="image/jpeg") if img else Response(status_code=204)


@app.get("/api/historico")
async def historico():
    return memory.history(60)


@app.get("/api/persona")
async def get_persona():
    return {"texto": persona_raw(), "usuario": USER_NAME}


class Texto(BaseModel):
    texto: str


@app.post("/api/persona")
async def set_persona(t: Texto):
    db.set("persona", t.texto)
    audit.log("personalidade_alterada")
    return {"ok": True}


@app.get("/api/estado")
async def estado():
    return {"desligada": kill_switch.off, "modelo": LLM_MODEL, "trabalhando": len(TASKS)}


@app.post("/api/desligar")
async def desligar():
    kill_switch.kill()
    for t in list(TASKS):
        t.cancel()
    for f in PENDENTES.values():
        if not f.done():
            f.set_result(False)
    await fechar_navegador()
    return {"ok": True}


@app.post("/api/ligar")
async def ligar():
    kill_switch.revive()
    return {"ok": True}


@app.get("/api/log")
async def log(n: int = 200):
    return audit.tail(n)


@app.get("/api/permissoes")
async def get_perms():
    return {nome: permissions.get(nome) for nome in TOOLS}


class Perm(BaseModel):
    ferramenta: str
    valor: str


@app.post("/api/permissoes")
async def set_perm(p: Perm):
    permissions.set(p.ferramenta, p.valor)
    return {"ok": True}


@app.get("/api/memorias")
async def memorias():
    return memory.all()


@app.delete("/api/memorias/{mid}")
async def apagar_memoria(mid: int):
    audit.log("memoria_apagada_pelo_usuario", id=mid)
    return {"ok": memory.forget(mid)}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not autorizado(sock.cookies):
        await sock.close(code=4401)
        return
    await sock.accept()
    SOCKETS.add(sock)

    async def emit(ev):
        try:
            await sock.send_json(ev)
        except Exception:
            pass

    async def ask_approval(ferramenta, args):
        pid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        PENDENTES[pid] = fut
        await emit({"tipo": "aprovacao", "id": pid, "ferramenta": ferramenta, "args": args})
        try:
            return await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            return False
        finally:
            PENDENTES.pop(pid, None)

    async def rodar(texto):
        try:
            await agent.run(texto, emit, ask_approval)
        except asyncio.CancelledError:
            await emit({"tipo": "erro", "texto": "Tarefa interrompida."})
        except Exception as e:
            audit.log("erro", erro=str(e))
            await emit({"tipo": "erro", "texto": f"Deu ruim: {e}"})
        finally:
            await emit({"tipo": "fim"})

    try:
        while True:
            msg = await sock.receive_json()
            if msg.get("tipo") == "mensagem" and msg.get("texto", "").strip():
                t = asyncio.create_task(rodar(msg["texto"].strip()))
                TASKS.add(t)
                t.add_done_callback(TASKS.discard)
            elif msg.get("tipo") == "aprovacao":
                fut = PENDENTES.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(bool(msg.get("aprovado")))
    except WebSocketDisconnect:
        SOCKETS.discard(sock)
