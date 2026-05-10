# .PHONY tells Make that these are not filenames.
.PHONY: check lint format typecheck test install

# dependencies let targets call other targets
check: lint format typecheck test

# Target: dependencies
# make lint runs ruff check src/
lint:
	ruff check src/

format:
	ruff format src/

typecheck:
	pyright src/

test:
	pytest

install:
	pip install ruff pyright pytest pre-commit
	pre-commit install