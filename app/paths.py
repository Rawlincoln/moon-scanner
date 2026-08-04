"""Project root path (repo root, not app package)."""

from pathlib import Path

from config import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
# Durable SQLite root (DATA_DIR env / disk mount, else ./data)
DATA_DIR = DATA_DIR
