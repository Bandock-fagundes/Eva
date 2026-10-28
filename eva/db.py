"""Banco de dados: Postgres (nuvem, quando DATABASE_URL existe) ou SQLite (local). Mesma interface.

- Migrações idempotentes: cria tabelas que faltam e adiciona colunas novas sem mexer nos dados antigos.
- Postgres (Neon) derruba conexões paradas: reconecta sozinho e repete a consulta uma vez.
- SQL é escrito com '?' e convertido para '%s' no Postgres.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR, DATABASE_URL

log = logging.getLogger("eva.db")

# (tabela, DDL base) — base igual à v0.2 para não quebrar o que já existe no Neon
TABELAS = [
    ("conversa", "CREATE TABLE IF NOT EXISTS conversa(id {pk}, ts TEXT, papel TEXT, conteudo TEXT)"),
    ("memorias", "CREATE TABLE IF NOT EXISTS memorias(id {pk}, ts TEXT, categoria TEXT, conteudo TEXT)"),
    ("audit", "CREATE TABLE IF NOT EXISTS audit(id {pk}, ts TEXT, tipo TEXT, dados TEXT)"),
    ("config", "CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT)"),
    ("resumos", "CREATE TABLE IF NOT EXISTS resumos(id {pk}, ts TEXT, ate_id INTEGER, texto TEXT)"),
    ("lembretes", "CREATE TABLE IF NOT EXISTS lembretes(id {pk}, criado TEXT, quando TEXT, texto TEXT, "
                  "status TEXT DEFAULT 'pendente', disparado_em TEXT)"),
]

# colunas adicionadas na v1.0 (ALTER TABLE ... ADD COLUMN IF NOT EXISTS)
COLUNAS_NOVAS = [
    ("memorias", "importancia", "INTEGER DEFAULT 3"),
    ("memorias", "atualizada", "TEXT"),
    ("memorias", "origem", "TEXT"),
    ("memorias", "vetor", "TEXT"),
]

INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_tipo ON audit(tipo)",
    "CREATE INDEX IF NOT EXISTS idx_lembretes_status ON lembretes(status)",
]


class DB:
    def __init__(self, url: str | None = None, sqlite_path: Path | str | None = None):
        self.url = DATABASE_URL if url is None else url
        self.pg = self.url.startswith(("postgres://", "postgresql://"))
        self.sqlite_path = Path(sqlite_path) if sqlite_path else DATA_DIR / "eva.db"
        self.lock = threading.RLock()
        self.conn = None
        self.ultimo_ok = 0.0
        for tentativa in range(4):  # Neon pode demorar alguns segundos para "acordar"
            try:
                self._connect()
                self.migrar()
                break
            except Exception as e:  # noqa: BLE001
                if tentativa == 3 or not self._erro_de_conexao(e):
                    raise
                log.warning("Banco não respondeu (%s); tentando de novo…", type(e).__name__)
                time.sleep(2 * (tentativa + 1))

    # ---------- conexão ----------
    def _connect(self):
        if self.pg:
            import psycopg
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:
                pass
            # prepare_threshold=None: compatível com o pooler do Neon (PgBouncer)
            self.conn = psycopg.connect(
                self.url, autocommit=True, connect_timeout=15, prepare_threshold=None,
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
            )
        else:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False, timeout=30)
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass

    def _erro_de_conexao(self, e: Exception) -> bool:
        if not self.pg:
            return False
        import psycopg
        return isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError))

    def _exec(self, sql: str, params=()):
        """Executa e devolve (descrição, linhas, rowcount, lastrowid). Tudo dentro do lock."""
        if self.pg:
            sql = sql.replace("?", "%s")
        with self.lock:
            for tentativa in range(3):
                try:
                    if self.pg and (self.conn is None or self.conn.closed):
                        self._connect()
                    cur = self.conn.execute(sql, tuple(params))
                    desc = cur.description
                    linhas = cur.fetchall() if desc else []
                    if not self.pg:
                        self.conn.commit()
                    self.ultimo_ok = time.time()
                    return desc, linhas, cur.rowcount, getattr(cur, "lastrowid", None)
                except Exception as e:  # noqa: BLE001
                    if self._erro_de_conexao(e) and tentativa < 2:
                        log.warning("Conexão com o Postgres caiu (%s); reconectando…", type(e).__name__)
                        time.sleep(0.3 * (tentativa + 1))
                        try:
                            self._connect()
                        except Exception as e2:  # noqa: BLE001
                            log.warning("Falha ao reconectar: %s", type(e2).__name__)
                        continue
                    raise

    # ---------- API ----------
    def run(self, sql: str, params=()) -> int:
        """Executa um comando; devolve quantas linhas mudaram."""
        return self._exec(sql, params)[2]

    def all(self, sql: str, params=()) -> list[dict]:
        desc, linhas, _, _ = self._exec(sql, params)
        if not desc:
            return []
        cols = [d[0] for d in desc]
        return [dict(zip(cols, r)) for r in linhas]

    def one(self, sql: str, params=()) -> dict | None:
        r = self.all(sql, params)
        return r[0] if r else None

    def insert(self, sql: str, params=()) -> int:
        if self.pg:
            _, linhas, _, _ = self._exec(sql + " RETURNING id", params)
            return int(linhas[0][0])
        return int(self._exec(sql, params)[3])

    def get(self, chave: str, padrao=None):
        r = self.one("SELECT valor FROM config WHERE chave=?", (chave,))
        return r["valor"] if r else padrao

    def set(self, chave: str, valor: str):
        self.run("INSERT INTO config(chave,valor) VALUES(?,?) "
                 "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))

    def delete_key(self, chave: str):
        self.run("DELETE FROM config WHERE chave=?", (chave,))

    def ok(self, cache_s: float = 0) -> bool:
        """Banco respondendo? Com cache_s, aceita uma consulta bem-sucedida recente (não acorda o Neon à toa)."""
        if cache_s and time.time() - self.ultimo_ok < cache_s:
            return True
        try:
            return self.one("SELECT 1 AS um") is not None
        except Exception:  # noqa: BLE001
            return False

    # ---------- migrações ----------
    def _colunas(self, tabela: str) -> set[str]:
        if self.pg:
            rows = self.all("SELECT column_name FROM information_schema.columns WHERE table_name=?", (tabela,))
            return {r["column_name"] for r in rows}
        return {r["name"] for r in self.all(f"PRAGMA table_info({tabela})")}

    def migrar(self):
        pk = "SERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY"
        for _, ddl in TABELAS:
            self.run(ddl.format(pk=pk))
        for tabela, coluna, tipo in COLUNAS_NOVAS:
            if self.pg:
                self.run(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna} {tipo}")
            elif coluna not in self._colunas(tabela):
                self.run(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
        for ddl in INDICES:
            self.run(ddl)

    def fechar(self):
        with self.lock:
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:  # noqa: BLE001
                pass


db = DB()
