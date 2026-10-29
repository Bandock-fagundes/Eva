"""Permissões por ferramenta: 'permitir' | 'perguntar' | 'bloquear'.

- Ferramenta desconhecida = perguntar.
- Algumas ferramentas 'permitidas' viram 'perguntar' sozinhas em situações sensíveis
  (ex.: clicar em "Finalizar compra" ou digitar em campo de senha/cartão) — ver tools/__init__.py (sensivel).
- No banco ficam só as escolhas do Gabriel que diferem do padrão.
"""
import json

from . import audit
from .db import db

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
    "navegador_elementos": "permitir",
    "navegador_clicar": "permitir",     # vira 'perguntar' sozinho em botão de compra/pagamento
    "navegador_digitar": "permitir",    # vira 'perguntar' sozinho em senha/cartão/checkout
    "navegador_rolar": "permitir",
    "navegador_voltar": "permitir",
    "navegador_screenshot": "permitir",
    "navegador_fechar": "permitir",
    "agendar_lembrete": "permitir",
    "listar_lembretes": "permitir",
    "cancelar_lembrete": "permitir",
    "perguntar_ao_gabriel": "permitir",
    # Computador da Eva (PC Windows do Gabriel ou desktop na nuvem)
    "computador_ver_tela": "permitir",
    "computador_clicar": "permitir",    # vira 'perguntar' sozinho em tela de pagamento
    "computador_digitar": "permitir",   # vira 'perguntar' sozinho em login/senha/pagamento ou cartão/CPF no texto
    "computador_tecla": "permitir",     # Enter em tela de pagamento vira 'perguntar'
    "computador_rolar": "permitir",
    "computador_arrastar": "permitir",
    "computador_abrir": "permitir",
    "computador_comando": "perguntar",
    "computador_janelas": "permitir",
    "computador_area_transferencia": "permitir",  # ler vira 'perguntar' sozinho (pode ter senha copiada)
    "computador_arquivo": "perguntar",
}
VALIDOS = ("permitir", "perguntar", "bloquear")


class Permissions:
    def __init__(self):
        self.overrides: dict[str, str] = {}
        self.carregar()

    def carregar(self):
        self.overrides = {}
        try:
            salvo = json.loads(db.get("permissoes", "{}") or "{}")
            if isinstance(salvo, dict):
                self.overrides = {k: v for k, v in salvo.items() if v in VALIDOS and PADRAO.get(k) != v}
        except Exception:  # noqa: BLE001
            pass

    def _save(self):
        db.set("permissoes", json.dumps(self.overrides, ensure_ascii=False))

    def get(self, tool: str) -> str:
        return self.overrides.get(tool) or PADRAO.get(tool, "perguntar")

    def padrao(self, tool: str) -> str:
        return PADRAO.get(tool, "perguntar")

    def set(self, tool: str, value: str):
        if value not in VALIDOS:
            raise ValueError(f"valor inválido: {value}")
        if PADRAO.get(tool, "perguntar") == value:
            self.overrides.pop(tool, None)
        else:
            self.overrides[tool] = value
        self._save()
        audit.log("permissao_alterada", ferramenta=tool, valor=value)

    def restaurar(self):
        self.overrides = {}
        self._save()
        audit.log("permissoes_restauradas")


permissions = Permissions()
