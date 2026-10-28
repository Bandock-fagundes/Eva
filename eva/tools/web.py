"""Pesquisa na web (ddgs com retry + fallback) e leitura de páginas (texto principal limpo)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

from . import tool

log = logging.getLogger("eva.web")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
CABECALHOS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
              "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
MAX_BYTES = 3_000_000


# ---------------------------------------------------------------- busca

def _ddgs(consulta: str, n: int, backend: str):
    try:
        from ddgs import DDGS
    except ImportError:  # versões antigas
        from duckduckgo_search import DDGS  # type: ignore
    with DDGS(timeout=12) as d:
        return list(d.text(consulta, region="br-pt", safesearch="moderate", max_results=n, backend=backend) or [])


def parse_ddg_html(html: str, n: int) -> list[dict]:
    """Lê a página HTML 'lite' do DuckDuckGo (fallback quando a biblioteca falha)."""
    soup = BeautifulSoup(html, "html.parser")
    res = []
    for bloco in soup.select(".result, .web-result"):
        a = bloco.select_one("a.result__a, a.result-link")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:  # link de redirecionamento do DDG
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snip = bloco.select_one(".result__snippet, .result-snippet")
        res.append({"title": a.get_text(" ", strip=True), "href": href,
                    "body": snip.get_text(" ", strip=True) if snip else ""})
        if len(res) >= n:
            break
    return res


async def _ddg_html(consulta: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(headers=CABECALHOS, follow_redirects=True, timeout=15) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": consulta, "kl": "br-pt"})
        r.raise_for_status()
    return parse_ddg_html(r.text, n)


def formatar_resultados(res: list[dict]) -> str:
    linhas = []
    for i, r in enumerate(res, 1):
        titulo = (r.get("title") or "").strip()
        url = (r.get("href") or r.get("url") or "").strip()
        corpo = (r.get("body") or r.get("snippet") or "").strip()
        linhas.append(f"{i}. {titulo}\n   {url}\n   {corpo}")
    return "\n\n".join(linhas)


@tool("buscar_web", "Pesquisa na internet e devolve títulos, links e trechos. Use para qualquer fato atual.",
      {"consulta": {"type": "string"}, "quantidade": {"type": "integer", "description": "1 a 10, padrão 6"}},
      required=["consulta"], rotulo="Pesquisando na web", timeout=60)
async def buscar_web(consulta: str, quantidade: int = 6):
    n = max(1, min(10, int(quantidade or 6)))
    erros = []
    for tentativa, backend in enumerate(("auto", "auto", "duckduckgo")):
        try:
            res = await asyncio.wait_for(asyncio.to_thread(_ddgs, consulta, n, backend), timeout=25)
            if res:
                return formatar_resultados(res)
            erros.append(f"{backend}: sem resultados")
        except Exception as e:  # noqa: BLE001
            erros.append(f"{backend}: {type(e).__name__}")
        await asyncio.sleep(0.8 * (tentativa + 1))
    try:
        res = await _ddg_html(consulta, n)
        if res:
            return formatar_resultados(res)
        erros.append("html: sem resultados")
    except Exception as e:  # noqa: BLE001
        erros.append(f"html: {type(e).__name__}")
    log.warning("Busca falhou: %s", erros)
    return ("Não consegui resultados na pesquisa agora (" + "; ".join(erros) + "). "
            "Tente outra formulação, ou use ler_pagina/navegador num site específico.")


# ---------------------------------------------------------------- leitura de página

def _ip_privado(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # deixa o httpx dar o erro de DNS normal
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


async def validar_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    if not p.hostname:
        raise ValueError("URL inválida.")
    from .. import config
    if config.PERMITIR_ENDERECOS_LOCAIS:
        return url
    if p.hostname in ("localhost",) or await asyncio.to_thread(_ip_privado, p.hostname):
        raise PermissionError("Por segurança, não acesso endereços internos/privados.")
    return url


def extrair_texto_principal(html: str, limite: int = 8000) -> tuple[str, str]:
    """Devolve (título, texto principal limpo)."""
    soup = BeautifulSoup(html, "html.parser")
    titulo = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]
    for t in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "aside", "form",
                   "button", "template"]):
        t.decompose()
    for t in soup.select('[role="navigation"], [aria-hidden="true"], .cookie, .cookies, .advertisement, .ads'):
        t.decompose()
    candidatos = soup.find_all("article") + soup.find_all("main")
    alvo = None
    if candidatos:
        alvo = max(candidatos, key=lambda c: len(c.get_text(" ", strip=True)))
        if len(alvo.get_text(" ", strip=True)) < 400:
            alvo = None
    alvo = alvo or soup.body or soup
    linhas = []
    for linha in alvo.get_text("\n").splitlines():
        linha = re.sub(r"\s+", " ", linha).strip()
        if len(linha) > 1:
            linhas.append(linha)
    texto = "\n".join(linhas)
    if len(texto) > limite:
        texto = texto[:limite] + f"\n… (página cortada; tinha {len(texto)} caracteres)"
    return titulo, texto


@tool("ler_pagina", "Baixa uma página da web e devolve o texto principal dela (sem menus/propagandas).",
      {"url": {"type": "string"}, "limite": {"type": "integer", "description": "máx. de caracteres (padrão 8000)"}},
      required=["url"], rotulo="Lendo uma página", timeout=45)
async def ler_pagina(url: str, limite: int = 8000):
    url = await validar_url(url)
    limite = max(500, min(20000, int(limite or 8000)))
    async with httpx.AsyncClient(headers=CABECALHOS, follow_redirects=True, timeout=20, max_redirects=8) as c:
        async with c.stream("GET", url) as r:
            tipo = r.headers.get("content-type", "").lower()
            corpo = b""
            async for pedaco in r.aiter_bytes():
                corpo += pedaco
                if len(corpo) > MAX_BYTES:
                    break
            final = str(r.url)
            status = r.status_code
            enc = r.encoding or "utf-8"
    if status >= 400:
        return f"A página respondeu com erro {status} ({final})."
    if "pdf" in tipo:
        return f"Isso é um PDF ({final}); ainda não consigo ler PDFs por aqui. Tente outra fonte."
    texto_bruto = corpo.decode(enc, errors="replace")
    if "html" not in tipo and "xml" not in tipo and tipo:
        texto = texto_bruto[:limite]
        return f"URL: {final}\n\n{texto}"
    titulo, texto = extrair_texto_principal(texto_bruto, limite)
    return f"URL: {final}\nTítulo: {titulo}\n\n{texto or '(página sem texto legível — talvez precise do navegador)'}"


__all__ = ["buscar_web", "ler_pagina", "parse_ddg_html", "extrair_texto_principal", "validar_url", "quote_plus"]
