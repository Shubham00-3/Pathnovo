FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY config ./config
COPY eval ./eval
COPY tests ./tests
COPY data ./data
COPY Makefile ./

RUN pip install --no-cache-dir -e ".[dev]"

ENV PYTHONPATH=src:.
ENV LLM_PROVIDER=extractive

RUN python -c "from scripts.make_synthetic_pid_pair import main as a; from scripts.make_scanned_pair import main as b; from scripts.build_eval_dataset import main as c; a(); b(); c()"

EXPOSE 8501
CMD ["streamlit", "run", "src/delta_chat/ui/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
