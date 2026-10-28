"""Terminal — pede aprovação por padrão, roda dentro de workspace_eva, com tempo limite."""
import asyncio
import os
import signal
import sys

from ..config import WORKSPACE
from . import tool

LIMITE_SAIDA = 6000
_SEGREDOS = ("KEY", "SENHA", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "PASS")


def _env_limpo() -> dict:
    """Ambiente do comando sem as variáveis secretas (chave da API, senha, banco)."""
    return {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in _SEGREDOS)}


def _matar(proc):
    try:
        if sys.platform != "win32":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


@tool("executar_comando", "Executa um comando no terminal do servidor (dentro de workspace_eva). "
      "Pede aprovação por padrão.",
      {"comando": {"type": "string"}, "timeout": {"type": "integer", "description": "segundos (padrão 60, máx. 300)"}},
      required=["comando"], rotulo="Rodando um comando", timeout=320)
async def executar_comando(comando: str, timeout: int = 60):
    timeout = max(1, min(300, int(timeout or 60)))
    kwargs = {"start_new_session": True} if sys.platform != "win32" else {}
    proc = await asyncio.create_subprocess_shell(comando, cwd=str(WORKSPACE), stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT, env=_env_limpo(), **kwargs)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _matar(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        return f"Tempo esgotado ({timeout}s). Comando interrompido."
    except asyncio.CancelledError:
        _matar(proc)
        raise
    texto = out.decode(errors="replace")
    if len(texto) > LIMITE_SAIDA:
        texto = texto[:2000] + f"\n… ({len(texto) - LIMITE_SAIDA} caracteres omitidos) …\n" + texto[-4000:]
    return f"(código de saída {proc.returncode})\n{texto}"
