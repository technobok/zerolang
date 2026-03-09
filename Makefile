.PHONY: check test fmt

check:
	uv run ruff format src/ tests/
	uv run ruff check src/ tests/ --fix
	uv run ty check src/

test:
	uv run pytest tests/ -v

fmt:
	uv run ruff format src/ tests/
