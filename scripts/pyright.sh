#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYRIGHT_JS="$ROOT_DIR/.venv/lib/python3.11/site-packages/pyright/dist/index.js"

if [[ ! -f "$PYRIGHT_JS" ]]; then
  echo "pyright CLI not found at $PYRIGHT_JS" >&2
  echo "Ensure .venv is created and pyright is installed." >&2
  exit 1
fi

node "$PYRIGHT_JS" "$@"
