"""Botão de desligar. Fica desligada até o Gabriel ligar de novo (sobrevive a reinício do servidor).

O estado fica no banco e numa cópia em memória (checada muitas vezes por tarefa, sem ir ao banco).
O efeito completo (cancelar tarefas, fechar navegador) está em agent.desligar_tudo().
"""
from . import audit
from .db import db


class KillSwitch:
    def __init__(self):
        self._off: bool | None = None

    @property
    def off(self) -> bool:
        if self._off is None:
            try:
                self._off = db.get("desligada", "0") == "1"
            except Exception:  # noqa: BLE001 — sem banco: assume ligada, mas tenta de novo depois
                return False
        return self._off

    def kill(self, motivo: str = "botão de desligar"):
        self._off = True
        db.set("desligada", "1")
        audit.log("DESLIGADA", motivo=motivo)

    def revive(self):
        self._off = False
        db.set("desligada", "0")
        audit.log("LIGADA")

    def recarregar(self):
        self._off = None


kill_switch = KillSwitch()
