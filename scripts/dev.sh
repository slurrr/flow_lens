#!/usr/bin/env bash
source .venv/bin/activate
set -e
ruff check .
pyright
pytest
