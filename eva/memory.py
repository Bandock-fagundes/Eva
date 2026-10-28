"""Memória da Eva: histórico da conversa + fatos duradouros sobre o Gabriel e o mundo dele."""
import re, time
from .db import db


class Memory:
    def add_message(self, papel, conteudo):
        db.run("INSERT INTO conversa(ts,papel,conteudo) VALUES(?,?,?)", (time.strftime("%Y-%m-%d %H:%M:%S"), papel, conteudo))

    def recent_messages(self, n=30):
        rows = db.all("SELECT papel, conteudo FROM conversa ORDER BY id DESC LIMIT ?", (n,))
        return [{"role": r["papel"], "content": r["conteudo"]} for r in reversed(rows)]

    def history(self, n=60):
        return db.all("SELECT ts, papel, conteudo FROM conversa ORDER BY id DESC LIMIT ?", (n,))[::-1]

    def remember(self, conteudo, categoria="geral"):
        return db.insert("INSERT INTO memorias(ts,categoria,conteudo) VALUES(?,?,?)", (time.strftime("%Y-%m-%d"), categoria, conteudo))

    def forget(self, mid):
        if not db.all("SELECT id FROM memorias WHERE id=?", (int(mid),)):
            return False
        db.run("DELETE FROM memorias WHERE id=?", (int(mid),))
        return True

    def search(self, consulta, limite=8):
        palavras = list({w for w in re.findall(r"\w+", consulta.lower()) if len(w) > 2})[:12]
        if not palavras:
            return []
        cond = " OR ".join(f"conteudo {db.like} ?" for _ in palavras)
        rows = db.all(f"SELECT id, ts, categoria, conteudo FROM memorias WHERE {cond} LIMIT 200", [f"%{w}%" for w in palavras])
        rows.sort(key=lambda r: -sum(w in r["conteudo"].lower() for w in palavras))
        return rows[:limite]

    def by_category(self, categoria, limite=50):
        return db.all("SELECT id, ts, categoria, conteudo FROM memorias WHERE categoria=? ORDER BY id DESC LIMIT ?", (categoria, limite))

    def all(self):
        return db.all("SELECT id, ts, categoria, conteudo FROM memorias ORDER BY id DESC")


memory = Memory()
