.PHONY: install format lint test check run data docker-build

install:
	python -m pip install --requirement requirements-dev.txt

format:
	python -m ruff format .
	python -m ruff check --fix .

lint:
	python -m ruff format --check .
	python -m ruff check .
	python -m bandit --recursive src --skip B104,B110
	python -m mypy

test:
	python -m pytest

check: lint test

run:
	python -m wiz_hatch.bot

data:
	python scripts/build_w101_pets.py

docker-build:
	docker build --tag wiz-hatch:0.2.0 .

