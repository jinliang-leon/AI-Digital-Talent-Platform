FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt requirements-core.txt requirements-resume.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt -r requirements-resume.txt
COPY backend ./backend
COPY alembic.ini ./
USER 10001
CMD ["sh", "-c", "python -m backend.app.cli migrate && python -m backend.app.cli init-storage && exec python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
