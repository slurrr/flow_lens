from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    pyright_js = root_dir / ".venv/lib/python3.11/site-packages/pyright/dist/index.js"

    if not pyright_js.is_file():
        print(f"pyright CLI not found at {pyright_js}", file=sys.stderr)
        print("Ensure .venv is created and pyright is installed.", file=sys.stderr)
        return 1

    result = subprocess.run(["node", str(pyright_js), *sys.argv[1:]], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
