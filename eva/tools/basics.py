import time
from . import tool
from ..memory import memory


@tool("hora_atual", "Data e hora atuais.", {})
async def hora_atual():
    return time.strftime("%A, %d/%m/%Y %H:%M:%S")


@tool("lembrar", "Salva um fato duradouro sobre o usuário ou o mundo dele na memória de longo prazo.",
      {"conteudo": {"type": "string", "description": "O fato, em uma frase clara, com o contexto."},
       "categoria": {"type": "string", "description": "perfil, preferencias, pessoas, projetos, rotina, saude, trabalho ou geral"}})
async def lembrar(conteudo: str, categoria: str = "geral"):
    mid = memory.remember(conteudo, categoria)
    return f"Memória salva (id {mid})."


@tool("buscar_memoria", "Procura na memória de longo prazo.", {"consulta": {"type": "string"}})
async def buscar_memoria(consulta: str):
    res = memory.search(consulta, 10)
    return "\n".join(f"[{m['id']}] ({m['categoria']}, {m['ts']}) {m['conteudo']}" for m in res) or "Nada encontrado."


@tool("esquecer", "Apaga uma memória pelo id (quando ficou errada ou o usuário pediu).", {"id": {"type": "integer"}})
async def esquecer(id: int):
    return "Apagada." if memory.forget(int(id)) else "Id não encontrado."
