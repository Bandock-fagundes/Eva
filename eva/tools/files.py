from pathlib import Path
from . import tool
from ..config import WORKSPACE


def _safe(caminho: str) -> Path:
    p = (WORKSPACE / caminho).resolve()
    if WORKSPACE.resolve() not in p.parents and p != WORKSPACE.resolve():
        raise PermissionError("A Eva só pode mexer dentro da pasta workspace_eva.")
    return p


@tool("listar_arquivos", "Lista arquivos na pasta de trabalho da Eva.", {"pasta": {"type": "string", "description": "subpasta, ou '.'"}}, required=[])
async def listar_arquivos(pasta: str = "."):
    p = _safe(pasta)
    return "\n".join(("📁 " if x.is_dir() else "📄 ") + x.name for x in sorted(p.iterdir())) or "(vazia)"


@tool("ler_arquivo", "Lê um arquivo de texto da pasta de trabalho.", {"caminho": {"type": "string"}})
async def ler_arquivo(caminho: str):
    return _safe(caminho).read_text(encoding="utf-8", errors="replace")[:10000]


@tool("escrever_arquivo", "Cria ou sobrescreve um arquivo de texto na pasta de trabalho.",
      {"caminho": {"type": "string"}, "conteudo": {"type": "string"}})
async def escrever_arquivo(caminho: str, conteudo: str):
    p = _safe(caminho)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(conteudo, encoding="utf-8")
    return f"Salvo: {p.name} ({len(conteudo)} caracteres)."
