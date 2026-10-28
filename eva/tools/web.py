import asyncio
import httpx
from bs4 import BeautifulSoup
from . import tool

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EVA/1.0"}


@tool("buscar_web", "Pesquisa na internet. Use para qualquer fato atual.",
      {"consulta": {"type": "string"}, "quantidade": {"type": "integer", "description": "padrão 6"}}, required=["consulta"])
async def buscar_web(consulta: str, quantidade: int = 6):
    def _search():
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return list(DDGS().text(consulta, region="br-pt", max_results=int(quantidade)))
    res = await asyncio.to_thread(_search)
    return "\n\n".join(f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in res) or "Sem resultados."


@tool("ler_pagina", "Baixa uma página da web e devolve o texto dela.", {"url": {"type": "string"}})
async def ler_pagina(url: str):
    async with httpx.AsyncClient(headers=UA, follow_redirects=True, timeout=20) as c:
        r = await c.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    texto = " ".join(soup.get_text(" ").split())
    return texto[:8000] or f"Página vazia (status {r.status_code})."
