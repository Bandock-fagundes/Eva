from .. import tempo
from ..memory import CATEGORIAS, memory
from . import tool


@tool("hora_atual", "Data e hora atuais no horário de Brasília.", {}, rotulo="Vendo que horas são")
async def hora_atual():
    return tempo.por_extenso() + f" (horário de Brasília). ISO: {tempo.agora().isoformat(timespec='minutes')}"


@tool("lembrar", "Salva um fato duradouro sobre o usuário ou o mundo dele na memória de longo prazo. "
      "Se já existir algo parecido, a memória é atualizada (não duplica).",
      {"conteudo": {"type": "string", "description": "O fato, numa frase clara e completa, com o contexto."},
       "categoria": {"type": "string", "enum": CATEGORIAS},
       "importancia": {"type": "integer", "description": "1 (detalhe) a 5 (essencial). Padrão 3."}},
      required=["conteudo"], rotulo="Guardando na memória")
async def lembrar(conteudo: str, categoria: str = "geral", importancia: int = 3):
    mid, acao = await memory.lembrar(conteudo, categoria, importancia, origem="eva")
    if acao == "recusada":
        return "Não salvei: parece dado sensível (senha, cartão, documento) ou está vazio."
    return {"nova": f"Memória salva (id {mid}).", "atualizada": f"Memória {mid} atualizada.",
            "igual": f"Isso já estava na memória (id {mid})."}[acao]


@tool("buscar_memoria", "Procura na memória de longo prazo (coisas que o usuário já contou).",
      {"consulta": {"type": "string"}}, rotulo="Procurando na memória")
async def buscar_memoria(consulta: str):
    res = await memory.search(consulta, 10)
    return "\n".join(f"[{m['id']}] ({m['categoria']}, {m.get('atualizada') or m['ts']}) {m['conteudo']}"
                     for m in res) or "Nada encontrado na memória."


@tool("esquecer", "Apaga uma memória pelo id (quando ficou errada ou o usuário pediu).",
      {"id": {"type": "integer"}}, rotulo="Apagando uma memória")
async def esquecer(id: int):  # noqa: A002
    return "Apagada." if memory.forget(int(id)) else "Id não encontrado."


@tool("perguntar_ao_gabriel", "Faz uma pergunta ao usuário no meio da tarefa e espera a resposta. Use só quando a "
      "decisão é dele ou falta um dado que só ele tem.",
      {"pergunta": {"type": "string"},
       "opcoes": {"type": "array", "items": {"type": "string"}, "description": "Respostas sugeridas (opcional)"}},
      required=["pergunta"], rotulo="Perguntando pra você", interna=True, timeout=3600)
async def perguntar_ao_gabriel(pergunta: str, opcoes=None):
    # tratada pelo agente (precisa da tela); aqui só por segurança
    return "Pergunta indisponível neste modo."
