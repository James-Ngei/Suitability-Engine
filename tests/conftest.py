"""
Shared pytest configuration.

Ensures a headless Matplotlib backend (the API imports matplotlib.pyplot at
module load) and puts `src/` on the import path so the pipeline modules —
which import each other as top-level modules (e.g. `from config import ...`) —
resolve the same way they do when run as scripts.
"""

import os
import sys
from pathlib import Path

# Matplotlib must not try to open a GUI backend in CI / headless runs.
os.environ.setdefault("MPLBACKEND", "Agg")

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
