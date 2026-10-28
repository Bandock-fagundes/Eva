@echo off
cd /d %~dp0
if not exist .venv (
  echo Primeira vez: instalando a Eva...
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate
)
if not exist .env (
  copy .env.example .env
  echo.
  echo Abra o arquivo .env e cole sua chave da API, depois rode de novo.
  notepad .env
  pause
  exit /b
)
python main.py
