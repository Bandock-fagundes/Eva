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
    # o servidor falso é local: nunca passar por proxy
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
})
os.environ.pop("DATABASE_URL", None)
os.environ.pop("EVA_LLM_FALLBACKS", None)

from tests import fake_llm  # noqa: E402

_servidor_fake = fake_llm.iniciar(PORTA_FAKE)

from eva import agent as agent_mod  # noqa: E402
from eva.db import db  # noqa: E402
from eva.killswitch import kill_switch  # noqa: E402
from eva.lembretes import agendador  # noqa: E402
from eva.llm import cerebro  # noqa: E402
from eva.permissions import permissions  # noqa: E402


@pytest.fixture
def fake():
    return fake_llm.roteiro


@pytest.fixture(autouse=True)
def ambiente_limpo():
    for tabela in ("conversa", "memorias", "audit", "config", "resumos", "lembretes"):
        db.run(f"DELETE FROM {tabela}")
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
    yield
    agent_mod.ger.reset()


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
