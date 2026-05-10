.PHONY: check lint format typecheck test install

check: lint format typecheck test

lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	pyright src/

test:
	pytest

install:
	pip install ruff pyright pytest
