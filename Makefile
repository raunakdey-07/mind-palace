.PHONY: setup dev test lint eval doctor

setup:
	python -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip
	. .venv/bin/activate && python -m pip install -r requirements.txt
	. .venv/bin/activate && python -m pip install -e .

dev:
	docker compose up -d

test:
	pytest -q

eval:
	python -m cli.main eval retrieval

doctor:
	python -m cli.main doctor