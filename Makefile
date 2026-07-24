.PHONY: samples lint format test eval demo verify frontend-install frontend-build frontend-lint api

PYTHON ?= .venv/Scripts/python.exe
ifeq ($(OS),Windows_NT)
  PYTHONPATH_SEP := ;
else
  PYTHON ?= .venv/bin/python
  PYTHONPATH_SEP := :
endif

export PYTHONPATH := src$(PYTHONPATH_SEP).

samples:
	$(PYTHON) -c "from scripts.make_synthetic_pid_pair import main; main()"
	$(PYTHON) -c "from scripts.make_scanned_pair import main; main()"
	$(PYTHON) -c "from scripts.build_eval_dataset import main; main()"

format:
	$(PYTHON) -m ruff format src tests scripts eval

lint:
	$(PYTHON) -m ruff check src tests scripts eval
	$(PYTHON) -m ruff format --check src tests scripts eval
	$(PYTHON) -m mypy src/delta_chat

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m eval.run

frontend-install:
	cd frontend && npm ci || npm install

frontend-lint:
	cd frontend && npm run build

frontend-build: frontend-lint

api:
	$(PYTHON) -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000

# One-command local demo: API serving React if dist exists; otherwise print Vite note
demo: frontend-build samples
	@echo "Starting API on http://127.0.0.1:8000 (serves frontend/dist)"
	$(PYTHON) -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000

verify: format lint test frontend-build eval
	@echo "VERIFY OK"

run-pair:
	$(PYTHON) -m delta_chat.cli run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B
