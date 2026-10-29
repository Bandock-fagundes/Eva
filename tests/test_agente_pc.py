"""Agente do PC (computador/agente/agente_pc.py): autenticação, limites, montagem dos comandos do xdotool,
teclas, comandos de terminal, arquivos presos à pasta pessoal e print da tela. O X é simulado; o bash é real."""
import asyncio
import importlib
import io
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PIL = pytest.importorskip("PIL")
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "computador" / "agente"))
TOKEN = "tok-de-teste-do-agente-1234567890abcdef"


@pytest.fixture
def ag(tmp_path, monkeypatch):
    import agente_pc
    agente_pc = importlib.reload(agente_pc)
    casa = tmp_path / "casa"
    casa.mkdir()
    monkeypatch.setattr(agente_pc, "TOKEN", TOKEN)
    monkeypatch.setattr(agente_pc, "CASA", casa.resolve())
    chamadas = []

    async def rodar_falso(args, timeout=10, entrada=None):
        chamadas.append(list(args))
        a = " ".join(args)
        if "getdisplaygeometry" in a:
            return 0, b"1280 800\n", b""
        if "getmouselocation" in a:
            return 0, b"X=10\nY=20\nSCREEN=0\nWINDOW=123\n", b""
        if "getwindowname" in a:
            return 0, b"Janela Teste - Chromium\n", b""
        if args[:2] == ["xclip", "-selection"] and "-o" in args:
            return 0, b"antigo", b""
        return 0, b"", b""

    abertos, colados, clip = [], [], []
    monkeypatch.setattr(agente_pc, "rodar", rodar_falso)
    monkeypatch.setattr(agente_pc, "_soltar", lambda args: abertos.append(list(args)))

    async def set_clip(texto):
        clip.append(texto)

    async def colar(texto, janela):
        colados.append(texto)

    monkeypatch.setattr(agente_pc, "_set_clipboard", set_clip)
    monkeypatch.setattr(agente_pc, "_colar", colar)

    async def capturar():
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1280, 800), (20, 30, 40)).save(buf, "PNG")
        return buf.getvalue(), False

    monkeypatch.setattr(agente_pc, "capturar_png", capturar)
    agente_pc.zerar_limites()
    agente_pc._teste = {"chamadas": chamadas, "abertos": abertos, "colados": colados, "clip": clip, "casa": casa}
    return agente_pc


@pytest.fixture
def cli(ag):
    c = TestClient(ag.app)
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c


def _xdo(ag):
    return [c[1:] for c in ag._teste["chamadas"] if c[0] == "xdotool" and "getdisplaygeometry" not in c
            and "getmouselocation" not in c and "getwindowname" not in c]


