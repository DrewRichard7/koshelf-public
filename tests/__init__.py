"""KoShelf's tests. The modules under test live in src/, so put it on the import path."""
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / 'src'
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
