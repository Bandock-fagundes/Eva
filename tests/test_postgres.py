"""Banco Postgres real (como o Neon). Roda só se EVA_TEST_PG_URL estiver definido; senão é pulado com aviso.

Ex.: EVA_TEST_PG_URL=postgresql://eva@127.0.0.1:54329/evatest pytest tests/test_postgres.py
"""
import os

import pytest

PG_URL = os.getenv("EVA_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="EVA_TEST_PG_URL não definido — teste com Postgres real pulado")


@pytest.fixture
def pg_limpo():
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as c:
        for t in ("conversa", "memorias", "audit", "config", "resumos", "lembretes"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        # esquema e dados da v0.2 (como já existem no Neon)
        c.execute("CREATE TABLE conversa(id SERIAL PRIMARY KEY, ts TEXT, papel TEXT, conteudo TEXT)")
        c.execute("CREATE TABLE memorias(id SERIAL PRIMARY KEY, ts TEXT, categoria TEXT, conteudo TEXT)")
        c.execute("CREATE TABLE audit(id SERIAL PRIMARY KEY, ts TEXT, tipo TEXT, dados TEXT)")
        c.execute("CREATE TABLE config(chave TEXT PRIMARY KEY, valor TEXT)")
        c.execute("INSERT INTO conversa(ts,papel,conteudo) VALUES('2026-10-27 10:00:00','user','mensagem antiga da v0.2')")
        c.execute("INSERT INTO memorias(ts,categoria,conteudo) VALUES('2026-10-27','perfil','O Gabriel mora em Floripa.')")
        c.execute("INSERT INTO config(chave,valor) VALUES('persona','Persona antiga personalizada')")
    yield


def test_migracao_preserva_dados_v02(pg_limpo):
    from eva.db import DB
    d = DB(url=PG_URL)
    assert d.pg
    assert d.one("SELECT conteudo FROM conversa")["conteudo"] == "mensagem antiga da v0.2"
    m = d.one("SELECT * FROM memorias")
    assert m["conteudo"] == "O Gabriel mora em Floripa." and m["importancia"] == 3
    assert {"importancia", "atualizada", "origem", "vetor"} <= d._colunas("memorias")
    assert d.get("persona") == "Persona antiga personalizada"
    # idempotente: rodar de novo não quebra nada
    d.migrar()
    DB(url=PG_URL).migrar()
    assert d.one("SELECT COUNT(*) AS n FROM memorias")["n"] == 1
    d.fechar()


def test_crud_e_upsert(pg_limpo):
    from eva.db import DB
    d = DB(url=PG_URL)
    mid = d.insert("INSERT INTO memorias(ts,categoria,conteudo,importancia) VALUES(?,?,?,?)", ("x", "geral", "fato", 4))
    assert isinstance(mid, int) and mid > 1
    d.set("chave", "1")
    d.set("chave", "2")
    assert d.get("chave") == "2"
    assert d.run("UPDATE memorias SET conteudo=? WHERE id=?", ("novo", mid)) == 1
    lid = d.insert("INSERT INTO lembretes(criado,quando,texto,status) VALUES(?,?,?,?)", ("a", "b", "c", "pendente"))
    assert d.one("SELECT status FROM lembretes WHERE id=?", (lid,))["status"] == "pendente"
    d.delete_key("chave")
    assert d.get("chave") is None
    assert d.ok()
    d.fechar()


def test_reconecta_quando_o_neon_derruba_a_conexao(pg_limpo):
    import psycopg
    from eva.db import DB
    d = DB(url=PG_URL)
    pid = d.one("SELECT pg_backend_pid() AS p")["p"]
    with psycopg.connect(PG_URL, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    # a próxima consulta reconecta sozinha
    assert d.one("SELECT 1 AS um")["um"] == 1
    assert d.one("SELECT pg_backend_pid() AS p")["p"] != pid
    # e conexão fechada "por fora" também
    d.conn.close()
    assert d.get("persona") == "Persona antiga personalizada"
    d.fechar()


async def test_memoria_e_audit_funcionam_no_postgres(pg_limpo, monkeypatch):
    """Roda a camada de memória/auditoria inteira apontando para o Postgres."""
    from eva import audit as audit_mod
    from eva import db as db_mod
    from eva import memory as mem_mod
    from eva.db import DB
    d = DB(url=PG_URL)
    monkeypatch.setattr(mem_mod, "db", d)
    monkeypatch.setattr(audit_mod, "db", d)
    mem_mod.memory._invalidar()
    mid, acao = await mem_mod.memory.lembrar("O time do Gabriel é o Avaí.", "preferencias", 4)
    assert acao == "nova"
    _, acao2 = await mem_mod.memory.lembrar("O time do Gabriel é o Avaí", "preferencias")
    assert acao2 == "igual"
    res = await mem_mod.memory.search("time")
    assert res and res[0]["id"] == mid
    audit_mod.log("teste_pg", valor=1)
    pag = audit_mod.pagina(1, 10, "teste_pg")
    assert pag["total"] == 1 and pag["itens"][0]["valor"] == 1
    assert audit_mod.pagina(1, 10, busca="teste_pg")["total"] >= 1
    assert db_mod.db is not d
    mem_mod.memory._invalidar()
    d.fechar()
