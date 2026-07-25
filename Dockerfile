# Multi-stage: React build + Python API with both OCR backends and CAD support
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
# tesseract-ocr is the alternate OCR backend; the default (rapidocr) is pip-only.
# libgl1/libglib2.0-0 are required by the OpenCV build that rapidocr pulls in.
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
# Hosted platforms inject the listen port (Cloud Run, Fly) or expect a fixed one
# (Hugging Face Spaces uses 7860). Default to 8000 for local compose.
ENV PORT=8000

# Created before the build steps so ONNX model init and pip have a real HOME to
# write into, rather than a path that only exists after USER is switched.
RUN useradd --create-home --uid 1000 app
ENV HOME=/home/app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config
COPY eval ./eval
COPY data/registry.json ./data/registry.json
COPY data/samples/README.md ./data/samples/README.md

# Include the LiteLLM provider path in the image. Off by default: litellm is a
# large dependency and the demo runs the deterministic `extractive` provider, so
# shipping it would be weight for a code path the default deployment never
# executes. Build with --build-arg INSTALL_LLM=true to enable a hosted model.
# This never bakes in a key -- credentials stay runtime-only.
ARG INSTALL_LLM=false

# synthetic samples generated at build for offline demo
RUN if [ "$INSTALL_LLM" = "true" ]; then EXTRAS=".[dev,llm]"; else EXTRAS=".[dev]"; fi \
    && pip install --no-cache-dir -e "$EXTRAS" \
    && python -c "from scripts.make_synthetic_pid_pair import main as a; from scripts.make_secondary_pid_pair import main as b; from scripts.make_scanned_pair import main as c; from scripts.make_cad_pair import main as d; from scripts.build_eval_dataset import main as e; a(); b(); c(); d(); e()" \
    && python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"

COPY --from=frontend-build /frontend/dist ./frontend/dist

# Belt and braces: private drawings and planning notes are gitignored and
# dockerignored, but this guarantees they cannot reach a published image.
RUN rm -rf data/private_inputs tmp GROK_*.md || true \
    && mkdir -p artifacts data/private_inputs

# Run unprivileged. Required by Hugging Face Spaces (which runs as uid 1000) and
# correct everywhere else. artifacts/ is written at runtime, so it must be owned
# by the runtime user rather than root.
RUN chown -R app:app /app /home/app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health')" || exit 1

CMD ["sh", "-c", "exec python -m uvicorn delta_chat.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
