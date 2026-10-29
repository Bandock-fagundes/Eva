"""Testes do EVA-Agente. Rodam no Linux (plataforma falsa) e no Windows (CI). Nada sai para a internet."""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("EVA_AGENTE_PASTA", tempfile.mkdtemp(prefix="eva_agente_teste_"))
# as conexões dos testes são locais: nunca passar por proxy
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
