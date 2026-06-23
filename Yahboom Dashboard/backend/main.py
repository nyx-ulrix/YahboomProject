"""
Yahboom Dashboard Backend - Main Entry Point
"""

import sys
from pathlib import Path


def _venv_python() -> Path | None:
    root = Path(__file__).resolve().parent
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def _ensure_dependencies() -> None:
    try:
        import flask  # noqa: F401
        return
    except ImportError:
        pass

    venv_py = _venv_python()
    in_venv = sys.prefix != sys.base_prefix

    if not in_venv and venv_py is not None:
        print(
            "Python dependencies are installed in backend/.venv, but you are "
            f"using system Python ({sys.executable}).\n"
            f"Run instead:\n  {venv_py} main.py\n"
            "Or from the repo root:\n  npm run dev:backend",
            file=sys.stderr,
        )
        sys.exit(1)

    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    if not req.is_file():
        print(f"Missing {req}", file=sys.stderr)
        sys.exit(1)

    import subprocess

    print("Installing missing Python packages from requirements.txt …")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
    )


_ensure_dependencies()

from app import create_app
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG

if __name__ == "__main__":
    app = create_app()
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
