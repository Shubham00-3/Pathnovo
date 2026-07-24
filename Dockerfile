# Multi-stage: React build + Python API with Tesseract
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=src:.
ENV LLM_PROVIDER=extractive
ENV DELTA_CHAT_CONFIG=config/default.yaml
ENV PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config
COPY eval ./eval
COPY data/registry.json ./data/registry.json
COPY data/samples/README.md ./data/samples/README.md
# synthetic samples generated at build for offline demo
RUN pip install --no-cache-dir -e ".[dev]" \
    && python -c "from scripts.make_synthetic_pid_pair import main as a; from scripts.make_scanned_pair import main as b; from scripts.build_eval_dataset import main as c; a(); b(); c()"

COPY --from=frontend-build /frontend/dist ./frontend/dist

# Ensure private inputs are never present
RUN rm -rf data/private_inputs tmp GROK_*.md || true \
    && mkdir -p artifacts data/private_inputs \
    && echo "local demo only" > /app/LOCAL_ONLY.txt

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "delta_chat.api:app", "--host", "0.0.0.0", "--port", "8000"]
