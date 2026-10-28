"""Banco de dados: Postgres (nuvem, se DATABASE_URL existir) ou SQLite (local). Mesma interface."""
import sqlite3, threading
from .config import DATA_DIR, DATABASE_URL

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS conversa(id INTEGER PRIMARY KEY, ts TEXT, papel TEXT, conteudo TEXT);
CREATE TABLE IF NOT EXISTS memorias(id INTEGER PRIMARY KEY, ts TEXT, categoria TEXT, conteudo TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, tipo TEXT, dados TEXT);
CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT);
"""
DDL_PG = DDL_SQLITE.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")


class DB:
    def __init__(self):
        self.pg = DATABASE_URL.startswith("postgres")
        self.lock = threading.RLock()
        self._connect()
        for stmt in (DDL_PG if self.pg else DDL_SQLITE).split(";"):
            if stmt.strip():
                self.run(stmt)

    def _connect(self):
        if self.pg:
            import psycopg
            self.conn = psycopg.connect(DATABASE_URL, autocommit=True)
        else:
            self.conn = sqlite3.connect(DATA_DIR / "eva.db", check_same_thread=False)

    @property
    def like(self):
        return "ILIKE" if self.pg else "LIKE"

    def run(self, sql, params=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        with self.lock:
            try:
                cur = self.conn.execute(sql, params)
            except Exception:
                if not self.pg:
                    raise
                self._connect()  # Neon derruba conexão parada; reconecta e tenta de novo
                cur = self.conn.execute(sql, params)
            if not self.pg:
                self.conn.commit()
            return cur

    def all(self, sql, params=()):
        with self.lock:
            cur = self.run(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def insert(self, sql, params=()):
        with self.lock:
            if self.pg:
                return self.run(sql + " RETURNING id", params).fetchone()[0]
            return self.run(sql, params).lastrowid

    def get(self, chave, padrao=None):
        r = self.all("SELECT valor FROM config WHERE chave=?", (chave,))
        return r[0]["valor"] if r else padrao

    def set(self, chave, valor):
        self.run("INSERT INTO config(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))


db = DB()
