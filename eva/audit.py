"""Registro de TUDO que a Eva faz (tabela audit)."""
import json, time
from .db import db


def log(kind, **data):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tipo": kind, **data}
    db.run("INSERT INTO audit(ts,tipo,dados) VALUES(?,?,?)", (rec["ts"], kind, json.dumps(data, ensure_ascii=False, default=str)))
    return rec


def tail(n=200):
    rows = db.all("SELECT ts, tipo, dados FROM audit ORDER BY id DESC LIMIT ?", (n,))
    return [{"ts": r["ts"], "tipo": r["tipo"], **json.loads(r["dados"] or "{}")} for r in reversed(rows)]
