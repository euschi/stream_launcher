"""`python -m stream_launcher` e punto d'ingresso dell'eseguibile Windows (PyInstaller)."""
import sys

from stream_launcher.cli import main

sys.exit(main())
