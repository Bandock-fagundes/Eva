"""Agente do PC FALSO (imita computador/agente/agente_pc.py) para testar as ferramentas computador_* sem X.

Grava cada chamada em `estado.chamadas` e responde como o agente de verdade: tamanho da tela, mouse, janela ativa.
"""
from __future__ import annotations

import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

TOKEN = "token-falso-do-pc-1234567890abcdef"
# JPEG mínimo de verdade (1x1 px) — a Eva só repassa os bytes
JPEG_1PX = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c"
    "20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f00000105010101010101000000"
    "00000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1"
    "082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a7374"
    "75767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1"
    "e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ffd9")


class Estado:
    def __init__(self):
        self.reset()

    def reset(self):
        self.chamadas: list[dict] = []
        self.largura, self.altura = 1280, 800
        self.mouse = {"x": 640, "y": 400}
        self.janela = "Nova aba - Chromium"
        self.saude_ok = True
        self.falhar_tela = False
        self.prints = 0

    def info(self):
        return {"tela": {"largura": self.largura, "altura": self.altura}, "mouse": dict(self.mouse), "janela": self.janela}


estado = Estado()
app = FastAPI()


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path != "/saude" and request.headers.get("authorization") != f"Bearer {TOKEN}":
        return JSONResponse({"detail": "Token inválido (confira EVA_PC_TOKEN)."}, status_code=401)
    return await call_next(request)


def _gravar(rota, corpo=None, params=None):
    estado.chamadas.append({"rota": rota, "corpo": corpo, "params": params, "ts": time.time()})


@app.get("/saude")
async def saude():
    if not estado.saude_ok:
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True, "versao": "1.0", "tela": {"largura": estado.largura, "altura": estado.altura}}


@app.get("/info")
async def info():
    _gravar("/info")
    return {"ok": True, **estado.info()}


@app.get("/tela")
async def tela(request: Request):
    _gravar("/tela", params=dict(request.query_params))
    if estado.falhar_tela:
        return JSONResponse({"detail": "Não consegui tirar print da tela"}, status_code=500)
    estado.prints += 1
    return Response(JPEG_1PX, media_type="image/jpeg", headers={
        "X-Tela-Largura": str(estado.largura), "X-Tela-Altura": str(estado.altura)})


@app.post("/mouse")
async def mouse(request: Request):
    corpo = await request.json()
    _gravar("/mouse", corpo)
    if corpo.get("x") is not None:
        estado.mouse = {"x": corpo.get("x2", corpo["x"]) if corpo["acao"] == "arrastar" else corpo["x"],
                        "y": corpo.get("y2", corpo["y"]) if corpo["acao"] == "arrastar" else corpo["y"]}
    return {"ok": True, **estado.info()}


@app.post("/teclado")
async def teclado(request: Request):
    _gravar("/teclado", await request.json())
    return {"ok": True, **estado.info()}


@app.post("/comando")
async def comando(request: Request):
    corpo = await request.json()
    _gravar("/comando", corpo)
    return {"ok": True, "codigo": 0, "tempo_esgotado": False, "saida": f"saida de: {corpo['cmd']}\n", "duracao_s": 0.01}


@app.post("/abrir")
async def abrir(request: Request):
    corpo = await request.json()
    _gravar("/abrir", corpo)
    if corpo.get("app") == "inexistente":
        return JSONResponse({"detail": "app desconhecido"}, status_code=400)
    return {"ok": True, "aberto": corpo.get("url") or corpo.get("app") or corpo.get("arquivo"), **estado.info()}


@app.post("/parar")
async def parar():
    _gravar("/parar")
    return {"ok": True, "comandos_parados": 0}


def iniciar(porta: int):
    import uvicorn
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="error", lifespan="off"))
    th = threading.Thread(target=servidor.run, daemon=True)
    th.start()
    for _ in range(200):
        if servidor.started:
            return servidor
        time.sleep(0.05)
    raise RuntimeError("agente do PC falso não subiu")
