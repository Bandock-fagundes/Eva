"""Arquivos — a Eva só mexe dentro de workspace_eva."""
from pathlib import Path

from ..config import WORKSPACE
from . import tool

LIMITE_LEITURA = 20000
LIMITE_ESCRITA = 1_000_000


def _safe(caminho: str) -> Path:
    base = WORKSPACE.resolve()
    p = (base / (caminho or ".")).resolve()
    if p != base and base not in p.parents:
        raise PermissionError("A Eva só pode mexer dentro da pasta workspace_eva.")
    return p


@tool("listar_arquivos", "Lista arquivos na pasta de trabalho da Eva (workspace_eva).",
      {"pasta": {"type": "string", "description": "subpasta, ou '.'"}}, required=[], rotulo="Olhando os arquivos")
async def listar_arquivos(pasta: str = "."):
    p = _safe(pasta)
    if not p.exists():
        return "Essa pasta não existe."
    itens = []
    for x in sorted(p.iterdir()):
        if x.is_dir():
            itens.append(f"📁 {x.name}/")
        else:
            itens.append(f"📄 {x.name} ({x.stat().st_size} bytes)")
    return "\n".join(itens[:300]) or "(pasta vazia)"


@tool("ler_arquivo", "Lê um arquivo de texto da pasta de trabalho.", {"caminho": {"type": "string"}},
      rotulo="Lendo um arquivo")
async def ler_arquivo(caminho: str):
    p = _safe(caminho)
    if not p.is_file():
        return "Arquivo não encontrado."
    texto = p.read_text(encoding="utf-8", errors="replace")
    if len(texto) > LIMITE_LEITURA:
        return texto[:LIMITE_LEITURA] + f"\n… (cortado; o arquivo tem {len(texto)} caracteres)"
    return texto


@tool("escrever_arquivo", "Cria ou sobrescreve um arquivo de texto na pasta de trabalho.",
      {"caminho": {"type": "string"}, "conteudo": {"type": "string"}}, rotulo="Escrevendo um arquivo")
async def escrever_arquivo(caminho: str, conteudo: str):
    if len(conteudo) > LIMITE_ESCRITA:
        return "Conteúdo grande demais (máx. 1 MB)."
    p = _safe(caminho)
    if p == WORKSPACE.resolve():
        return "Informe o nome do arquivo."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(conteudo, encoding="utf-8")
    return f"Salvo: {p.relative_to(WORKSPACE.resolve())} ({len(conteudo)} caracteres)."
