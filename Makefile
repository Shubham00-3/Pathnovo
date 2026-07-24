.PHONY: samples lint test eval demo install sync

UV ?= uv
PYTHONPATH := src:.

sync:
	$(UV) sync --all-extras

install: sync
	$(UV) pip install -e ".[dev]"

samples:
	$(UV) run python -c "from scripts.make_synthetic_pid_pair import main; main()"
	$(UV) run python -c "from scripts.make_scanned_pair import main; main()"
	$(UV) run python -c "from scripts.build_eval_dataset import main; main()"

lint:
	$(UV) run ruff check src tests scripts eval
	$(UV) run ruff format --check src tests scripts eval || true

test:
	$(UV) run pytest -q

eval:
	$(UV) run python -m eval.run

demo:
	$(UV) run streamlit run src/delta_chat/ui/app.py --server.headless true --server.port 8501

run-pair:
	$(UV) run delta-chat run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B

chat:
	@echo "Usage: uv run delta-chat chat --run-id <id> -q 'What changed?'"
