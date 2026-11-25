"""Stream Launcher: servizi di streaming su runtime Firefox portatile."""
from pathlib import Path as _Path


def _version() -> str:
    """Versione dal tag git: la scrive la build (_version.py), dal repo la chiede a git."""
    try:
        from ._version import VERSION

        return VERSION
    except ImportError:
        pass
    import subprocess

    try:
        out = subprocess.run(["git", "-C", str(_Path(__file__).resolve().parent.parent), "describe", "--tags"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out.removeprefix("v") or "sviluppo"


__version__ = _version()
