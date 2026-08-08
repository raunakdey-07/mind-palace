.PHONY: setup dev test lint eval doctor

setup:
	python -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip
	. .venv/bin/activate && python -m pip install -r requirements.txt
	. .venv/bin/activate && python -m pip install -e .

dev:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose up -d; \
	elif command -v podman-compose >/dev/null 2>&1; then \
		podman-compose up -d; \
	else \
		echo "Neither docker compose nor podman-compose found"; \
		exit 1; \
	fi

test:
	pytest -q

eval:
	python -m cli.main eval retrieval

doctor:
	python -m cli.main doctor