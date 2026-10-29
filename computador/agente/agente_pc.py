"""Agente do PC — a "mão" da Eva dentro do computador na nuvem.

Um servidorzinho (FastAPI) que roda DENTRO do container do desktop, como o usuário da área de trabalho.
A Eva conversa com ele por HTTPS (através do Caddy) usando um token (EVA_PC_TOKEN):

    GET  /saude                 público: está vivo? (sem nada sensível)
    GET  /info                  tamanho da tela, posição do mouse, janela ativa
    GET  /tela                  print da tela (PNG/JPEG, com o cursor), ?escala= ?largura= ?qualidade= ?formato=
    POST /mouse                 {acao: mover|clicar|duplo|direito|meio|arrastar|rolar, x, y, x2, y2, dy, dx}
    POST /teclado               {texto: "..."} (unicode) ou {teclas: "ctrl+l"}
    POST /comando               {cmd, timeout} roda no terminal como o usuário da área de trabalho
    POST /abrir                 {url} | {app} | {arquivo}
    GET/POST /clipboard         área de transferência
    GET  /arquivos ?caminho=    lista uma pasta (dentro da pasta pessoal)
    GET  /arquivo  ?caminho=    baixa um arquivo
    POST /arquivo  ?caminho=    envia um arquivo (corpo = bytes do arquivo)
    POST /parar                 para comandos em andamento e solta mouse/teclas (botão de desligar da Eva)

Todas as respostas de ação devolvem {"tela": {"largura", "altura"}, "mouse": {...}, "janela": "..."}.
Limites: token obrigatório, bloqueio após muitas tentativas erradas, limite de requisições por segundo,
tamanho máximo de corpo/arquivo, saída de comando truncada e caminhos presos à pasta pessoal.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import logging
import os
import re
import shlex
import signal
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

VERSAO = "1.0"
log = logging.getLogger("agente_pc")

TOKEN = os.getenv("EVA_PC_TOKEN", "")
INSEGURO = os.getenv("EVA_PC_INSEGURO") == "1"          # só para testes locais
CASA = Path(os.getenv("EVA_PC_CASA", str(Path.home()))).resolve()
NAVEGADOR = os.getenv("EVA_PC_NAVEGADOR", "chromium")
UPLOAD_MB = int(os.getenv("EVA_PC_UPLOAD_MB", "50"))
DOWNLOAD_MB = int(os.getenv("EVA_PC_DOWNLOAD_MB", "200"))
SAIDA_MAX = int(os.getenv("EVA_PC_SAIDA_MAX", "10000"))
TEXTO_MAX = 5000
CORPO_MAX = 1024 * 1024                                  # 1 MB para JSON
REQ_POR_SEG = float(os.getenv("EVA_PC_REQ_POR_SEG", "25"))
REQ_RAJADA = float(os.getenv("EVA_PC_REQ_RAJADA", "60"))
MAX_FALHAS_AUTH = 20
JANELA_AUTH = 600

APPS = {
    "navegador": [NAVEGADOR],
    "chromium": [NAVEGADOR],
    "terminal": ["xfce4-terminal"],
    "arquivos": ["thunar"],
    "editor": ["mousepad"],
    "texto": ["libreoffice", "--writer"],
    "planilha": ["libreoffice", "--calc"],
    "apresentacao": ["libreoffice", "--impress"],
}

# nomes amigáveis de teclas -> nomes do X (xdotool)
TECLAS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "backspace": "BackSpace", "apagar": "BackSpace", "delete": "Delete", "del": "Delete",
    "espaco": "space", "espaço": "space", "space": "space",
    "cima": "Up", "up": "Up", "baixo": "Down", "down": "Down", "esquerda": "Left", "left": "Left",
    "direita": "Right", "right": "Right", "pageup": "Prior", "pgup": "Prior", "pagedown": "Next", "pgdn": "Next",
    "home": "Home", "inicio": "Home", "end": "End", "fim": "End", "insert": "Insert",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift", "super": "super", "win": "super",
    "windows": "super", "meta": "super", "printscreen": "Print", "menu": "Menu",
}
RE_TECLA = re.compile(r"^[A-Za-z0-9_]+$")


# ------------------------------------------------------------------ utilidades

def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("EVA_PC_TOKEN", "EVA_PC_VNC_SENHA")}
    env.setdefault("DISPLAY", ":1")
    env["HOME"] = str(CASA)
    return env


async def rodar(args: list[str], timeout: float = 10, entrada: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Roda um programa (sem shell) e devolve (código, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if entrada is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(entrada), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(504, f"'{args[0]}' demorou demais") from None
    return proc.returncode, out, err


async def xdotool(*args: str, timeout: float = 15) -> str:
    rc, out, err = await rodar(["xdotool", *args], timeout=timeout)
    if rc != 0:
        raise HTTPException(500, f"xdotool falhou: {err.decode(errors='replace')[:300]}")
    return out.decode(errors="replace")


def _soltar(args: list[str]):
    """Abre um programa gráfico sem esperar ele terminar."""
    log_arq = CASA / ".cache" / "eva-abrir.log"
    log_arq.parent.mkdir(parents=True, exist_ok=True)
    with open(log_arq, "ab") as f:
        import subprocess
        subprocess.Popen(args, stdout=f, stderr=f, stdin=subprocess.DEVNULL, env=_env(), cwd=str(CASA),
                         start_new_session=True)


async def tamanho_tela() -> tuple[int, int]:
    try:
        out = await xdotool("getdisplaygeometry", timeout=5)
        w, h = out.split()[:2]
        return int(w), int(h)
    except (HTTPException, ValueError):
        return 0, 0


async def posicao_mouse() -> dict:
    try:
        out = await xdotool("getmouselocation", "--shell", timeout=5)
        d = dict(linha.split("=", 1) for linha in out.split() if "=" in linha)
        return {"x": int(d.get("X", 0)), "y": int(d.get("Y", 0))}
    except (HTTPException, ValueError):
        return {"x": 0, "y": 0}


async def janela_ativa() -> str:
    """Título da janela em foco (ex.: "Entrar - Google Chrome"). Vazio se não der pra saber."""
    for args in (["xdotool", "getactivewindow", "getwindowname"], ["xdotool", "getwindowfocus", "-f", "getwindowname"]):
        try:
            rc, out, _ = await rodar(args, timeout=5)
        except HTTPException:
            continue
        nome = out.decode(errors="replace").strip()
        if rc == 0 and nome:
            return nome[:300]
    return ""


async def info() -> dict:
    (w, h), mouse, janela = await asyncio.gather(tamanho_tela(), posicao_mouse(), janela_ativa())
    return {"tela": {"largura": w, "altura": h}, "mouse": mouse, "janela": janela}


def caminho_seguro(rel: str, deve_existir: bool = False) -> Path:
    """Resolve um caminho relativo à pasta pessoal. Nada de escapar dela (nem por link simbólico)."""
    rel = (rel or ".").strip()
    if "\x00" in rel:
        raise HTTPException(400, "caminho inválido")
    p = Path(rel)
    p = (p if p.is_absolute() else CASA / p).resolve()
    if p != CASA and CASA not in p.parents:
        raise HTTPException(400, "Só posso mexer em arquivos dentro da pasta pessoal.")
    if deve_existir and not p.exists():
        raise HTTPException(404, "Arquivo ou pasta não encontrado.")
    return p


def truncar(texto: str, limite: int | None = None) -> str:
    limite = limite or SAIDA_MAX
    if len(texto) <= limite:
        return texto
    cab = int(limite * 0.4)
    cauda = limite - cab
    return texto[:cab] + f"\n… ({len(texto) - limite} caracteres omitidos) …\n" + texto[-cauda:]


def _valida_ponto(x, y, w, h, nome="x/y"):
    if x is None or y is None:
        raise HTTPException(400, f"faltou {nome}")
    if w and h and not (0 <= x < w and 0 <= y < h):
        raise HTTPException(400, f"{nome} fora da tela ({x},{y}); a tela tem {w}x{h}")


def normalizar_teclas(teclas: str | list[str]) -> list[str]:
    """'ctrl+l' -> ['ctrl+l'];  'ctrl+a Delete' -> ['ctrl+a', 'Delete'];  'Enter' -> ['Return']."""
    if isinstance(teclas, list):
        combos = [str(c) for c in teclas]
    else:
        combos = str(teclas).replace(",", " ").split()
    saida = []
    for combo in combos[:30]:
        partes = []
        for p in combo.split("+"):
            p = p.strip()
            if not p:
                continue
            q = TECLAS.get(p.lower())
            if q is None:
                if re.fullmatch(r"[fF]([1-9]|1[0-2])", p):
                    q = p.upper()
                elif len(p) == 1:
                    q = p.lower() if p.isalpha() else p
                else:
                    q = p
            if not RE_TECLA.match(q) and len(q) != 1:
                raise HTTPException(400, f"tecla inválida: {p!r}")
            partes.append(q)
        if partes:
            saida.append("+".join(partes))
    if not saida:
        raise HTTPException(400, "nenhuma tecla informada")
    return saida


# ------------------------------------------------------------------ captura de tela

def _desenhar_cursor(img, x: int, y: int):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    seta = [(x, y), (x, y + 18), (x + 5, y + 14), (x + 9, y + 22), (x + 12, y + 20), (x + 8, y + 13), (x + 14, y + 13)]
    d.polygon(seta, fill=(255, 255, 255), outline=(0, 0, 0))


async def capturar_png() -> tuple[bytes, bool]:
    """Devolve (png, cursor_ja_incluido). Tenta scrot (com cursor) e cai para ImageMagick."""
    pasta = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else tempfile.gettempdir()
    arq = os.path.join(pasta, f"eva-tela-{os.getpid()}-{time.monotonic_ns()}.png")
    try:
        rc, _, err = await rodar(["scrot", "-o", "-p", arq], timeout=10)
        if rc == 0 and os.path.exists(arq):
            with open(arq, "rb") as f:
                return f.read(), True
        log.info("scrot falhou (%s); usando import", err[:200])
    except (FileNotFoundError, HTTPException):
        pass
    finally:
        try:
            os.unlink(arq)
        except OSError:
            pass
    for cmd in (["import", "-window", "root", "png:-"], ["magick", "import", "-window", "root", "png:-"]):
        try:
            rc, out, _ = await rodar(cmd, timeout=15)
            if rc == 0 and out:
                return out, False
        except FileNotFoundError:
            continue
    raise HTTPException(500, "Não consegui tirar print da tela (scrot/import falharam).")


def _processar(png: bytes, cursor: dict | None, escala: float, largura: int | None, formato: str,
               qualidade: int) -> tuple[bytes, int, int, int, int]:
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w0, h0 = img.size
    if cursor is not None:
        _desenhar_cursor(img, cursor["x"], cursor["y"])
    fator = escala
    if largura:
        fator = min(fator, largura / w0)
    fator = max(0.1, min(1.0, fator))
    if fator < 0.999:
        img = img.resize((max(1, round(w0 * fator)), max(1, round(h0 * fator))), Image.LANCZOS)
    buf = io.BytesIO()
    if formato == "png":
        img.save(buf, "PNG", optimize=False)
    else:
        img.save(buf, "JPEG", quality=qualidade, optimize=True)
    return buf.getvalue(), w0, h0, img.size[0], img.size[1]


# ------------------------------------------------------------------ app, autenticação e limites

app = FastAPI(title="Agente do PC da Eva", version=VERSAO, docs_url=None, redoc_url=None, openapi_url=None)
_falhas: dict[str, deque] = defaultdict(deque)
_balde = {"fichas": REQ_RAJADA, "ts": time.monotonic()}
_entrada = asyncio.Lock()           # uma ação de mouse/teclado por vez
_capturas = asyncio.Semaphore(2)    # no máximo 2 prints ao mesmo tempo
_comandos: set[asyncio.subprocess.Process] = set()


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()  # o último é o que o Caddy viu (o resto o cliente pode inventar)
    return request.client.host if request.client else "?"


def _token_ok(request: Request) -> bool:
    if INSEGURO and not TOKEN:
        return True
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    return bool(TOKEN) and hmac.compare_digest(auth[7:].strip().encode(), TOKEN.encode())


def _limite_ok() -> bool:
    agora = time.monotonic()
    _balde["fichas"] = min(REQ_RAJADA, _balde["fichas"] + (agora - _balde["ts"]) * REQ_POR_SEG)
    _balde["ts"] = agora
    if _balde["fichas"] < 1:
        return False
    _balde["fichas"] -= 1
    return True


def zerar_limites():
    _falhas.clear()
    _balde.update(fichas=REQ_RAJADA, ts=time.monotonic())


@app.middleware("http")
async def porteiro(request: Request, call_next):
    path = request.url.path
    if path == "/saude":
        return await call_next(request)
    ip = _ip(request)
    fal = _falhas[ip]
    agora = time.time()
    while fal and agora - fal[0] > JANELA_AUTH:
        fal.popleft()
    if len(fal) >= MAX_FALHAS_AUTH:
        return JSONResponse({"detail": "Muitas tentativas com token errado. Espere alguns minutos."}, status_code=429)
    if not _token_ok(request):
        fal.append(agora)
        await asyncio.sleep(0.3)
        return JSONResponse({"detail": "Token inválido (confira EVA_PC_TOKEN)."}, status_code=401)
    if not _limite_ok():
        return JSONResponse({"detail": "Muitas ações seguidas; vai com calma."}, status_code=429)
    limite = (UPLOAD_MB * 1024 * 1024) if (path == "/arquivo" and request.method == "POST") else CORPO_MAX
    try:
        tam = int(request.headers.get("content-length") or 0)
    except ValueError:
        tam = 0
    if tam > limite:
        return JSONResponse({"detail": f"Corpo grande demais (máx. {limite // 1024} KB)."}, status_code=413)
    return await call_next(request)


# ------------------------------------------------------------------ rotas

@app.get("/saude")
async def saude():
    w, h = await tamanho_tela()
    return {"ok": w > 0, "versao": VERSAO, "tela": {"largura": w, "altura": h}}


@app.get("/info")
async def rota_info():
    return {"ok": True, "versao": VERSAO, **(await info())}


@app.get("/tela")
async def tela(escala: float = Query(1.0, gt=0, le=1), largura: int | None = Query(None, ge=100, le=4000),
               qualidade: int = Query(70, ge=10, le=95), formato: Literal["jpeg", "jpg", "png"] = "jpeg",
               cursor: bool = True):
    async with _capturas:
        png, tem_cursor = await capturar_png()
        mouse = await posicao_mouse()
        desenhar = mouse if (cursor and not tem_cursor) else None
        dados, w0, h0, w1, h1 = await asyncio.to_thread(
            _processar, png, desenhar, escala, largura, "png" if formato == "png" else "jpeg", qualidade)
    return Response(dados, media_type="image/png" if formato == "png" else "image/jpeg", headers={
        "Cache-Control": "no-store", "X-Tela-Largura": str(w0), "X-Tela-Altura": str(h0),
        "X-Imagem-Largura": str(w1), "X-Imagem-Altura": str(h1),
        "X-Mouse-X": str(mouse["x"]), "X-Mouse-Y": str(mouse["y"])})


class MouseIn(BaseModel):
    acao: Literal["mover", "clicar", "duplo", "direito", "meio", "arrastar", "rolar"]
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    dy: int = 0      # rolar: positivo = para baixo (em "cliques" da rodinha)
    dx: int = 0      # rolar: positivo = para a direita


@app.post("/mouse")
async def mouse(m: MouseIn):
    w, h = await tamanho_tela()
    async with _entrada:
        if m.acao == "rolar":
            if m.x is not None and m.y is not None:
                _valida_ponto(m.x, m.y, w, h)
                await xdotool("mousemove", str(m.x), str(m.y))
            dy, dx = max(-30, min(30, m.dy)), max(-30, min(30, m.dx))
            if not dy and not dx:
                raise HTTPException(400, "rolar precisa de dy ou dx")
            if dy:
                await xdotool("click", "--repeat", str(abs(dy)), "--delay", "40", "5" if dy > 0 else "4")
            if dx:
                await xdotool("click", "--repeat", str(abs(dx)), "--delay", "40", "7" if dx > 0 else "6")
        else:
            _valida_ponto(m.x, m.y, w, h)
            await xdotool("mousemove", str(m.x), str(m.y))
            if m.acao == "clicar":
                await xdotool("click", "1")
            elif m.acao == "duplo":
                await xdotool("click", "--repeat", "2", "--delay", "90", "1")
            elif m.acao == "direito":
                await xdotool("click", "3")
            elif m.acao == "meio":
                await xdotool("click", "2")
            elif m.acao == "arrastar":
                _valida_ponto(m.x2, m.y2, w, h, "x2/y2")
                await xdotool("mousedown", "1")
                try:
                    passos = 12
                    for i in range(1, passos + 1):
                        xi = round(m.x + (m.x2 - m.x) * i / passos)
                        yi = round(m.y + (m.y2 - m.y) * i / passos)
                        await xdotool("mousemove", str(xi), str(yi))
                        await asyncio.sleep(0.02)
                finally:
                    await xdotool("mouseup", "1")
    return {"ok": True, **(await info())}


class TecladoIn(BaseModel):
    texto: str | None = Field(default=None, max_length=TEXTO_MAX)
    teclas: str | list[str] | None = None
    modo: Literal["auto", "digitar", "colar"] = "auto"


RE_TERMINAL = re.compile(r"terminal|bash|zsh|@[\w.-]+:", re.I)


async def _colar(texto: str, janela: str):
    await _set_clipboard(texto)
    await asyncio.sleep(0.05)
    await xdotool("key", "--clearmodifiers", "ctrl+shift+v" if RE_TERMINAL.search(janela) else "ctrl+v")
    await asyncio.sleep(0.15)


async def digitar(texto: str, modo: str = "auto"):
    """Digita qualquer texto. O xdotool às vezes "come" letras fora do ASCII (ç, ✓, emoji) dependendo do
    teclado; por isso trechos ASCII são digitados tecla a tecla e os outros são colados pela área de
    transferência (que é restaurada no fim). modo=colar cola tudo de uma vez (bom para textos longos)."""
    janela = await janela_ativa()
    try:
        rc, antigo, _ = await rodar(["xclip", "-selection", "clipboard", "-o"], timeout=3)
    except (HTTPException, FileNotFoundError):
        rc, antigo = 1, b""
    usou_clip = False
    try:
        if modo == "colar" or (modo == "auto" and len(texto) > 400):
            await _colar(texto, janela)
            usou_clip = True
            return
        for trecho in re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]+", texto):
            if ord(trecho[0]) < 128 and modo != "colar":
                for i in range(0, len(trecho), 200):
                    pedaco = trecho[i:i + 200]
                    await xdotool("type", "--clearmodifiers", "--delay", "12", "--", pedaco,
                                  timeout=30 + len(pedaco) * 0.05)
            else:
                await _colar(trecho, janela)
                usou_clip = True
    finally:
        if usou_clip and rc == 0:
            await _set_clipboard(antigo.decode(errors="replace"))


@app.post("/teclado")
async def teclado(k: TecladoIn):
    if not k.texto and not k.teclas:
        raise HTTPException(400, "mande 'texto' ou 'teclas'")
    async with _entrada:
        if k.texto:
            await digitar(k.texto.replace("\r\n", "\n"), k.modo)
        if k.teclas:
            combos = normalizar_teclas(k.teclas)
            await xdotool("key", "--clearmodifiers", "--delay", "60", *combos)
    return {"ok": True, **(await info())}


class ComandoIn(BaseModel):
    cmd: str = Field(min_length=1, max_length=8000)
    timeout: int = Field(default=60, ge=1, le=300)


def _matar(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


@app.post("/comando")
async def comando(c: ComandoIn):
    inicio = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash", "-lc", c.cmd, cwd=str(CASA), env=_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    _comandos.add(proc)
    estourou = False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=c.timeout)
    except asyncio.TimeoutError:
        estourou = True
        _matar(proc)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:  # noqa: BLE001
            out = b""
    except asyncio.CancelledError:
        _matar(proc)
        raise
    finally:
        _comandos.discard(proc)
    return {"ok": not estourou and proc.returncode == 0, "codigo": proc.returncode, "tempo_esgotado": estourou,
            "saida": truncar(out.decode(errors="replace")), "duracao_s": round(time.monotonic() - inicio, 2)}


class AbrirIn(BaseModel):
    url: str | None = Field(default=None, max_length=4000)
    app: str | None = Field(default=None, max_length=60)
    arquivo: str | None = Field(default=None, max_length=1000)


@app.post("/abrir")
async def abrir(a: AbrirIn):
    async with _entrada:
        if a.url:
            url = a.url.strip()
            if not re.match(r"^https?://", url, re.I):
                if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", url, re.I):
                    url = "https://" + url
                else:
                    raise HTTPException(400, "URL precisa começar com http:// ou https://")
            _soltar([NAVEGADOR, url])
            aberto = url
        elif a.app:
            chave = a.app.strip().lower()
            if chave not in APPS:
                raise HTTPException(400, f"app desconhecido. Opções: {', '.join(APPS)}")
            _soltar(APPS[chave])
            aberto = chave
        elif a.arquivo:
            p = caminho_seguro(a.arquivo, deve_existir=True)
            _soltar(["xdg-open", str(p)])
            aberto = str(p)
        else:
            raise HTTPException(400, "mande url, app ou arquivo")
    return {"ok": True, "aberto": aberto, **(await info())}


class ClipIn(BaseModel):
    texto: str = Field(max_length=200000)


async def _set_clipboard(texto: str):
    proc = await asyncio.create_subprocess_exec(
        "xclip", "-selection", "clipboard", "-i", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=_env())
    await asyncio.wait_for(proc.communicate(texto.encode()), timeout=5)


@app.get("/clipboard")
async def get_clipboard():
    rc, out, _ = await rodar(["xclip", "-selection", "clipboard", "-o"], timeout=5)
    return {"ok": True, "texto": truncar(out.decode(errors="replace"), 50000) if rc == 0 else ""}


@app.post("/clipboard")
async def set_clipboard(c: ClipIn):
    await _set_clipboard(c.texto)
    return {"ok": True}


@app.get("/arquivos")
async def listar(caminho: str = "."):
    p = caminho_seguro(caminho, deve_existir=True)
    if not p.is_dir():
        raise HTTPException(400, "não é uma pasta")
    itens = []
    for f in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:500]:
        try:
            st = f.stat()
        except OSError:
            continue
        itens.append({"nome": f.name, "pasta": f.is_dir(), "tamanho": st.st_size, "modificado": int(st.st_mtime)})
    return {"ok": True, "caminho": str(p.relative_to(CASA)) if p != CASA else ".", "itens": itens}


@app.get("/arquivo")
async def baixar(caminho: str):
    p = caminho_seguro(caminho, deve_existir=True)
    if not p.is_file():
        raise HTTPException(400, "não é um arquivo")
    if p.stat().st_size > DOWNLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"arquivo maior que {DOWNLOAD_MB} MB")
    return FileResponse(p, filename=p.name)


@app.post("/arquivo")
async def enviar(request: Request, caminho: str):
    p = caminho_seguro(caminho)
    if p == CASA or p.is_dir():
        raise HTTPException(400, "informe o caminho do arquivo (pasta/nome.ext)")
    p.parent.mkdir(parents=True, exist_ok=True)
    limite = UPLOAD_MB * 1024 * 1024
    tmp = p.with_name(f".{p.name}.parcial")
    total = 0
    try:
        with open(tmp, "wb") as f:
            async for pedaco in request.stream():
                total += len(pedaco)
                if total > limite:
                    raise HTTPException(413, f"arquivo maior que {UPLOAD_MB} MB")
                f.write(pedaco)
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"ok": True, "caminho": str(p.relative_to(CASA)), "tamanho": total}


@app.post("/parar")
async def parar():
    n = 0
    for proc in list(_comandos):
        _matar(proc)
        n += 1
    try:
        await rodar(["xdotool", "mouseup", "1", "mouseup", "2", "mouseup", "3",
                     "keyup", "ctrl", "alt", "shift", "super"], timeout=5)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "comandos_parados": n}


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not INSEGURO and len(TOKEN) < 24:
        raise SystemExit("EVA_PC_TOKEN ausente ou curto demais (mínimo 24 caracteres). Recusando ligar sem proteção.")
    host = os.getenv("EVA_PC_HOST", "0.0.0.0")
    porta = int(os.getenv("EVA_PC_PORTA", "8765"))
    log.info("Agente do PC v%s na porta %s (pasta pessoal: %s)", VERSAO, porta, CASA)
    uvicorn.run(app, host=host, port=porta, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
