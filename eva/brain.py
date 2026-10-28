"""Monta o 'cérebro' de cada turno: personalidade + regras + memórias relevantes + hora."""
import time
from .config import USER_NAME, ROOT
from .db import db
from .memory import memory

DEFAULT = ROOT / "eva" / "persona_padrao.md"  # personalidade inicial; dá pra editar no app (aba Eva)

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def persona() -> str:
    return persona_raw().replace("{usuario}", USER_NAME)


def persona_raw() -> str:
    return db.get("persona") or DEFAULT.read_text(encoding="utf-8")


def build_system_prompt(user_text: str) -> str:
    agora = time.localtime()  # fuso vem de TZ (no servidor: America/Sao_Paulo)
    partes = [persona(), f"\n## Agora\n{DIAS[agora.tm_wday]}, {time.strftime('%d/%m/%Y %H:%M', agora)}."]

    perfil = memory.by_category("perfil", 30) + memory.by_category("preferencias", 30)
    if perfil:
        partes.append("\n## O que você sabe sobre o " + USER_NAME + "\n" + "\n".join(f"- [{m['id']}] {m['conteudo']}" for m in perfil))

    relevantes = [m for m in memory.search(user_text, 8) if m["categoria"] not in ("perfil", "preferencias")]
    if relevantes:
        partes.append("\n## Memórias que podem ter a ver com essa mensagem\n" + "\n".join(f"- [{m['id']}] ({m['categoria']}) {m['conteudo']}" for m in relevantes))

    partes.append("\n(Use as memórias com naturalidade; não diga de onde veio a informação.)")
    return "\n".join(partes)
