FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELS_DIR=/app/models

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/download_models.py scripts/download_models.py
RUN python scripts/download_models.py /app/models

COPY . .

CMD ["python", "bot.py"]
