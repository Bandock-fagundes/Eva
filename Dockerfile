# Imagem com Python + Chromium prontos (o "computador virtual" da Eva na nuvem)
# A versão da imagem PRECISA bater com playwright==1.55.0 do requirements.txt
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
WORKDIR /app
ENV TZ=America/Sao_Paulo \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    EVA_HOST=0.0.0.0 \
    EVA_BROWSER_HEADLESS=true \
    EVA_BROWSER_LEVE=true \
    MALLOC_ARENA_MAX=2
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# confere na hora do build que a app importa (e apaga o banco local criado pelo teste)
RUN python -c "import eva.server; print('E.V.A. importada com sucesso')" && rm -rf data workspace_eva
EXPOSE 8000
CMD ["python", "main.py", "--sem-abrir"]
