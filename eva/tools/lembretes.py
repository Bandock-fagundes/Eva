from .. import lembretes as lem
from . import tool


@tool("agendar_lembrete", "Agenda um lembrete que aparece pro usuário na hora marcada (horário de Brasília).",
      {"quando": {"type": "string", "description": "'AAAA-MM-DD HH:MM', 'amanhã 9h', '15:30' ou 'em 20 minutos'"},
       "texto": {"type": "string", "description": "o que lembrar, curto e claro"}},
      rotulo="Agendando um lembrete")
async def agendar_lembrete(quando: str, texto: str):
    try:
        r = lem.criar(quando, texto)
    except ValueError as e:
        return f"ERRO: {e}"
    return f"Lembrete {r['id']} agendado para {r['quando'].strftime('%d/%m/%Y às %H:%M')}: {r['texto']}"


@tool("listar_lembretes", "Lista os lembretes pendentes.", {}, rotulo="Vendo os lembretes")
async def listar_lembretes():
    itens = lem.listar()
    return "\n".join(f"[{x['id']}] {x['quando_legivel']} — {x['texto']}" for x in itens) or "Nenhum lembrete pendente."


@tool("cancelar_lembrete", "Cancela um lembrete pendente pelo id.", {"id": {"type": "integer"}},
      rotulo="Cancelando um lembrete")
async def cancelar_lembrete(id: int):  # noqa: A002
    return "Lembrete cancelado." if lem.cancelar(int(id)) else "Não achei lembrete pendente com esse id."
