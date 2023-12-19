REQUIRED_COVERAGE := "100"

test cores="auto": lint-python
    python3 -m pytest \
      -n {{cores}} \
      --failed-first \
      --cov-config .coveragerc \
      --cov-report html \
      --cov src \
      test/test.py
    python3 -m coverage report \
      --show-missing \
      --fail-under {{ REQUIRED_COVERAGE }}

first-fail:
    python3 -m pytest --failed-first -x test/test.py

alias lint := lint-python

lint-python:
    black .
    mypy --check-untyped-defs .
    ruff check --fix .

