"""Onde o EVA-Agente guarda as coisas: %APPDATA%\\EVA-Agente (Windows) ou ~/.config/eva-agente (testes no Linux).

config.json: endereço da Eva, id/nome do dispositivo e o token. No Windows o token vai cifrado com DPAPI
(só a conta do Gabriel neste PC decifra); fora do Windows vai num arquivo com permissão só do dono (0600).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import NOME, SERVIDOR_PADRAO, dpapi

log = logging.getLogger("eva_agente")


def pasta() -> Path:
    if os.getenv("EVA_AGENTE_PASTA"):
        p = Path(os.environ["EVA_AGENTE_PASTA"])
    elif sys.platform == "win32":
        p = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / NOME
    else:
        p = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "eva-agente"
    p.mkdir(parents=True, exist_ok=True)
    return p


def arquivo_config() -> Path:
    return pasta() / "config.json"


def arquivo_registro() -> Path:
    return pasta() / "registro.log"


@dataclass
class Config:
    servidor: str = SERVIDOR_PADRAO
    dispositivo_id: int | None = None
    nome: str = ""
    token: str = ""
    manter_eva_acordada: bool = True     # ping leve em /api/saude a cada 10 min enquanto conectado (Render grátis)
    extras: dict = field(default_factory=dict)

    @property
    def pareado(self) -> bool:
        return bool(self.servidor and self.token)


def carregar() -> Config:
    arq = arquivo_config()
    if not arq.exists():
        return Config()
    try:
        d = json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("config.json ilegível; começando do zero")
        return Config()
    token = ""
    if d.get("token_dpapi"):
        try:
            token = dpapi.decifrar(base64.b64decode(d["token_dpapi"])).decode()
        except (OSError, ValueError) as e:
            log.warning("não consegui decifrar o token (%s); vai precisar parear de novo", e)
    elif d.get("token_arquivo"):
        token = str(d["token_arquivo"])
    return Config(servidor=str(d.get("servidor") or SERVIDOR_PADRAO), dispositivo_id=d.get("dispositivo_id"),
                  nome=str(d.get("nome") or ""), token=token,
                  manter_eva_acordada=bool(d.get("manter_eva_acordada", True)), extras=dict(d.get("extras") or {}))


def salvar(c: Config):
    d = asdict(c)
    token = d.pop("token")
    if token:
        if dpapi.disponivel():
            try:
                d["token_dpapi"] = base64.b64encode(dpapi.cifrar(token.encode())).decode()
            except OSError as e:
                log.warning("DPAPI falhou (%s); guardando o token só com permissão de arquivo", e)
                d["token_arquivo"] = token
        else:
            d["token_arquivo"] = token
    arq = arquivo_config()
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, arq)


def esquecer_token():
    c = carregar()
    c.token = ""
    c.dispositivo_id = None
    salvar(c)


def configurar_registro(nivel=logging.INFO) -> logging.Logger:
    """Registro local de TODAS as ações (registro.log, até ~3 MB, gira sozinho)."""
    lg = logging.getLogger("eva_agente")
    lg.setLevel(nivel)
    if not any(isinstance(h, RotatingFileHandler) for h in lg.handlers):
        h = RotatingFileHandler(arquivo_registro(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%d/%m/%Y %H:%M:%S"))
        lg.addHandler(h)
    return lg


# ---------------------------------------------------------------- iniciar com o Windows

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _comando_inicio() -> str:
    exe = sys.executable if getattr(sys, "frozen", False) else f'{sys.executable}" "{Path(sys.argv[0]).resolve()}'
    return f'"{exe}" --inicio'


def inicia_com_windows() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            winreg.QueryValueEx(k, NOME)
            return True
    except OSError:
        return False


def definir_inicio_com_windows(ligar: bool) -> bool:
    """Liga/desliga 'iniciar com o Windows' (só para este usuário; sem precisar de administrador)."""
    if sys.platform != "win32":
        return False
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if ligar:
            winreg.SetValueEx(k, NOME, 0, winreg.REG_SZ, _comando_inicio())
        else:
            try:
                winreg.DeleteValue(k, NOME)
            except FileNotFoundError:
                pass
    return ligar
