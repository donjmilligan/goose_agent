# toycalc architecture

- One module, `src/toycalc/calc.py`, holds every function.
- `src/toycalc/__init__.py` re-exports the public functions.
- Tests live in `tests/` and use pytest. The gate is `uv run pytest -q`.
