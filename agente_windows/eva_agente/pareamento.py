"""Pareamento: troca o código de uso único (gerado no app da Eva) por um token de dispositivo."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import VERSAO

LOCAIS = ("localhost", "127.0.0.1", "::1")


class ErroPareamento(Exception):
    pass


def normalizar_servidor(endereco: str) -> str:
    """'eva-hldw.onrender.com' -> 'https://eva-hldw.onrender.com'. Exige HTTPS (menos no próprio PC, para testes)."""
    e = str(endereco or "").strip()
    if not e:
        raise ErroPareamento("Digite o endereço da Eva.")
    if "://" not in e:
        e = "https://" + e
    u = urlparse(e)
    if u.scheme.lower() not in ("http", "https") or not u.hostname or \
            ("." not in u.hostname and u.hostname not in LOCAIS):
        raise ErroPareamento("Endereço inválido. Exemplo: https://eva-hldw.onrender.com")
    if u.scheme.lower() == "http" and u.hostname not in LOCAIS:
        raise ErroPareamento("Por segurança o endereço precisa começar com https://")
    caminho = u.path.rstrip("/")
    return f"{u.scheme.lower()}://{u.netloc}{caminho}"


def url_ws(servidor: str) -> str:
    u = urlparse(normalizar_servidor(servidor))
    esquema = "wss" if u.scheme == "https" else "ws"
    return f"{esquema}://{u.netloc}{u.path.rstrip('/')}/ws/pc"


def contexto_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()  # no Windows usa os certificados do sistema
    try:
        import certifi  # opcional (vem junto no .exe se instalado)
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


def _post_json(url: str, corpo: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"EVA-Agente/{VERSAO}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=contexto_ssl()) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def parear(servidor: str, codigo: str, nome: str, timeout: float = 90.0) -> dict:
    """Devolve {servidor, token, dispositivo_id, nome}. Erros viram ErroPareamento com mensagem em PT-BR."""
    servidor = normalizar_servidor(servidor)
    codigo = "".join(c for c in str(codigo or "").upper() if c.isalnum())
    if len(codigo) != 8:
        raise ErroPareamento("O código tem 8 letras/números (ex.: K7PX-4MQ2). Confira no app da Eva.")
    try:
        status, d = _post_json(f"{servidor}/api/pc/parear/confirmar",
                               {"codigo": codigo, "nome": (nome or "")[:60], "versao": VERSAO}, timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        motivo = getattr(e, "reason", e)
        raise ErroPareamento("Não consegui falar com a Eva. Confira o endereço e a internet. Se a Eva estava "
                             f"dormindo, espere 1 minuto e tente de novo. ({motivo})") from None
    if status == 200 and d.get("token"):
        return {"servidor": servidor, "token": d["token"], "dispositivo_id": d.get("dispositivo_id"),
                "nome": d.get("nome") or nome}
    if status == 429:
        raise ErroPareamento(d.get("erro") or "Muitas tentativas. Espere 10 minutos.")
    if status in (401, 403):
        raise ErroPareamento(d.get("erro") or "Código errado ou vencido. Gere um novo no app da Eva.")
    raise ErroPareamento(d.get("erro") or f"A Eva respondeu com erro {status}. Tente de novo em instantes.")
