"""Lembretes persistentes no banco + agendador em segundo plano que dispara na hora certa.

Quando dispara: grava a mensagem no chat (aparece no histórico mesmo com o app fechado) e avisa
todas as telas abertas (evento 'lembrete', que vira notificação no celular/PC se o app estiver aberto).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from . import audit, tempo
from .config import LEMBRETE_INTERVALO
from .db import db

log = logging.getLogger("eva.lembretes")

UNIDADES = {
    "s": 1, "seg": 1, "segundo": 1, "segundos": 1,
    "m": 60, "min": 60, "mins": 60, "minuto": 60, "minutos": 60,
    "h": 3600, "hr": 3600, "hora": 3600, "horas": 3600,
    "d": 86400, "dia": 86400, "dias": 86400,
    "semana": 604800, "semanas": 604800,
}


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def interpretar_quando(texto: str, agora: datetime | None = None) -> datetime:
    """Entende: '2026-10-28 15:30', '2026-10-28T15:30:00-03:00', '28/10/2026 15:30', '28/10 15h',
    '15:30' / '15h' (hoje, ou amanhã se já passou), 'amanhã 9h', 'hoje às 18:00',
    'em 10 minutos', 'daqui a 2 horas', 'em 1h30'. Horário de Brasília quando não tiver fuso."""
    agora = (agora or tempo.agora()).astimezone(tempo.TZ)
    t = _sem_acento((texto or "").strip().lower())
    t = re.sub(r"\s+", " ", t)
    if not t:
        raise ValueError("Diga quando (ex.: 'em 10 minutos' ou '2026-10-28 15:30').")

    # ISO
    try:
        iso = t.upper().replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2}", t) else None
        if iso:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=tempo.TZ)).astimezone(tempo.TZ)
    except ValueError:
        pass

    # relativo: "em 10 minutos", "daqui a 2 horas", "em 1h30", "daqui 3 dias"
    m = re.match(r"^(?:em|daqui(?: a)?|dentro de)\s+(.+)$", t)
    if m:
        resto = m.group(1).replace(" e ", " ")
        total = 0
        for num, un in re.findall(r"(\d+(?:[.,]\d+)?)\s*([a-z]+)?", resto):
            n = float(num.replace(",", "."))
            if not un:  # "1h30" -> 30 minutos
                total += n * 60
                continue
            if un not in UNIDADES:
                raise ValueError(f"Não entendi a unidade '{un}'.")
            total += n * UNIDADES[un]
        if total <= 0:
            raise ValueError("Não entendi quanto tempo.")
        return agora + timedelta(seconds=total)

    dia = agora.date()
    data_explicita = True
    if t.startswith("depois de amanha"):
        dia = dia + timedelta(days=2)
        t = t[len("depois de amanha"):].strip()
    elif t.startswith("amanha"):
        dia = dia + timedelta(days=1)
        t = t[len("amanha"):].strip()
    elif t.startswith("hoje"):
        t = t[len("hoje"):].strip()
    else:
        data_explicita = False
        m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*(.*)$", t)
        if m:
            data_explicita = True
            d, mes, ano, t = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4).strip()
            ano = int(ano) + (2000 if ano and len(ano) == 2 else 0) if ano else agora.year
            dia = datetime(ano, mes, d).date()
            if not m.group(3) and dia < agora.date():
                dia = datetime(ano + 1, mes, d).date()
    t = re.sub(r"^(as|a|ao|de|pelas|por volta das)\s+", "", t).strip()
    if not t:
        hh, mm = 9, 0  # só a data: 9h da manhã
    else:
        m = re.match(r"^(\d{1,2})(?:[:h](\d{2}))?\s*(h|horas?)?\s*(da manha|da tarde|da noite)?$", t)
        if not m:
            raise ValueError(f"Não entendi o horário '{texto}'. Use 'AAAA-MM-DD HH:MM' ou 'em 30 minutos'.")
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if m.group(4) in ("da tarde", "da noite") and hh < 12:
            hh += 12
    if hh > 23 or mm > 59:
        raise ValueError("Horário inválido.")
    dt = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=tempo.TZ)
    if dt <= agora and not data_explicita:
        dt += timedelta(days=1)  # "15h" já passou hoje -> amanhã
    return dt


def criar(quando: str, texto: str) -> dict:
    dt = interpretar_quando(quando)
    if dt < tempo.agora() - timedelta(minutes=1):
        raise ValueError(f"Esse horário já passou ({dt.strftime('%d/%m/%Y %H:%M')}).")
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Diga o que devo lembrar.")
    lid = db.insert("INSERT INTO lembretes(criado,quando,texto,status) VALUES(?,?,?,?)",
                    (tempo.ts(), tempo.para_utc_iso(dt), texto[:1000], "pendente"))
    audit.log("lembrete_criado", id=lid, quando=dt.isoformat(), texto=texto)
    agendador.invalidar()
    return {"id": lid, "quando": dt, "texto": texto}


def _fmt(r: dict) -> dict:
    dt = tempo.de_iso(r["quando"]).astimezone(tempo.TZ)
    return {"id": r["id"], "texto": r["texto"], "status": r["status"], "quando": dt.isoformat(timespec="minutes"),
            "quando_legivel": dt.strftime("%d/%m/%Y %H:%M"), "criado": r["criado"], "disparado_em": r.get("disparado_em")}


def listar(incluir_antigos: bool = False, limite: int = 100) -> list[dict]:
    if incluir_antigos:
        rows = db.all("SELECT * FROM lembretes ORDER BY id DESC LIMIT ?", (limite,))
    else:
        rows = db.all("SELECT * FROM lembretes WHERE status='pendente'")
    itens = [_fmt(r) for r in rows]
    itens.sort(key=lambda x: x["quando"])
    return itens


def cancelar(lid: int) -> bool:
    n = db.run("UPDATE lembretes SET status='cancelado' WHERE id=? AND status='pendente'", (int(lid),))
    if n:
        audit.log("lembrete_cancelado", id=int(lid))
        agendador.invalidar()
    return bool(n)


class Agendador:
    """Guarda os lembretes pendentes em memória (não consulta o banco a cada 15s, deixando o Neon dormir)."""

    RECARREGAR_S = 1800

    def __init__(self):
        self.tarefa: asyncio.Task | None = None
        self.ao_disparar = None  # async callback(evento: dict)
        self._cache: list[dict] | None = None
        self._carregado_em = 0.0

    def invalidar(self):
        self._cache = None

    def _pendentes(self) -> list[dict]:
        import time as _t
        if self._cache is None or _t.time() - self._carregado_em > self.RECARREGAR_S:
            self._cache = db.all("SELECT * FROM lembretes WHERE status='pendente'")
            self._carregado_em = _t.time()
        return self._cache

    async def verificar(self) -> list[dict]:
        """Uma passada: dispara todos os lembretes vencidos. Devolve os disparados."""
        from .memory import memory
        agora_utc = datetime.now(timezone.utc)
        disparados = []
        vencidos = []
        for r in self._pendentes():
            try:
                quando = tempo.de_iso(r["quando"])
            except (TypeError, ValueError):
                db.run("UPDATE lembretes SET status='erro' WHERE id=?", (r["id"],))
                self.invalidar()
                continue
            if quando <= agora_utc:
                vencidos.append((r, quando))
        for r, quando in vencidos:
            self.invalidar()
            # marca primeiro (evita disparar duas vezes)
            if not db.run("UPDATE lembretes SET status='disparado', disparado_em=? WHERE id=? AND status='pendente'",
                          (tempo.ts(), r["id"])):
                continue
            atraso = (agora_utc - quando).total_seconds()
            local = quando.astimezone(tempo.TZ).strftime("%d/%m %H:%M")
            texto = f"⏰ Lembrete: {r['texto']}"
            if atraso > 180:
                texto += f"\n(era pra {local} — eu estava dormindo/desligada e disparei agora)"
            memory.add_message("assistant", texto)
            ev = {"tipo": "lembrete", "id": r["id"], "texto": texto, "lembrete": r["texto"], "quando": local,
                  "atrasado": atraso > 180}
            audit.log("lembrete_disparado", id=r["id"], texto=r["texto"], atraso_s=int(atraso))
            disparados.append(ev)
            if self.ao_disparar:
                try:
                    await self.ao_disparar(ev)
                except Exception as e:  # noqa: BLE001
                    log.warning("Falha ao avisar lembrete: %s", e)
        return disparados

    async def _loop(self):
        while True:
            try:
                await self.verificar()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Agendador: %s", e)
            await asyncio.sleep(LEMBRETE_INTERVALO)

    def iniciar(self):
        if self.tarefa is None or self.tarefa.done():
            self.tarefa = asyncio.create_task(self._loop())

    async def parar(self):
        if self.tarefa:
            self.tarefa.cancel()
            try:
                await self.tarefa
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.tarefa = None


agendador = Agendador()
