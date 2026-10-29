"""Configuração dos testes: banco SQLite temporário + servidor LLM falso local (nada sai para a internet)."""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = tempfile.mkdtemp(prefix="eva_teste_")


def _porta_livre() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORTA_FAKE = _porta_livre()
PORTA_PC = _porta_livre()
SENHA_TESTE = "senha-de-teste-123"

# precisa vir ANTES de importar a eva (config lê o ambiente na importação)
os.environ.update({
    "EVA_DATA_DIR": _TMP,
    "EVA_WORKSPACE": os.path.join(_TMP, "workspace"),
    "EVA_LLM_BASE_URL": f"http://127.0.0.1:{PORTA_FAKE}/",
    "EVA_LLM_API_KEY": "chave-falsa-de-teste",
    "EVA_LLM_MODEL": "gemini-3.8-flash",
    "EVA_LLM_BACKOFF": "0.01",
    "EVA_LLM_TIMEOUT": "10",
    "EVA_LLM_DESCOBRIR_MODELOS": "0",
    "EVA_SENHA": SENHA_TESTE,
    "EVA_HOST": "127.0.0.1",
    "EVA_LEMBRETE_INTERVALO": "3600",
    "EVA_USER_NAME": "Gabriel",
    # computador da Eva: desligado por padrão nos testes (os testes do computador ligam o falso)
    "EVA_PC_URL": "",
    "EVA_PC_TOKEN": "",
    "EVA_PC_VNC_URL": "",
    "EVA_PC_VNC_SENHA": "",
    "EVA_PC_ESPERA": "0",
    # o servidor falso é local: nunca passar por proxy
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
})
os.environ.pop("DATABASE_URL", None)
os.environ.pop("EVA_LLM_FALLBACKS", None)

from tests import fake_llm, fake_pc  # noqa: E402

_servidor_fake = fake_llm.iniciar(PORTA_FAKE)
_servidor_pc = fake_pc.iniciar(PORTA_PC)

from eva import agent as agent_mod  # noqa: E402
from eva.db import db  # noqa: E402
from eva.killswitch import kill_switch  # noqa: E402
from eva.lembretes import agendador  # noqa: E402
from eva.llm import cerebro  # noqa: E402
from eva.permissions import permissions  # noqa: E402
from eva.computador import pc  # noqa: E402


@pytest.fixture
def fake():
    return fake_llm.roteiro


@pytest.fixture(autouse=True)
def ambiente_limpo():
    for tabela in ("conversa", "memorias", "audit", "config", "resumos", "lembretes", "dispositivos"):
        db.run(f"DELETE FROM {tabela}")
    from eva.pc_hub import hub
    hub.reset()
    fake_llm.roteiro.reset()
    from eva.memory import memory
    memory._invalidar()
    cerebro.configurar(base_url=f"http://127.0.0.1:{PORTA_FAKE}/", api_key="chave-falsa-de-teste",
                       modelo="gemini-3.8-flash", fallbacks=[], backoff=0.01, timeout=10, streaming=True)
    agent_mod.ger.reset()
    kill_switch._off = False
    permissions.carregar()
    agendador.invalidar()
    agendador.ao_disparar = None
    from eva import server
    server.zerar_limites()
    pc.configurar(url="", token="", vnc_url="", vnc_senha="")
    pc.controle_gabriel = False
    fake_pc.estado.reset()
    yield
    agent_mod.ger.reset()
    hub.reset()


@pytest.fixture
def eventos():
    """Lista que recebe todos os eventos transmitidos pelo gerenciador."""
    lista = []
    agent_mod.ger.adicionar_ouvinte(lista.append)
    return lista


async def rodar(texto: str, politica: str = "perguntar", timeout: float = 15):
    t = await agent_mod.ger.enviar(texto, origem="teste", politica=politica)
    await asyncio.wait_for(t.fim.wait(), timeout)
    return t


@pytest.fixture
def rodar_tarefa():
    return rodar


@pytest.fixture
def pc_fake():
    """Liga a Eva no agente do PC falso (tests/fake_pc.py)."""
    pc.configurar(url=f"http://127.0.0.1:{PORTA_PC}", token=fake_pc.TOKEN,
                  vnc_url="https://1-2-3-4.sslip.io/vnc/", vnc_senha="senhaVnc0123456789abcdef")
    fake_pc.estado.reset()
    yield fake_pc.estado
    pc.configurar(url="", token="", vnc_url="", vnc_senha="")
