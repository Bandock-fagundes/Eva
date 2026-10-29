"""Ferramentas do Computador da Eva: ela olha a tela (print) e usa mouse e teclado de um computador de verdade —
o PC Windows do Gabriel (EVA-Agente) ou o desktop Linux na nuvem. As ferramentas são as mesmas; só muda o transporte
(ver eva/computador.py).

Coordenadas sempre na grade 0–1000 (x da esquerda para a direita, y de cima para baixo), independente da
resolução real. Depois de cada ação o agente tira um print novo e mostra ao modelo (ver agent.py).
Só aparecem para o modelo quando há um computador conectado e respondendo.
"""
from __future__ import annotations

import re

from ..computador import GRADE, ComputadorErro, pc
from . import tool

APPS = ["navegador", "terminal", "arquivos", "editor", "texto", "planilha", "apresentacao"]
APPS_WINDOWS = ["calculadora", "configuracoes", "paint", "word", "excel", "powerpoint", "outlook", "spotify",
                "whatsapp", "chrome", "edge", "firefox"]

RE_JANELA_LOGIN = re.compile(r"senha|password|passwd|login|log in|sign[ -]?in|entrar|iniciar sess|acessar (a )?conta|"
                             r"autentica|verifica[çc][ãa]o em duas|2fa|token de acesso|seguran[çc]a do windows|"
                             r"windows security|credenciais|credential|bitwarden|1password|lastpass|keepass", re.I)
RE_JANELA_PAGAMENTO = re.compile(r"checkout|pagamento|payment|\bpagar\b|finalizar (a )?(compra|pedido)|carrinho|\bcart\b|"
                                 r"\bpix\b|boleto|cart[ãa]o de cr[ée]dito|billing|internet banking|transfer[êe]ncia", re.I)
RE_DIGITOS = re.compile(r"(?<![\d])(?:\d[ .-]?){10,19}(?![\d])")


def _luhn(num: str) -> bool:
    soma, alt = 0, False
    for c in reversed(num):
        d = int(c)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        soma += d
        alt = not alt
    return soma % 10 == 0


def _cpf_valido(n: str) -> bool:
    if len(n) != 11 or n == n[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(n[i]) * (tam + 1 - i) for i in range(tam))
        dig = (soma * 10) % 11 % 10
        if dig != int(n[tam]):
            return False
    return True


def texto_sensivel(texto: str) -> str | None:
    """Parece número de cartão (Luhn) ou CPF (dígitos verificadores)? Telefones comuns não disparam."""
    for m in RE_DIGITOS.finditer(texto or ""):
        n = re.sub(r"\D", "", m.group(0))
        if 13 <= len(n) <= 19 and _luhn(n):
            return "o texto parece um número de cartão"
        if len(n) == 11 and _cpf_valido(n):
            return "o texto parece um CPF"
    if re.search(r"\b(cvv|cvc|c[óo]digo de seguran[çc]a)\b", texto or "", re.I):
        return "o texto fala de código de segurança de cartão"
    return None


async def _sensivel_digitar(args: dict) -> str | None:
    motivo = texto_sensivel(str(args.get("texto", "")))
    if motivo:
        return motivo
    janela = await pc.janela_ativa()
    if RE_JANELA_LOGIN.search(janela):
        return f"a janela ativa parece de login/senha (\"{janela[:80]}\")"
    if RE_JANELA_PAGAMENTO.search(janela):
        return f"a janela ativa parece de pagamento (\"{janela[:80]}\")"
    return None


async def _sensivel_clicar(args: dict) -> str | None:
    if str(args.get("tipo") or "clicar") == "mover":
        return None
    janela = await pc.janela_ativa()
    if RE_JANELA_PAGAMENTO.search(janela):
        return f"clique numa tela de pagamento/compra (\"{janela[:80]}\")"
    return None


async def _sensivel_tecla(args: dict) -> str | None:
    teclas = str(args.get("teclas", "")).lower()
    if not re.search(r"\b(enter|return|kp_enter)\b", teclas):
        return None
    janela = await pc.janela_ativa()
    if RE_JANELA_PAGAMENTO.search(janela):
        return f"Enter numa tela de pagamento/compra (\"{janela[:80]}\")"
    return None


def _bloqueio() -> str | None:
    if pc.controle_gabriel:
        return ("ERRO: o Gabriel assumiu o controle do computador agora (aba Tela). Não mexa no computador até ele "
                "devolver; avise o que você ia fazer ou pergunte se pode continuar.")
    return None


def _f(v) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _janela(d: dict) -> str:
    j = (d or {}).get("janela") or ""
    return f" Janela ativa: \"{j[:100]}\"." if j else ""


