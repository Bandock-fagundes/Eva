"""Lembretes (parser + disparo) e ferramentas (arquivos, web, terminal)."""
import asyncio
from datetime import datetime, timedelta

import pytest

from eva import lembretes, tempo
from eva.db import db
from eva.lembretes import agendador, interpretar_quando
from eva.memory import memory

AGORA = datetime(2026, 10, 28, 12, 0, tzinfo=tempo.TZ)


@pytest.mark.parametrize("texto,esperado", [
    ("em 10 minutos", AGORA + timedelta(minutes=10)),
    ("daqui a 2 horas", AGORA + timedelta(hours=2)),
    ("em 1h30", AGORA + timedelta(minutes=90)),
    ("amanhã 9h", datetime(2026, 10, 29, 9, 0, tzinfo=tempo.TZ)),
    ("amanhã às 14:30", datetime(2026, 10, 29, 14, 30, tzinfo=tempo.TZ)),
    ("15:30", datetime(2026, 10, 28, 15, 30, tzinfo=tempo.TZ)),
    ("10h", datetime(2026, 10, 29, 10, 0, tzinfo=tempo.TZ)),  # já passou hoje -> amanhã
    ("2026-12-01 08:00", datetime(2026, 12, 1, 8, 0, tzinfo=tempo.TZ)),
    ("2026-12-01T08:00:00-03:00", datetime(2026, 12, 1, 8, 0, tzinfo=tempo.TZ)),
    ("25/12 20h", datetime(2026, 12, 25, 20, 0, tzinfo=tempo.TZ)),
    ("hoje às 8 da noite", datetime(2026, 10, 28, 20, 0, tzinfo=tempo.TZ)),
])
def test_interpretar_quando(texto, esperado):
    assert interpretar_quando(texto, AGORA) == esperado


def test_interpretar_quando_invalido():
    with pytest.raises(ValueError):
        interpretar_quando("quando der vontade", AGORA)
    with pytest.raises(ValueError):
        interpretar_quando("", AGORA)


async def test_lembrete_dispara_na_hora():
    disparos = []

    async def avisar(ev):
        disparos.append(ev)
    agendador.ao_disparar = avisar
    r = lembretes.criar("em 1 minuto", "beber água")
    assert await agendador.verificar() == []  # ainda não
    # adianta o relógio: coloca o lembrete no passado
    db.run("UPDATE lembretes SET quando=? WHERE id=?",
           (tempo.para_utc_iso(tempo.agora() - timedelta(seconds=5)), r["id"]))
    agendador.invalidar()
    evs = await agendador.verificar()
    assert len(evs) == 1 and evs[0]["tipo"] == "lembrete" and "beber água" in evs[0]["texto"]
    assert disparos == evs
    assert await agendador.verificar() == []  # não dispara duas vezes
    assert memory.history()[-1]["conteudo"].startswith("⏰ Lembrete: beber água")
    assert lembretes.listar() == []
    assert lembretes.listar(incluir_antigos=True)[0]["status"] == "disparado"


async def test_lembrete_atrasado_avisa():
    r = lembretes.criar("em 1 minuto", "reunião")
    db.run("UPDATE lembretes SET quando=? WHERE id=?",
           (tempo.para_utc_iso(tempo.agora() - timedelta(hours=1)), r["id"]))
    agendador.invalidar()
    evs = await agendador.verificar()
    assert evs[0]["atrasado"] and "dormindo" in evs[0]["texto"]


async def test_cancelar_lembrete():
    r = lembretes.criar("em 2 horas", "algo")
    assert lembretes.cancelar(r["id"])
    assert not lembretes.cancelar(r["id"])
    assert lembretes.listar() == []


async def test_lembrete_no_passado_e_recusado():
    with pytest.raises(ValueError):
        lembretes.criar("2020-01-01 10:00", "velho")


async def test_agente_agenda_lembrete(fake, rodar_tarefa):
    fake.adicionar({"tool_calls": [{"nome": "agendar_lembrete", "args": {"quando": "em 30 minutos", "texto": "ligar pra mãe"}}]},
                   {"texto": "Fechado, te lembro."})
    t = await rodar_tarefa("me lembra de ligar pra minha mãe daqui meia hora")
    assert t.resposta == "Fechado, te lembro."
    pend = lembretes.listar()
    assert len(pend) == 1 and pend[0]["texto"] == "ligar pra mãe"


