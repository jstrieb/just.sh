REQUIRED_COVERAGE := "100"

# Run all tests, and check coverage
test cores="auto":
    python3 -m pytest \
      -n {{ cores }} \
      --failed-first \
      --color yes \
      --cov-config .coveragerc \
      --cov-report html \
      --cov . \
      test/test.py
    python3 -m coverage report \
      --show-missing \
      --fail-under {{ REQUIRED_COVERAGE }}

# Test until the first failure
first-fail:
    python3 -m pytest -v --failed-first -x test/test.py

alias lint := lint-python

lint-python:
    black .
    mypy test/ just_sh/
    ruff check --fix .

# Fail if linting is required
check:
    black --check --diff --color .
    mypy test/ just_sh/
    ruff check .