async def _executar(rotulo: str, coro_fn):
    bloqueio = _bloqueio()
    if bloqueio:
        return bloqueio
    pc.acao_atual = rotulo
    try:
        return await coro_fn()
    except ComputadorErro as e:
        return str(e)


_XY = {"type": "number", "description": f"0 a {GRADE} (0 = esquerda, {GRADE} = direita)"}
_YY = {"type": "number", "description": f"0 a {GRADE} (0 = topo, {GRADE} = base)"}


@tool("computador_ver_tela",
      "Tira um print novo da tela do computador (o usuário assiste ao vivo na aba Tela). "
      "Use para olhar antes de agir ou para esperar algo carregar. A imagem chega logo depois do resultado.",
      {"esperar": {"type": "integer", "description": "segundos para esperar antes do print (0 a 10; padrão 0)"}},
      required=[], rotulo="Olhando a tela do computador", captura=True, timeout=40)
async def computador_ver_tela(esperar: int = 0):
    import asyncio

    async def acao():
        s = max(0, min(10, int(esperar or 0)))
        if s:
            await asyncio.sleep(s)
        d = await pc.info()
        m = d.get("mouse") or {}
        gx, gy = pc.para_grade(int(m.get("x", 0)), int(m.get("y", 0)))
        return (f"Tela {pc.largura}x{pc.altura}. Mouse em ({gx}, {gy}) na grade.{_janela(d)} "
                "O print vem na próxima mensagem.")
    return await _executar("Olhando a tela", acao)


@tool("computador_clicar",
      "Clica num ponto da tela do computador. Coordenadas na grade 0–1000 (mire no CENTRO do botão/campo). "
      "tipo: clicar (esquerdo, padrão), duplo (abrir arquivo/ícone), direito (menu) ou mover (só passa o mouse).",
      {"x": _XY, "y": _YY, "tipo": {"type": "string", "enum": ["clicar", "duplo", "direito", "mover"]}},
      required=["x", "y"], rotulo="Clicando no computador", sensivel=_sensivel_clicar, captura=True, espera=0.8,
      timeout=40)
async def computador_clicar(x: float, y: float, tipo: str = "clicar"):
    async def acao():
        t = tipo if tipo in ("clicar", "duplo", "direito", "mover") else "clicar"
        px, py = pc.para_pixels(x, y)
        d = await pc.mouse(t, x=px, y=py)
        verbo = {"clicar": "Cliquei", "duplo": "Dei duplo clique", "direito": "Cliquei com o botão direito",
                 "mover": "Movi o mouse"}[t]
        return f"{verbo} em ({_f(x)}, {_f(y)}) na grade → pixel ({px}, {py}).{_janela(d)} Confira no print se funcionou."
    return await _executar("Clicando", acao)


@tool("computador_digitar",
      "Digita um texto no lugar onde está o cursor (clique no campo antes). Aceita acentos e emoji. "
      "enter=true aperta Enter no fim; limpar=true apaga o que já estava no campo antes (Ctrl+A).",
      {"texto": {"type": "string"}, "enter": {"type": "boolean"}, "limpar": {"type": "boolean"}},
      required=["texto"], rotulo="Digitando no computador", sensivel=_sensivel_digitar, captura=True, espera=0.6,
      timeout=120)
async def computador_digitar(texto: str, enter: bool = False, limpar: bool = False):
    async def acao():
        if len(texto or "") > 5000:
            return "ERRO: texto grande demais (máx. 5000 caracteres por vez). Divida em partes."
        if limpar:
            await pc.teclado(teclas="ctrl+a BackSpace")
        d = await pc.teclado(texto=texto, teclas="Return" if enter else None)
        return (f"Digitei {len(texto)} caractere(s){' e apertei Enter' if enter else ''}.{_janela(d)} "
                "Confira no print se o texto apareceu no lugar certo.")
    return await _executar("Digitando", acao)


@tool("computador_tecla",
      "Aperta teclas ou atalhos no computador. Exemplos: 'enter', 'esc', 'tab', 'ctrl+l' (barra de endereço), "
      "'ctrl+t' (nova aba), 'ctrl+w', 'alt+tab', 'ctrl+c', 'ctrl+v', 'ctrl+s', 'f5', 'pagedown'. "
      "Vários em sequência separados por espaço: 'ctrl+a delete'.",
      {"teclas": {"type": "string"}}, rotulo="Apertando teclas no computador", sensivel=_sensivel_tecla,
      captura=True, espera=0.7, timeout=40)
async def computador_tecla(teclas: str):
    async def acao():
        d = await pc.teclado(teclas=teclas)
        return f"Apertei: {teclas}.{_janela(d)}"
    return await _executar(f"Apertando {teclas}", acao)


