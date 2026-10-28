import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LLM_BASE_URL = os.getenv("EVA_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_API_KEY = os.getenv("EVA_LLM_API_KEY", "")
LLM_MODEL = os.getenv("EVA_LLM_MODEL", "gemini-3.8-flash")
USER_NAME = os.getenv("EVA_USER_NAME", "Gabriel")
MAX_STEPS = int(os.getenv("EVA_MAX_STEPS", "15"))
BROWSER_HEADLESS = os.getenv("EVA_BROWSER_HEADLESS", "false").lower() == "true"
PORT = int(os.getenv("PORT", os.getenv("EVA_PORT", "8000")))

DATA_DIR = ROOT / "data"            # memória, log, permissões
WORKSPACE = ROOT / "workspace_eva"  # única pasta onde a Eva pode mexer em arquivos
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE.mkdir(parents=True, exist_ok=True)

# Nuvem: banco Postgres (Neon) pra memória não sumir; senha pra só você entrar
DATABASE_URL = os.getenv("DATABASE_URL", "")
SENHA = os.getenv("EVA_SENHA", "")
HOST = os.getenv("EVA_HOST", "127.0.0.1")