async def test_loop_do_agendador_dispara_sozinho(monkeypatch):
    monkeypatch.setattr(lembretes, "LEMBRETE_INTERVALO", 0.05)
    disparos = []

    async def avisar(ev):
        disparos.append(ev)
    agendador.ao_disparar = avisar
    r = lembretes.criar("em 1 minuto", "teste loop")
    db.run("UPDATE lembretes SET quando=? WHERE id=?", (tempo.para_utc_iso(tempo.agora()), r["id"]))
    agendador.invalidar()
    agendador.iniciar()
    try:
        for _ in range(100):
            if disparos:
                break
            await asyncio.sleep(0.02)
    finally:
        await agendador.parar()
    assert disparos and disparos[0]["lembrete"] == "teste loop"


# ------------------------------------------------------------------ ferramentas

async def test_arquivos_nao_saem_do_workspace():
    from eva.tools.files import escrever_arquivo, ler_arquivo, listar_arquivos
    assert "Salvo" in await escrever_arquivo("pasta/a.txt", "conteúdo")
    assert await ler_arquivo("pasta/a.txt") == "conteúdo"
    assert "pasta" in await listar_arquivos(".")
    with pytest.raises(PermissionError):
        await ler_arquivo("../../etc/passwd")
    with pytest.raises(PermissionError):
        await escrever_arquivo("/etc/eva.txt", "x")


async def test_terminal_timeout_e_sem_segredos(monkeypatch):
    from eva.tools.shell import executar_comando
    monkeypatch.setenv("EVA_LLM_API_KEY", "segredo-que-nao-pode-vazar")
    r = await executar_comando("echo ola; env", 10)
    assert "ola" in r and "segredo-que-nao-pode-vazar" not in r
    r = await executar_comando("sleep 5", 1)
    assert "Tempo esgotado" in r


def test_extrair_texto_principal():
    from eva.tools.web import extrair_texto_principal
    html = """<html><head><title>Previsão Floripa</title><script>var x=1</script></head><body>
    <nav>Menu Início Contato</nav><article><h1>Tempo hoje</h1><p>Florianópolis terá sol com máxima de 27°C.</p>
    <p>""" + ("Texto do artigo. " * 40) + """</p></article><footer>Rodapé ©</footer></body></html>"""
    titulo, texto = extrair_texto_principal(html)
    assert titulo == "Previsão Floripa"
    assert "27°C" in texto and "Menu" not in texto and "Rodapé" not in texto and "var x" not in texto


def test_parse_ddg_html():
    from eva.tools.web import parse_ddg_html
    html = """<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemplo.com%2Fa">
    Título A</a><a class="result__snippet">Trecho A</a></div>
    <div class="result"><a class="result__a" href="https://b.com">Título B</a><div class="result__snippet">Trecho B</div></div>"""
    res = parse_ddg_html(html, 5)
    assert res[0] == {"title": "Título A", "href": "https://exemplo.com/a", "body": "Trecho A"}
    assert res[1]["href"] == "https://b.com"


async def test_buscar_web_usa_fallback(monkeypatch):
    from eva.tools import web

    def ddgs_quebrado(*a, **k):
        raise RuntimeError("ddgs fora do ar")

    async def html_ok(consulta, n):
        return [{"title": "Clima Floripa", "href": "https://clima.exemplo", "body": "Sol"}]

    async def sem_espera(s):
        pass
    monkeypatch.setattr(web, "_ddgs", ddgs_quebrado)
    monkeypatch.setattr(web, "_ddg_html", html_ok)
    monkeypatch.setattr(web.asyncio, "sleep", sem_espera)
    r = await web.buscar_web("clima florianópolis")
    assert "Clima Floripa" in r and "https://clima.exemplo" in r


async def test_buscar_web_tudo_falha_vira_texto(monkeypatch):
    from eva.tools import web

    def quebrado(*a, **k):
        raise RuntimeError("x")

    async def quebrado_async(*a, **k):
        raise RuntimeError("y")

    async def sem_espera(s):
        pass
    monkeypatch.setattr(web, "_ddgs", quebrado)
    monkeypatch.setattr(web, "_ddg_html", quebrado_async)
    monkeypatch.setattr(web.asyncio, "sleep", sem_espera)
    r = await web.buscar_web("algo")
    assert r.startswith("Não consegui resultados")


async def test_ler_pagina_bloqueia_enderecos_internos():
    from eva.tools.web import ler_pagina
    with pytest.raises(PermissionError):
        await ler_pagina("http://127.0.0.1:8000/api/estado")
    with pytest.raises(PermissionError):
        await ler_pagina("http://localhost/")
    with pytest.raises(PermissionError):
        await ler_pagina("http://169.254.169.254/latest/meta-data")


async def test_hora_atual_fuso_brasilia():
    from eva.tools.basics import hora_atual
    r = await hora_atual()
    assert "horário de Brasília" in r and "-03:00" in r
