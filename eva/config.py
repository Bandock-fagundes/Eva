"""Configurações da E.V.A. — tudo vem de variáveis de ambiente (ou do arquivo .env no PC)."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

VERSAO = "1.0"

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao


def _float(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao


def _bool(nome: str, padrao: bool) -> bool:
    v = os.getenv(nome)
    if v is None or v.strip() == "":
        return padrao
    return v.strip().lower() in ("1", "true", "sim", "yes", "on")


# ===== Cérebro (qualquer API compatível com OpenAI; padrão: Google Gemini) =====
LLM_BASE_URL = os.getenv("EVA_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_API_KEY = os.getenv("EVA_LLM_API_KEY", "")
LLM_MODEL = os.getenv("EVA_LLM_MODEL", "gemini-3.8-flash")
# lista extra de modelos reserva, separados por vírgula (opcional; o padrão já cobre os modelos grátis)
LLM_FALLBACKS = [m.strip() for m in os.getenv("EVA_LLM_FALLBACKS", "").split(",") if m.strip()]
LLM_TIMEOUT = _float("EVA_LLM_TIMEOUT", 90.0)          # segundos por chamada
LLM_TENTATIVAS = _int("EVA_LLM_TENTATIVAS", 4)        # tentativas por modelo em erro temporário
LLM_BACKOFF = _float("EVA_LLM_BACKOFF", 1.0)          # segundos base do backoff exponencial
LLM_PRAZO_TOTAL = _float("EVA_LLM_PRAZO_TOTAL", 240.0)  # tempo máximo somando todas as tentativas
LLM_STREAMING = _bool("EVA_LLM_STREAMING", True)
LLM_DESCOBRIR_MODELOS = _bool("EVA_LLM_DESCOBRIR_MODELOS", True)
EMBED_MODEL = os.getenv("EVA_EMBED_MODEL", "gemini-embedding-001")
# opcional: "low"/"medium"/"high" (Gemini 3 mapeia para thinking_level). Vazio = não envia (padrão do modelo).
LLM_RACIOCINIO = os.getenv("EVA_LLM_RACIOCINIO", "").strip()

# ===== Geral =====
USER_NAME = os.getenv("EVA_USER_NAME", "Gabriel")
FUSO = os.getenv("EVA_FUSO", "America/Sao_Paulo")
MAX_STEPS = _int("EVA_MAX_STEPS", 25)
APROVACAO_TIMEOUT = _float("EVA_APROVACAO_TIMEOUT", 300.0)   # 5 min sem resposta = negado
PERGUNTA_TIMEOUT = _float("EVA_PERGUNTA_TIMEOUT", 900.0)     # 15 min pra responder uma pergunta da Eva
FERRAMENTA_LIMITE_CHARS = _int("EVA_FERRAMENTA_LIMITE", 8000)

# Contexto: últimas N mensagens literais + resumo das antigas
HIST_RECENTES = _int("EVA_HIST_RECENTES", 16)
CONTEXTO_CHARS = _int("EVA_CONTEXTO_CHARS", 24000)

LEMBRETE_INTERVALO = _float("EVA_LEMBRETE_INTERVALO", 15.0)  # segundos entre checagens

# ===== Navegador =====
_sem_tela = sys.platform.startswith("linux") and not os.getenv("DISPLAY")
BROWSER_HEADLESS = _bool("EVA_BROWSER_HEADLESS", _sem_tela)
BROWSER_OCIOSO_MIN = _float("EVA_BROWSER_OCIOSO_MIN", 5.0)  # fecha o Chromium depois de X min parado
BROWSER_LEVE = _bool("EVA_BROWSER_LEVE", False)            # --single-process etc. (servidor com pouca RAM)
# por segurança, ler_pagina/navegador não acessam endereços internos (localhost, 10.x, 192.168.x...)
PERMITIR_ENDERECOS_LOCAIS = _bool("EVA_PERMITIR_ENDERECOS_LOCAIS", False)

# ===== Servidor =====
PORT = _int("PORT", _int("EVA_PORT", 8000))
HOST = os.getenv("EVA_HOST", "127.0.0.1")
SENHA = os.getenv("EVA_SENHA", "")
SEGREDO = os.getenv("EVA_SECRET", "")  # opcional: chave extra para assinar o cookie

# ===== Pastas e banco =====
DATA_DIR = Path(os.getenv("EVA_DATA_DIR", str(ROOT / "data")))          # SQLite local, prints
WORKSPACE = Path(os.getenv("EVA_WORKSPACE", str(ROOT / "workspace_eva")))  # única pasta de arquivos da Eva
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", "")
