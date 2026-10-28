# Imagem com Python + Chromium prontos (o "computador virtual" da Eva na nuvem)
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
WORKDIR /app
ENV TZ=America/Sao_Paulo PYTHONUNBUFFERED=1 EVA_HOST=0.0.0.0 EVA_BROWSER_HEADLESS=true
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py", "--sem-abrir"]
