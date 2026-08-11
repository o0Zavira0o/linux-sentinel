#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

cd "$ROOT_DIR"

echo
echo "======================================"
echo " Sentinel-X Engineering Quality Gate"
echo "======================================"
echo

echo "[1/5] Python syntax validation"
python -m compileall -q \
    src/sentinel_x \
    tests

echo "PASS: syntax"
echo

echo "[2/5] Ruff formatting"
ruff format --check \
    src/sentinel_x \
    tests

echo "PASS: formatting"
echo

echo "[3/5] Ruff linting"
ruff check \
    src/sentinel_x \
    tests

echo "PASS: Ruff"
echo

echo "[4/5] mypy strict type checking"
python -m mypy \
    --package sentinel_x

echo "PASS: mypy"
echo

echo "[5/5] Unit tests"
python -m unittest discover \
    -s tests/unit \
    -v

echo
echo "======================================"
echo " ALL SENTINEL-X QUALITY CHECKS PASSED"
echo "======================================"
