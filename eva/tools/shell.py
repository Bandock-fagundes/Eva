import asyncio
from . import tool
from ..config import WORKSPACE


@tool("executar_comando", "Executa um comando no terminal do computador (dentro de workspace_eva). Sempre pede aprovação por padrão.",
      {"comando": {"type": "string"}})
async def executar_comando(comando: str):
    proc = await asyncio.create_subprocess_shell(comando, cwd=str(WORKSPACE),
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return "Tempo esgotado (120s)."
    return f"(código {proc.returncode})\n" + out.decode(errors="replace")[-6000:]
