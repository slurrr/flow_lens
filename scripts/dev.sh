#!/usr/bin/env bash
set -e
ruff check .
pyright
pytest
