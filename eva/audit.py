"""Registro de TUDO que a Eva faz (tabela audit). Nunca derruba a Eva se o banco falhar."""
from __future__ import annotations

import json
import logging
import re

from . import tempo
from .db import db

_logger = logging.getLogger("eva.audit")

_CHAVES_SECRETAS = re.compile(r"(.*[_-])?(senha|password|api_?key|token|secret|authorization|cookie)", re.I)
LIMITE_CAMPO = 4000


def _limpar(v, profundidade=0):
    if profundidade > 4:
        return str(v)[:200]
    if isinstance(v, dict):
        return {k: ("***" if _CHAVES_SECRETAS.fullmatch(str(k)) else _limpar(x, profundidade + 1)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_limpar(x, profundidade + 1) for x in list(v)[:50]]
    if isinstance(v, str) and len(v) > LIMITE_CAMPO:
        return v[:LIMITE_CAMPO] + f"… (+{len(v) - LIMITE_CAMPO} caracteres)"
    return v


MAX_LINHAS = 50000      # guarda os últimos 50 mil registros (o banco grátis do Neon tem 0,5 GB)
_contador = {"n": 0}


def _podar():
    try:
        r = db.one("SELECT MAX(id) AS m FROM audit")
        if r and r["m"] and int(r["m"]) > MAX_LINHAS:
            db.run("DELETE FROM audit WHERE id < ?", (int(r["m"]) - MAX_LINHAS,))
    except Exception as e:  # noqa: BLE001
        _logger.warning("Falha ao podar auditoria: %s", type(e).__name__)


def log_evento(tipo: str, /, **dados) -> dict:
    rec = {**dados, "ts": tempo.ts(), "tipo": tipo}
    try:
        db.run("INSERT INTO audit(ts,tipo,dados) VALUES(?,?,?)",
               (rec["ts"], tipo, json.dumps(_limpar(dados), ensure_ascii=False, default=str)))
        _contador["n"] += 1
        if _contador["n"] % 1000 == 1:
            _podar()
    except Exception as e:  # noqa: BLE001
        _logger.error("Falha ao gravar auditoria (%s): %s", tipo, type(e).__name__)
    return rec


def log(tipo: str, /, **dados) -> dict:
    """Atalho: audit.log("tipo", campo=valor, ...)."""
    return log_evento(tipo, **dados)


def _linha(r: dict) -> dict:
    try:
        extra = json.loads(r.get("dados") or "{}")
    except (TypeError, ValueError):
        extra = {"bruto": r.get("dados")}
    if not isinstance(extra, dict):
        extra = {"valor": extra}
    return {**extra, "id": r["id"], "ts": r["ts"], "tipo": r["tipo"]}


def pagina(pagina: int = 1, tamanho: int = 50, tipo: str | None = None, busca: str | None = None) -> dict:
    pagina = max(1, int(pagina))
    tamanho = max(1, min(500, int(tamanho)))
    cond, params = [], []
    if tipo:
        cond.append("tipo = ?")
        params.append(tipo)
    if busca:
        cond.append("(dados LIKE ? OR tipo LIKE ?)")
        params += [f"%{busca}%", f"%{busca}%"]
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    total = db.one(f"SELECT COUNT(*) AS n FROM audit{where}", params)["n"]
    rows = db.all(f"SELECT id, ts, tipo, dados FROM audit{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                  params + [tamanho, (pagina - 1) * tamanho])
    return {"itens": [_linha(r) for r in rows], "total": int(total), "pagina": pagina, "tamanho": tamanho}


def tipos() -> list[str]:
    return [r["tipo"] for r in db.all("SELECT DISTINCT tipo FROM audit ORDER BY tipo")]


def tail(n: int = 200) -> list[dict]:
    return list(reversed(pagina(1, n)["itens"]))
