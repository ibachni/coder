# .PHONY tells Make that these are not filenames.
.PHONY: check lint format typecheck test install

# dependencies let targets call other targets
check: lint format typecheck test

# Target: dependencies
# make lint runs ruff check src/
lint:
	uv run ruff check src/

format:
	uv run ruff format src/

typecheck:
	uv run pyright src/

test:
	uv run pytest; code=$$?; [ $$code -eq 5 ] && exit 0 || exit $$code

install:
	uv sync
	uv run pre-commit install