FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libreoffice-calc fonts-noto-naskh-arabic fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py pipeline.py bot.py whatsapp.py parse_lessons.py ./
COPY templates/ ./templates/

ENV PORT=7860 GEN_WORK=/tmp/genwork LO_PROFILE=/app/loprofile
EXPOSE 8080
CMD ["python", "app.py"]