def test_saude_publica_e_token(ag):
    c = TestClient(ag.app)
    assert c.get("/saude").json() == {"ok": True, "versao": ag.VERSAO, "tela": {"largura": 1280, "altura": 800}}
    assert c.get("/info").status_code == 401
    assert c.get("/info", headers={"Authorization": "Bearer errado"}).status_code == 401
    r = c.get("/info", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert r["tela"] == {"largura": 1280, "altura": 800} and r["mouse"] == {"x": 10, "y": 20}
    assert r["janela"] == "Janela Teste - Chromium"


def test_bloqueio_apos_muitos_erros(ag, monkeypatch):
    monkeypatch.setattr(ag, "MAX_FALHAS_AUTH", 3)
    monkeypatch.setattr(asyncio, "sleep", _sem_espera)
    c = TestClient(ag.app)
    for _ in range(3):
        assert c.get("/info", headers={"Authorization": "Bearer x"}).status_code == 401
    assert c.get("/info", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429  # até com o certo


async def _sem_espera(*_a, **_k):
    return None


def test_limite_de_requisicoes_e_tamanho(ag, cli, monkeypatch):
    monkeypatch.setattr(ag, "REQ_POR_SEG", 0.0)
    ag._balde.update(fichas=2)
    assert cli.get("/info").status_code == 200
    assert cli.get("/info").status_code == 200
    assert cli.get("/info").status_code == 429
    ag.zerar_limites()
    monkeypatch.setattr(ag, "REQ_POR_SEG", 100.0)
    r = cli.post("/teclado", content=b"x" * (ag.CORPO_MAX + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_mouse(ag, cli):
    assert cli.post("/mouse", json={"acao": "clicar", "x": 100, "y": 200}).status_code == 200
    assert _xdo(ag)[-2:] == [["mousemove", "100", "200"], ["click", "1"]]
    cli.post("/mouse", json={"acao": "duplo", "x": 1, "y": 2})
    assert _xdo(ag)[-1] == ["click", "--repeat", "2", "--delay", "90", "1"]
    cli.post("/mouse", json={"acao": "direito", "x": 1, "y": 2})
    assert _xdo(ag)[-1] == ["click", "3"]
    cli.post("/mouse", json={"acao": "rolar", "x": 640, "y": 400, "dy": -3})
    assert _xdo(ag)[-1] == ["click", "--repeat", "3", "--delay", "40", "4"]
    cli.post("/mouse", json={"acao": "rolar", "dx": 2})
    assert _xdo(ag)[-1] == ["click", "--repeat", "2", "--delay", "40", "7"]
    r = cli.post("/mouse", json={"acao": "arrastar", "x": 0, "y": 0, "x2": 120, "y2": 60}).json()
    seq = _xdo(ag)
    assert ["mousedown", "1"] in seq and seq[-1] == ["mouseup", "1"] and ["mousemove", "120", "60"] in seq
    assert r["tela"] == {"largura": 1280, "altura": 800}
    assert cli.post("/mouse", json={"acao": "clicar", "x": 1280, "y": 10}).status_code == 400   # fora da tela
    assert cli.post("/mouse", json={"acao": "clicar"}).status_code == 400
    assert cli.post("/mouse", json={"acao": "rolar"}).status_code == 400
    assert cli.post("/mouse", json={"acao": "voar", "x": 1, "y": 1}).status_code == 422


def test_teclas_normalizadas(ag):
    n = ag.normalizar_teclas
    assert n("ctrl+l") == ["ctrl+l"]
    assert n("Enter") == ["Return"]
    assert n("ctrl+a delete") == ["ctrl+a", "Delete"]
    assert n("ctrl+shift+T") == ["ctrl+shift+t"]
    assert n("alt+tab, f5") == ["alt+Tab", "F5"]
    assert n(["win", "esc"]) == ["super", "Escape"]
    from fastapi import HTTPException
    for ruim in ("", "ctrl+$(rm)", "a;b"):
        with pytest.raises(HTTPException):
            n(ruim)


def test_teclado(ag, cli):
    cli.post("/teclado", json={"teclas": "ctrl+l Enter"})
    assert _xdo(ag)[-1] == ["key", "--clearmodifiers", "--delay", "60", "ctrl+l", "Return"]
    cli.post("/teclado", json={"texto": "Olá ação ✓ ok"})
    tipos = [c for c in _xdo(ag) if c[0] == "type"]
    assert [t[-1] for t in tipos] == ["Ol", " a", "o ", " ok"]
    # trechos ASCII são digitados; trechos com acento/emoji vão pela área de transferência
    assert ag._teste["colados"] == ["á", "çã", "✓"]
    assert ag._teste["clip"][-1] == "antigo"          # área de transferência restaurada
    cli.post("/teclado", json={"texto": "x" * 450})
    assert ag._teste["colados"][-1] == "x" * 450      # texto longo: cola de uma vez
    assert cli.post("/teclado", json={}).status_code == 400
    assert cli.post("/teclado", json={"texto": "x" * 5001}).status_code == 422


def test_comando_real(ag, cli, monkeypatch):
    r = cli.post("/comando", json={"cmd": "echo oi; pwd; printenv EVA_PC_TOKEN || echo sem-token"}).json()
    assert r["ok"] and r["codigo"] == 0
    linhas = r["saida"].split()
    assert linhas[0] == "oi" and linhas[1] == str(ag._teste["casa"].resolve()) and linhas[2] == "sem-token"
    r = cli.post("/comando", json={"cmd": "sleep 5", "timeout": 1}).json()
    assert r["tempo_esgotado"] and not r["ok"] and r["duracao_s"] < 4
    monkeypatch.setattr(ag, "SAIDA_MAX", 100)
    r = cli.post("/comando", json={"cmd": "seq 1 5000"}).json()
    assert "omitidos" in r["saida"] and len(r["saida"]) < 200
    assert cli.post("/comando", json={"cmd": "x", "timeout": 999}).status_code == 422


async def test_parar_interrompe_comando(ag):
    import httpx
    transporte = httpx.ASGITransport(app=ag.app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://pc",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        longo = asyncio.create_task(c.post("/comando", json={"cmd": "sleep 30", "timeout": 60}))
        for _ in range(100):
            if ag._comandos:
                break
            await asyncio.sleep(0.02)
        r = (await c.post("/parar")).json()
        assert r["comandos_parados"] == 1
        res = (await asyncio.wait_for(longo, 10)).json()
        assert res["codigo"] != 0


def test_abrir(ag, cli):
    cli.post("/abrir", json={"url": "https://exemplo.com"})
    cli.post("/abrir", json={"url": "g1.globo.com"})
    cli.post("/abrir", json={"app": "planilha"})
    assert ag._teste["abertos"] == [[ag.NAVEGADOR, "https://exemplo.com"], [ag.NAVEGADOR, "https://g1.globo.com"],
                                    ["libreoffice", "--calc"]]
    assert cli.post("/abrir", json={"url": "javascript:alert(1)"}).status_code == 400
    assert cli.post("/abrir", json={"app": "rm -rf /"}).status_code == 400
    assert cli.post("/abrir", json={}).status_code == 400
    (ag._teste["casa"] / "nota.txt").write_text("oi")
    cli.post("/abrir", json={"arquivo": "nota.txt"})
    assert ag._teste["abertos"][-1][0] == "xdg-open"
    assert cli.post("/abrir", json={"arquivo": "../../etc/passwd"}).status_code == 400


def test_arquivos_presos_na_pasta_pessoal(ag, cli, monkeypatch):
    r = cli.post("/arquivo?caminho=Documentos/teste.txt", content="conteúdo ✓".encode()).json()
    assert r == {"ok": True, "caminho": "Documentos/teste.txt", "tamanho": len("conteúdo ✓".encode())}
    assert cli.get("/arquivo?caminho=Documentos/teste.txt").content.decode() == "conteúdo ✓"
    lista = cli.get("/arquivos?caminho=Documentos").json()
    assert [i["nome"] for i in lista["itens"]] == ["teste.txt"]
    for ruim in ("../fora.txt", "/etc/passwd", "Documentos/../../x"):
        assert cli.get(f"/arquivo?caminho={ruim}").status_code == 400
        assert cli.post(f"/arquivo?caminho={ruim}", content=b"x").status_code == 400
    os.symlink("/etc", ag._teste["casa"] / "atalho")
    assert cli.get("/arquivo?caminho=atalho/passwd").status_code == 400     # link simbólico para fora
    assert cli.get("/arquivo?caminho=nao-existe.txt").status_code == 404
    monkeypatch.setattr(ag, "UPLOAD_MB", 0)
    assert cli.post("/arquivo?caminho=grande.bin", content=b"x" * 10).status_code == 413


def test_clipboard(ag, cli):
    assert cli.post("/clipboard", json={"texto": "oi"}).json() == {"ok": True}
    assert ag._teste["clip"][-1] == "oi"
    assert cli.get("/clipboard").json()["texto"] == "antigo"


def test_print_da_tela(ag, cli):
    r = cli.get("/tela?largura=640&qualidade=50")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-tela-largura"] == "1280" and r.headers["x-imagem-largura"] == "640"
    from PIL import Image
    assert Image.open(io.BytesIO(r.content)).size == (640, 400)
    r = cli.get("/tela?formato=png")
    assert r.headers["content-type"] == "image/png" and Image.open(io.BytesIO(r.content)).size == (1280, 800)
    assert cli.get("/tela?escala=2").status_code == 422


def test_recusa_ligar_sem_token(ag, monkeypatch):
    monkeypatch.setattr(ag, "TOKEN", "curto")
    monkeypatch.setattr(ag, "INSEGURO", False)
    with pytest.raises(SystemExit):
        ag.main()
