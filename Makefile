.PHONY: venv install dev test lint clean

venv:
	python3 -m venv .venv

install:
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e .

dev:
	.venv/bin/python -m pip install -e '.[dev]'

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check src tests

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache
