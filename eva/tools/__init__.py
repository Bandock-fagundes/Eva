"""Registro de ferramentas (as 'mãos' da Eva).

Para criar uma nova: num arquivo desta pasta, decore uma função async com
    @tool("nome", "descrição para a IA", {"param": {"type": "string"}}, rotulo="Fazendo X")
e importe o módulo no fim deste arquivo. Defina a permissão padrão em eva/permissions.py.

Opções:
- obrigatorios: lista de parâmetros obrigatórios (padrão: todos)
- rotulo: texto curto que aparece na tela ("Pesquisando na web")
- sensivel: async (args) -> motivo|None. Se devolver um motivo, uma ferramenta 'permitida' passa a pedir aprovação.
- timeout: segundos máximos de execução
- captura: depois de rodar, o agente tira um print do computador da Eva e mostra ao modelo
- espera: segundos para a tela "assentar" antes desse print (ex.: abrir um programa demora mais)
"""
from __future__ import annotations

from typing import Awaitable, Callable

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, params: dict, required=None, *, rotulo: str | None = None,
         sensivel: Callable[[dict], Awaitable[str | None]] | None = None, timeout: float = 120.0,
         interna: bool = False, captura: bool = False, espera: float = 0.0):
    def deco(fn):
        funcao = {"name": name, "description": description}
        if params:  # Gemini recusa 'properties' vazio: ferramenta sem argumentos vai sem 'parameters'
            obrig = list(params) if required is None else list(required)
            funcao["parameters"] = {"type": "object", "properties": params}
            if obrig:
                funcao["parameters"]["required"] = obrig
        TOOLS[name] = {
            "fn": fn,
            "rotulo": rotulo or name.replace("_", " "),
            "sensivel": sensivel,
            "timeout": timeout,
            "interna": interna,
            "captura": captura,
            "espera": espera,
            "schema": {"type": "function", "function": funcao},
        }
        return fn
    return deco


def schemas(excluir_prefixos: tuple[str, ...] = ()) -> list[dict]:
    return [t["schema"] for nome, t in TOOLS.items() if not (excluir_prefixos and nome.startswith(excluir_prefixos))]


def rotulo(nome: str) -> str:
    t = TOOLS.get(nome)
    return t["rotulo"] if t else nome


from . import basics, web, files, shell, browser, lembretes, computador  # noqa: E402,F401  (registra as ferramentas)