@tool("computador_rolar",
      "Rola a tela (rodinha do mouse) no computador. quanto = cliques da rodinha (1 a 15, padrão 5). "
      "x/y (grade 0–1000) = onde rolar (padrão: centro da tela).",
      {"direcao": {"type": "string", "enum": ["baixo", "cima", "esquerda", "direita"]},
       "quanto": {"type": "integer"}, "x": _XY, "y": _YY},
      required=["direcao"], rotulo="Rolando a tela do computador", captura=True, espera=0.6, timeout=40)
async def computador_rolar(direcao: str = "baixo", quanto: int = 5, x: float | None = None, y: float | None = None):
    async def acao():
        n = max(1, min(15, int(quanto or 5)))
        px, py = pc.para_pixels(GRADE / 2 if x is None else x, GRADE / 2 if y is None else y)
        dy = n if direcao == "baixo" else -n if direcao == "cima" else 0
        dx = n if direcao == "direita" else -n if direcao == "esquerda" else 0
        if not dx and not dy:
            return "ERRO: direção inválida (use baixo, cima, esquerda ou direita)."
        await pc.mouse("rolar", x=px, y=py, dy=dy, dx=dx)
        return f"Rolei {n} clique(s) para {direcao}."
    return await _executar("Rolando a tela", acao)


@tool("computador_arrastar",
      "Arrasta com o botão esquerdo de (x, y) até (x2, y2), na grade 0–1000 (mover janelas, selecionar, "
      "controles deslizantes).",
      {"x": _XY, "y": _YY, "x2": _XY, "y2": _YY},
      rotulo="Arrastando no computador", captura=True, espera=0.6, timeout=40)
async def computador_arrastar(x: float, y: float, x2: float, y2: float):
    async def acao():
        p1, p2 = pc.para_pixels(x, y), pc.para_pixels(x2, y2)
        await pc.mouse("arrastar", x=p1[0], y=p1[1], x2=p2[0], y2=p2[1])
        return f"Arrastei de ({_f(x)}, {_f(y)}) até ({_f(x2)}, {_f(y2)}) na grade."
    return await _executar("Arrastando", acao)


@tool("computador_comando",
      "Roda um comando no terminal do computador, na pasta do usuário: PowerShell no PC Windows do Gabriel, bash "
      "no desktop da nuvem. Programas gráficos abertos por aqui aparecem na tela. Pede aprovação por padrão.",
      {"comando": {"type": "string"},
       "timeout": {"type": "integer", "description": "segundos (padrão 60, máx. 300)"}},
      required=["comando"], rotulo="Rodando comando no computador", captura=True, espera=1.0, timeout=330)
async def computador_comando(comando: str, timeout: int = 60):
    async def acao():
        t = max(1, min(300, int(timeout or 60)))
        d = await pc.comando(comando, t)
        cab = "(tempo esgotado; comando interrompido)" if d.get("tempo_esgotado") else f"(código de saída {d.get('codigo')})"
        return f"{cab}\n{d.get('saida') or '(sem saída)'}"
    return await _executar("Rodando um comando", acao)


@tool("computador_abrir",
      "Abre um site, um programa ou um arquivo no computador. Use url para sites (abre no navegador padrão; é mais "
      "confiável que clicar na barra de endereço), app para programas ou arquivo (caminho dentro da pasta do "
      "usuário). Apps comuns: " + ", ".join(APPS) + ". No PC Windows também: " + ", ".join(APPS_WINDOWS) +
      " ou o nome de qualquer programa instalado (ele procura no menu Iniciar).",
      {"url": {"type": "string"},
       "app": {"type": "string", "description": "ex.: navegador, arquivos, editor, planilha, calculadora, word"},
       "arquivo": {"type": "string", "description": "ex.: Documentos/relatorio.docx"}},
      required=[], rotulo="Abrindo no computador", captura=True, espera=2.5, timeout=40)
async def computador_abrir(url: str | None = None, app: str | None = None, arquivo: str | None = None):
    async def acao():
        if not (url or app or arquivo):
            return "ERRO: informe url, app ou arquivo."
        if app and len(app) > 60:
            return "ERRO: nome de app comprido demais."
        d = await pc.abrir(url=url, app=app, arquivo=arquivo)
        return f"Abri {d.get('aberto') or url or app or arquivo}. Pode levar alguns segundos para carregar; confira no print."
    return await _executar(f"Abrindo {url or app or arquivo}", acao)


# ---------------------------------------------------------------- extras: janelas, área de transferência, arquivos

