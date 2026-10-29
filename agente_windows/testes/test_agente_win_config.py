"""Config (token guardado com DPAPI no Windows / arquivo 0600 fora dele) e pareamento por código."""
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from eva_agente import SERVIDOR_PADRAO, config
from eva_agente.pareamento import ErroPareamento, normalizar_servidor, parear, url_ws


@pytest.fixture(autouse=True)
def pasta_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("EVA_AGENTE_PASTA", str(tmp_path))
    yield tmp_path


def test_config_padrao_e_ida_e_volta(pasta_tmp):
    c = config.carregar()
    assert c.servidor == SERVIDOR_PADRAO == "https://eva-hldw.onrender.com" and not c.pareado
    config.salvar(config.Config(servidor="https://eva.exemplo.com", dispositivo_id=7, nome="PC-Sala",
                                token="evapc_segredo123"))
    c = config.carregar()
    assert (c.servidor, c.dispositivo_id, c.nome, c.token) == ("https://eva.exemplo.com", 7, "PC-Sala",
                                                               "evapc_segredo123")
    assert c.pareado
    bruto = (pasta_tmp / "config.json").read_text()
    if sys.platform == "win32":
        assert "evapc_segredo123" not in bruto and "token_dpapi" in json.loads(bruto)   # cifrado com DPAPI
    else:
        assert stat.S_IMODE(os.stat(pasta_tmp / "config.json").st_mode) == 0o600
    config.esquecer_token()
    c = config.carregar()
    assert not c.token and not c.pareado and c.servidor == "https://eva.exemplo.com"


def test_config_corrompida(pasta_tmp):
    (pasta_tmp / "config.json").write_text("{lixo")
    assert config.carregar().token == ""


def test_registro_local(pasta_tmp):
    lg = config.configurar_registro()
    try:
        lg.info("ação de teste")
        for h in lg.handlers:
            h.flush()
        assert "ação de teste" in config.arquivo_registro().read_text(encoding="utf-8")
    finally:
        for h in list(lg.handlers):
            if getattr(h, "baseFilename", "").startswith(str(pasta_tmp)):
                lg.removeHandler(h)
                h.close()


@pytest.mark.parametrize("entrada,saida", [
    ("eva-hldw.onrender.com", "https://eva-hldw.onrender.com"),
    ("https://eva-hldw.onrender.com/", "https://eva-hldw.onrender.com"),
    ("  HTTPS://Eva.com  ", "https://Eva.com"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("http://localhost:8000/", "http://localhost:8000"),
])
def test_normalizar_servidor(entrada, saida):
    assert normalizar_servidor(entrada) == saida


@pytest.mark.parametrize("ruim", ["", "http://eva-hldw.onrender.com", "ftp://x.com", "https://"])
def test_servidor_inseguro_ou_invalido(ruim):
    with pytest.raises(ErroPareamento):
        normalizar_servidor(ruim)


def test_url_ws():
    assert url_ws("https://eva-hldw.onrender.com") == "wss://eva-hldw.onrender.com/ws/pc"
    assert url_ws("http://127.0.0.1:9000") == "ws://127.0.0.1:9000/ws/pc"


class _Eva(BaseHTTPRequestHandler):
    respostas: dict = {}
    recebidos: list = []

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Eva.recebidos.append((self.path, corpo))
        status, d = _Eva.respostas.get(corpo["codigo"], (401, {"ok": False, "erro": "Código errado ou vencido."}))
        dados = json.dumps(d).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *a):
        pass


@pytest.fixture
def eva_http():
    srv = HTTPServer(("127.0.0.1", 0), _Eva)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Eva.recebidos = []
    _Eva.respostas = {"ABCD2345": (200, {"ok": True, "token": "evapc_novo", "dispositivo_id": 3, "nome": "PC"}),
                      "LIMITE22": (429, {"ok": False, "erro": "Muitas tentativas."})}
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_parear(eva_http):
    d = parear(eva_http, "abcd-2345", "PC do Gabriel")
    assert d == {"servidor": eva_http, "token": "evapc_novo", "dispositivo_id": 3, "nome": "PC"}
    caminho, corpo = _Eva.recebidos[0]
    assert caminho == "/api/pc/parear/confirmar" and corpo["codigo"] == "ABCD2345" and corpo["nome"] == "PC do Gabriel"
    with pytest.raises(ErroPareamento, match="errado ou vencido"):
        parear(eva_http, "ZZZZ-9999", "PC")
    with pytest.raises(ErroPareamento, match="Muitas"):
        parear(eva_http, "LIMITE22", "PC")
    with pytest.raises(ErroPareamento, match="8 letras"):
        parear(eva_http, "123", "PC")


def test_parear_sem_conexao():
    with pytest.raises(ErroPareamento, match="Não consegui falar com a Eva"):
        parear("http://127.0.0.1:9", "ABCD2345", "PC", timeout=3)
