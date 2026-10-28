"""Hora certa no fuso do Gabriel (America/Sao_Paulo), independente do relógio do servidor."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import FUSO

try:
    TZ = ZoneInfo(FUSO)
except Exception:  # noqa: BLE001 — sem base de fusos: usa -03:00 fixo
    from datetime import timedelta
    TZ = timezone(timedelta(hours=-3), "BRT")

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


def agora() -> datetime:
    return datetime.now(TZ)


def ts() -> str:
    """Carimbo de hora local legível, usado no banco (AAAA-MM-DD HH:MM:SS)."""
    return agora().strftime("%Y-%m-%d %H:%M:%S")


def por_extenso(dt: datetime | None = None) -> str:
    dt = (dt or agora()).astimezone(TZ)
    return f"{DIAS[dt.weekday()]}, {dt.day} de {MESES[dt.month - 1]} de {dt.year}, {dt.strftime('%H:%M')}"


def para_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def de_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt
