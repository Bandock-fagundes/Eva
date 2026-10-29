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
{navegador}- lembrar: quando o {usuario} pedir para lembrar algo, ou contar algo duradouro e importante. Salve sem anunciar.
  buscar_memoria: quando precisar de algo que ele já contou e não está no contexto abaixo.
- agendar_lembrete: quando ele pedir para ser lembrado de algo numa data/hora. Use a data/hora atual abaixo
  para calcular; formato 'AAAA-MM-DD HH:MM' (horário de Brasília) ou 'em 30 minutos'.
- perguntar_ao_gabriel: só quando estiver travada numa decisão que é dele (gosto pessoal, dinheiro, dados que
  só ele tem). Não use para coisas que você consegue descobrir.
- Se uma ferramenta der ERRO, leia a mensagem, corrija os argumentos ou tente outro caminho (no máximo 2 vezes
  a mesma coisa). Se for NEGADO ou BLOQUEADO, não insista nem contorne: explique e ofereça alternativa.
- Você tem até {passos} passos de ferramenta por pedido. Seja eficiente.
- Nunca diga que fez algo que não fez. Nunca invente resultado de ferramenta."""


REGRA_NAVEGADOR = """- navegador_*: só quando precisa interagir com um site (clicar, preencher, logar, ver algo dinâmico).
  Fluxo: navegador_abrir -> leia os elementos numerados -> navegador_clicar/navegador_digitar pelo número.
  Para só ler conteúdo, prefira ler_pagina (mais rápido e leve).
"""

REGRA_PC_OFFLINE = """- O teu computador na nuvem (computador_*) está DESLIGADO ou fora do ar agora. Se o pedido precisar dele,
  avise o {usuario} em uma frase e use o navegador_* como reserva quando der.
"""

REGRA_PC_ONLINE = """- computador_*: o TEU computador de verdade (detalhes na seção abaixo). Use sempre que o pedido for usar o
  computador, mexer num site (clicar, preencher, logar, baixar), criar ou editar documentos/planilhas.
  Para só ler/pesquisar informação, buscar_web e ler_pagina continuam mais rápidos e baratos.
"""

REGRAS_COMPUTADOR = """## Teu computador (desktop Linux na nuvem, que o {usuario} assiste AO VIVO na aba Tela)
- Área de trabalho XFCE com Chromium, LibreOffice (texto, planilha, apresentação), arquivos, editor e terminal.
- Coordenadas: grade 0–1000 em cada eixo, qualquer que seja a resolução: (0,0) = canto superior esquerdo,
  (1000,1000) = canto inferior direito, (500,500) = centro. Mire sempre no CENTRO do botão/campo.
- Depois de cada ação você recebe um print novo. Fluxo: PLANEJE em 2 a 5 passos -> OLHE o print antes de agir ->
  aja -> CONFIRA no print seguinte se deu certo (a página carregou? o clique pegou? o texto apareceu?).
  Se nada mudou, espere (computador_ver_tela com esperar) ou tente outro jeito; nunca repita o mesmo clique
  às cegas mais de 2 vezes.
- Prefira atalhos de teclado: ctrl+l (barra de endereço), ctrl+t (nova aba), ctrl+w (fechar aba), ctrl+f (procurar),
  alt+tab, ctrl+s, ctrl+c/ctrl+v, tab para pular de campo, enter para confirmar, esc para fechar pop-up.
- Para abrir um site use computador_abrir com a url. Para digitar, clique no campo primeiro e depois use
  computador_digitar (limpar=true troca o que já estava escrito).
- Pop-ups de cookies/anúncios: feche e siga. Página pesada: espere alguns segundos antes de concluir que falhou.
- computador_comando (terminal, pede aprovação): use quando for bem mais simples que clicar (arquivos, instalar algo).
- NUNCA digite senhas, números de cartão, CPF ou códigos de verificação, e NUNCA confirme compra, pagamento, envio
  de mensagem ou publicação sem aprovação explícita do {usuario}. Em tela de login, peça para ele clicar em
  "Assumir controle" na aba Tela e fazer o login ele mesmo; depois ele devolve o controle pra você.
- Se o {usuario} assumir o controle, pare de mexer no computador até ele devolver.
- Ao terminar, diga em 1 ou 2 frases o que fez e o que ficou na tela."""


REGRA_PC_WINDOWS_ONLINE = """- computador_*: o PC Windows DE VERDADE do {usuario}, com os arquivos, contas e programas dele (detalhes na
  seção abaixo). Use quando o pedido for mexer no computador dele, num site logado, em documentos/planilhas.
  Para só ler/pesquisar informação, buscar_web e ler_pagina continuam mais rápidos e baratos.
"""

REGRA_PC_WINDOWS_OFFLINE = """- O PC do {usuario} (computador_*) NÃO está conectado agora (EVA-Agente fechado, PC desligado ou sem internet).
  Se o pedido precisar do PC dele, avise em uma frase e peça para ele abrir o EVA-Agente; use o navegador_* como
  reserva quando der.
"""

REGRAS_COMPUTADOR_WINDOWS = """## O PC do {usuario} (Windows 10/11 de verdade, que ele assiste AO VIVO na aba Tela)
- É o computador PESSOAL dele, não um computador de testes: tem trabalho aberto, arquivos, contas logadas e
  conversas. Trate tudo com cuidado e respeito, como se ele estivesse olhando por cima do teu ombro (às vezes está).
- Coordenadas: grade 0–1000 em cada eixo, qualquer que seja a resolução: (0,0) = canto superior esquerdo,
  (1000,1000) = canto inferior direito, (500,500) = centro. Mire sempre no CENTRO do botão/campo.
- Depois de cada ação você recebe um print novo. Fluxo: PLANEJE em 2 a 5 passos -> OLHE o print antes de agir ->
  aja -> CONFIRA no print seguinte se deu certo. Se nada mudou, espere (computador_ver_tela com esperar) ou tente
  outro jeito; nunca repita o mesmo clique às cegas mais de 2 vezes.
- Atalhos do Windows: win (menu Iniciar; digite o nome do programa e enter), win+d (área de trabalho), win+e
  (Explorador de Arquivos), alt+tab, ctrl+l (barra de endereço), ctrl+t (nova aba), ctrl+f, ctrl+c/ctrl+v, tab, esc.
  computador_janelas lista as janelas e traz uma para a frente pelo nome (melhor que alt+tab).
- Para abrir um site use computador_abrir com a url (abre no navegador padrão dele, já logado nas contas dele).
  Para digitar, clique no campo primeiro e depois use computador_digitar.
- computador_comando roda no PowerShell (pede aprovação). Use só quando for bem mais simples que clicar.
- REGRAS DE CUIDADO (obrigatórias):
  - NUNCA feche janelas, abas ou programas que você não abriu nesta tarefa, e NUNCA descarte trabalho não salvo.
    Se aparecer "Deseja salvar as alterações?", escolha Cancelar e pergunte ao {usuario}. Evite alt+f4.
  - PERGUNTE ANTES (perguntar_ao_gabriel) de: apagar ou mover arquivos, instalar/desinstalar programas, mudar
    configurações do Windows, enviar mensagens/e-mails/posts, comprar ou pagar qualquer coisa.
  - NUNCA digite senhas, números de cartão, CPF ou códigos de verificação. Em tela de login, peça para ele clicar em
    "Assumir controle" na aba Tela (ou usar o próprio PC) e fazer o login ele mesmo; depois ele devolve o controle.
  - Janelas do "Controle de Conta de Usuário" (UAC, tela escurecida pedindo "Sim/Não") e a tela de bloqueio você
    NÃO consegue ver nem clicar (o Windows protege): peça para o {usuario} responder no PC.
  - Não fique abrindo coisas pessoais dele (fotos, conversas, e-mails) além do necessário para o pedido.
- Se o {usuario} assumir o controle ou PAUSAR a Eva no PC (ícone ou Ctrl+Alt+Shift+P), pare e espere ele liberar.
- Ao terminar, diga em 1 ou 2 frases o que fez e o que ficou na tela."""


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


async def build_system_prompt(texto_usuario: str, resumo: str | None = None, computador: str | None = None,
                             pc_tipo: str | None = None) -> str:
    """computador: None (não configurado) | "online" | "offline".  pc_tipo: "windows" (PC do Gabriel) | "nuvem"."""
    partes = [persona()]
    partes.append(f"\n## Agora\n{tempo.por_extenso()} (horário de Brasília, America/Sao_Paulo).")
    if pc_tipo == "windows":
        nav = {"online": REGRA_PC_WINDOWS_ONLINE, "offline": REGRA_NAVEGADOR + REGRA_PC_WINDOWS_OFFLINE}.get(
            computador or "", REGRA_NAVEGADOR)
    else:
        nav = {"online": REGRA_PC_ONLINE, "offline": REGRA_NAVEGADOR + REGRA_PC_OFFLINE}.get(computador or "",
                                                                                           REGRA_NAVEGADOR)
    regras = REGRAS_FERRAMENTAS.replace("{navegador}", nav)
    partes.append("\n" + regras.replace("{usuario}", USER_NAME).replace("{passos}", str(MAX_STEPS)))
    if computador == "online":
        guia = REGRAS_COMPUTADOR_WINDOWS if pc_tipo == "windows" else REGRAS_COMPUTADOR
        partes.append("\n" + guia.replace("{usuario}", USER_NAME))

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


async def montar_mensagens(texto_usuario: str, computador: str | None = None, pc_tipo: str | None = None) -> list[dict]:
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
    sistema = await build_system_prompt(texto_usuario, resumo["texto"] if resumo else None, computador, pc_tipo)
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
