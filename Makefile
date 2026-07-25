.PHONY: samples lint format format-check typecheck test eval eval-compare eval-baseline demo verify verify-py frontend-install frontend-build frontend-lint frontend-test api

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
	$(PYTHON) -c "from scripts.make_secondary_pid_pair import main; main()"
	$(PYTHON) -c "from scripts.make_scanned_pair import main; main()"
	$(PYTHON) -c "from scripts.make_cad_pair import main; main()"
	$(PYTHON) -c "from scripts.build_eval_dataset import main; main()"

format:
	$(PYTHON) -m ruff format src tests scripts eval

format-check:
	$(PYTHON) -m ruff format --check src tests scripts eval

typecheck:
	$(PYTHON) -m mypy src scripts eval

lint: format-check typecheck
	$(PYTHON) -m ruff check src tests scripts eval

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m eval.run

# Diff the last scorecard against the committed baseline; non-zero on regression.
eval-compare:
	$(PYTHON) -m eval.compare

# Promote the last scorecard to the new baseline (review the diff first).
eval-baseline:
	$(PYTHON) -m eval.compare --update-baseline

frontend-install:
	cd frontend && npm ci || npm install

frontend-lint:
	cd frontend && npm run lint

frontend-test:
	cd frontend && npm run test

frontend-build:
	cd frontend && npm run build

api:
	$(PYTHON) -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000

# One-command local demo: API serving React if dist exists; otherwise print Vite note
demo: frontend-build samples
	@echo "Starting API on http://127.0.0.1:8000 (serves frontend/dist)"
	$(PYTHON) -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000

verify: samples lint test frontend-lint frontend-test frontend-build eval eval-compare
	@echo "VERIFY OK"

# Same chain without make, for hosts that do not have it (e.g. stock Windows).
verify-py:
	$(PYTHON) scripts/verify.py

run-pair:
	$(PYTHON) -m delta_chat.cli run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B
