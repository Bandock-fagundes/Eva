"""Monta o 'cérebro' de cada turno: persona + hora + regras de ferramentas + memórias + resumo + conversa recente."""
from __future__ import annotations

from . import tempo
from .config import CONTEXTO_CHARS, HIST_RECENTES, MAX_STEPS, ROOT, USER_NAME
from .db import db
from .memory import memory

DEFAULT = ROOT / "eva" / "persona_padrao.md"

REGRAS_FERRAMENTAS = """## Como trabalhar e usar suas ferramentas
- Pedido simples ou conversa: responda direto, sem ferramenta.
- Pedido complexo (várias etapas): pense num plano curto (2 a 5 passos) e execute passo a passo usando as
  ferramentas, sem pedir licença a cada passo. Se quiser, mostre o plano em uma linha antes de começar.
- buscar_web: para QUALQUER informação atual ou que você não tem certeza (clima, notícias, preços, horários,
  eventos, pessoas, produtos). Depois, se precisar de detalhe, use ler_pagina no melhor resultado.
  Cite a fonte (site) quando der informação pesquisada.
- navegador_*: só quando precisa interagir com um site (clicar, preencher, logar, ver algo dinâmico).
  Fluxo: navegador_abrir -> leia os elementos numerados -> navegador_clicar/navegador_digitar pelo número.
  Para só ler conteúdo, prefira ler_pagina (mais rápido e leve).
- lembrar: quando o {usuario} pedir para lembrar algo, ou contar algo duradouro e importante. Salve sem anunciar.
  buscar_memoria: quando precisar de algo que ele já contou e não está no contexto abaixo.
- agendar_lembrete: quando ele pedir para ser lembrado de algo numa data/hora. Use a data/hora atual abaixo
  para calcular; formato 'AAAA-MM-DD HH:MM' (horário de Brasília) ou 'em 30 minutos'.
- perguntar_ao_gabriel: só quando estiver travada numa decisão que é dele (gosto pessoal, dinheiro, dados que
  só ele tem). Não use para coisas que você consegue descobrir.
- Se uma ferramenta der ERRO, leia a mensagem, corrija os argumentos ou tente outro caminho (no máximo 2 vezes
  a mesma coisa). Se for NEGADO ou BLOQUEADO, não insista nem contorne: explique e ofereça alternativa.
- Você tem até {passos} passos de ferramenta por pedido. Seja eficiente.
- Nunca diga que fez algo que não fez. Nunca invente resultado de ferramenta."""


def persona_raw() -> str:
    try:
        salvo = db.get("persona")
    except Exception:  # noqa: BLE001
        salvo = None
    return salvo or persona_padrao()


def persona_padrao() -> str:
    return DEFAULT.read_text(encoding="utf-8")


def persona_personalizada() -> bool:
    try:
        return bool(db.get("persona"))
    except Exception:  # noqa: BLE001
        return False


def persona() -> str:
    return persona_raw().replace("{usuario}", USER_NAME)


async def build_system_prompt(texto_usuario: str, resumo: str | None = None) -> str:
    partes = [persona()]
    partes.append(f"\n## Agora\n{tempo.por_extenso()} (horário de Brasília, America/Sao_Paulo).")
    partes.append("\n" + REGRAS_FERRAMENTAS.replace("{usuario}", USER_NAME).replace("{passos}", str(MAX_STEPS)))

    fixas = memory.importantes(25)
    try:
        relevantes = await memory.search(texto_usuario, 8)
    except Exception:  # noqa: BLE001 — memória nunca derruba a resposta
        relevantes = []
    vistos, linhas = set(), []
    for m in fixas + relevantes:
        if m["id"] in vistos:
            continue
        vistos.add(m["id"])
        linhas.append(f"- [{m['id']}] ({m['categoria']}) {m['conteudo']}")
    if linhas:
        partes.append(f"\n## O que você sabe sobre o {USER_NAME} (memória de longo prazo)\n" + "\n".join(linhas[:40])
                      + "\n(Use com naturalidade; não diga de onde veio. Se algo estiver desatualizado, confie no que "
                        "ele disser agora.)")
    if resumo:
        partes.append(f"\n## Resumo da conversa anterior\n{resumo}")
    return "\n".join(partes)


def _juntar_papeis(msgs: list[dict]) -> list[dict]:
    """Junta mensagens seguidas do mesmo papel e garante que a conversa começa pelo usuário."""
    saida: list[dict] = []
    for m in msgs:
        if m["role"] not in ("user", "assistant") or not (m.get("content") or "").strip():
            continue
        if saida and saida[-1]["role"] == m["role"]:
            saida[-1] = {"role": m["role"], "content": saida[-1]["content"] + "\n\n" + m["content"]}
        else:
            saida.append({"role": m["role"], "content": m["content"]})
    while saida and saida[0]["role"] != "user":
        saida.pop(0)
    return saida


async def montar_mensagens(texto_usuario: str) -> list[dict]:
    """System prompt + mensagens recentes (dentro do orçamento). Resumo cobre o que ficou para trás."""
    resumo = memory.ultimo_resumo()
    ate_id = resumo["ate_id"] if resumo else 0
    linhas = memory.mensagens_apos(ate_id, limite=max(HIST_RECENTES * 3, 60))
    escolhidas, total = [], 0
    for r in reversed(linhas):
        tamanho = len(r["conteudo"] or "")
        if escolhidas and (total + tamanho > CONTEXTO_CHARS or len(escolhidas) >= HIST_RECENTES * 3):
            break
        escolhidas.append(r)
        total += tamanho
    escolhidas.reverse()
    sistema = await build_system_prompt(texto_usuario, resumo["texto"] if resumo else None)
    conversa = _juntar_papeis([{"role": r["papel"], "content": r["conteudo"]} for r in escolhidas])
    return [{"role": "system", "content": sistema}] + conversa


async def atualizar_resumo() -> bool:
    """Se as mensagens após o último resumo passaram do orçamento, resume as antigas (mantém as N últimas)."""
    resumo = memory.ultimo_resumo()
    ate_id = resumo["ate_id"] if resumo else 0
    linhas = memory.mensagens_apos(ate_id, limite=1000)
    total = sum(len(r["conteudo"] or "") for r in linhas)
    if len(linhas) <= HIST_RECENTES or (total <= CONTEXTO_CHARS * 0.6 and len(linhas) <= HIST_RECENTES * 2):
        return False
    antigas = linhas[:-HIST_RECENTES]
    texto = await memory.resumir(antigas, resumo["texto"] if resumo else None, USER_NAME)
    if not texto:
        return False
    memory.salvar_resumo(texto, antigas[-1]["id"])
    return True
