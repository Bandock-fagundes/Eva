"""Botão de desligar. Fica desligada até o Gabriel ligar de novo (sobrevive a reinício)."""
from .db import db
from . import audit


class KillSwitch:
    @property
    def off(self):
        return db.get("desligada", "0") == "1"

    def kill(self, motivo="botão de desligar"):
        db.set("desligada", "1")
        audit.log("DESLIGADA", motivo=motivo)

    def revive(self):
        db.set("desligada", "0")
        audit.log("LIGADA")


kill_switch = KillSwitch()
