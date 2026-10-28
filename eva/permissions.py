"""Permissões por ação: 'permitir' | 'perguntar' | 'bloquear'. Ferramenta desconhecida = perguntar."""
import json
from .db import db
from . import audit

PADRAO = {
    "hora_atual": "permitir",
    "buscar_web": "permitir",
    "ler_pagina": "permitir",
    "lembrar": "permitir",
    "buscar_memoria": "permitir",
    "esquecer": "perguntar",
    "listar_arquivos": "permitir",
    "ler_arquivo": "permitir",
    "escrever_arquivo": "perguntar",
    "executar_comando": "perguntar",
    "navegador_abrir": "permitir",
    "navegador_ler": "permitir",
    "navegador_screenshot": "permitir",
    "navegador_clicar": "perguntar",
    "navegador_digitar": "perguntar",
}
VALIDOS = {"permitir", "perguntar", "bloquear"}


class Permissions:
    def __init__(self):
        self.rules = dict(PADRAO)
        try:
            self.rules.update(json.loads(db.get("permissoes", "{}")))
        except Exception:
            pass

    def _save(self):
        db.set("permissoes", json.dumps(self.rules, ensure_ascii=False))

    def get(self, tool: str) -> str:
        return self.rules.get(tool, "perguntar")

    def set(self, tool: str, value: str):
        if value not in VALIDOS:
            raise ValueError(f"valor inválido: {value}")
        self.rules[tool] = value
        self._save()
        audit.log("permissao_alterada", ferramenta=tool, valor=value)


permissions = Permissions()