@tool("computador_janelas",
      "Lista as janelas abertas no PC Windows (título e qual está ativa) ou traz uma delas para a frente "
      "(focar = parte do título, ex.: 'Excel' ou 'WhatsApp'). Mais confiável que alt+tab.",
      {"focar": {"type": "string", "description": "parte do título da janela para trazer para a frente (opcional)"}},
      required=[], rotulo="Olhando as janelas do computador", captura=True, espera=0.6, timeout=40)
async def computador_janelas(focar: str | None = None):
    async def acao():
        if focar:
            d = await pc.focar(focar)
            return f"Trouxe para a frente: \"{(d.get('janela') or focar)[:100]}\"."
        d = await pc.janelas()
        lista = d.get("janelas") or []
        if not lista:
            return "Nenhuma janela aberta (só a área de trabalho)."
        linhas = [f"{'➤ ' if j.get('ativa') else '- '}{str(j.get('titulo'))[:120]}" for j in lista[:40]]
        return "Janelas abertas (➤ = ativa):\n" + "\n".join(linhas)
    return await _executar(f"Focando {focar}" if focar else "Olhando as janelas", acao)


async def _sensivel_clipboard(args: dict) -> str | None:
    if str(args.get("acao") or "ler") == "ler":
        return "ler a área de transferência pode mostrar uma senha ou dado pessoal que o Gabriel copiou"
    return texto_sensivel(str(args.get("texto", "")))


@tool("computador_area_transferencia",
      "Lê ou escreve a área de transferência (Ctrl+C/Ctrl+V) do computador. Escrever + ctrl+v é ótimo para colar "
      "textos longos. Ler pede aprovação (pode ter senha copiada).",
      {"acao": {"type": "string", "enum": ["ler", "escrever"]}, "texto": {"type": "string"}},
      required=["acao"], rotulo="Usando a área de transferência", sensivel=_sensivel_clipboard, timeout=40)
async def computador_area_transferencia(acao: str = "ler", texto: str | None = None):
    async def acao_fn():
        if acao == "escrever":
            if texto is None:
                return "ERRO: informe o texto para escrever."
            if len(texto) > 100000:
                return "ERRO: texto grande demais (máx. 100 mil caracteres)."
            await pc.clipboard(texto)
            return f"Coloquei {len(texto)} caractere(s) na área de transferência (use ctrl+v para colar)."
        d = await pc.clipboard()
        conteudo = str(d.get("texto") or "")
        return f"Área de transferência ({len(conteudo)} caracteres):\n{conteudo[:6000]}" if conteudo else \
            "A área de transferência está vazia (ou não tem texto)."
    return await _executar("Usando a área de transferência", acao_fn)


ARQUIVO_MAX = 8 * 1024 * 1024


@tool("computador_arquivo",
      "Copia arquivos entre o computador e a tua pasta de trabalho (workspace_eva). acao=baixar: do computador "
      "(caminho dentro da pasta do usuário, ex.: 'Downloads/nota.pdf') para a pasta da Eva; acao=enviar: da pasta "
      "da Eva (origem) para o computador (caminho). Máx. 8 MB. Pede aprovação.",
      {"acao": {"type": "string", "enum": ["baixar", "enviar"]},
       "caminho": {"type": "string", "description": "caminho no computador, relativo à pasta do usuário"},
       "origem": {"type": "string", "description": "enviar: arquivo na pasta da Eva"},
       "sobrescrever": {"type": "boolean"}},
      required=["acao", "caminho"], rotulo="Copiando arquivo do/para o computador", timeout=150)
async def computador_arquivo(acao: str, caminho: str, origem: str | None = None, sobrescrever: bool = False):
    from .files import _safe

    async def acao_fn():
        if acao == "baixar":
            nome, dados = await pc.arquivo_ler(caminho)
            if len(dados) > ARQUIVO_MAX:
                return "ERRO: arquivo maior que 8 MB."
            destino = _safe("do_computador/" + re.sub(r"[\\/:*?\"<>|]+", "_", nome)[:120])
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(dados)
            return f"Baixei '{nome}' ({len(dados)} bytes) para a tua pasta: do_computador/{destino.name}."
        if acao == "enviar":
            if not origem:
                return "ERRO: informe 'origem' (o arquivo na pasta da Eva)."
            p = _safe(origem)
            if not p.is_file():
                return "ERRO: esse arquivo não existe na pasta da Eva."
            if p.stat().st_size > ARQUIVO_MAX:
                return "ERRO: arquivo maior que 8 MB."
            d = await pc.arquivo_escrever(caminho, p.read_bytes(), sobrescrever=bool(sobrescrever))
            return f"Enviei '{p.name}' para o computador em {d.get('caminho') or caminho}."
        return "ERRO: acao precisa ser 'baixar' ou 'enviar'."
    return await _executar("Copiando arquivo", acao_fn)
