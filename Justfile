REQUIRED_COVERAGE := "100"

test: lint-python
    python3 -m pytest \
      -n auto \
      --cov-config .coveragerc \
      --cov-report html \
      --cov . \
      test.py
    python3 -m coverage report \
      --show-missing \
      --fail-under {{ REQUIRED_COVERAGE }}

lint-python:
    black .
    mypy .
