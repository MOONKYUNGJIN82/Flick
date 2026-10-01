"""Build-channel checks; only a bundled App Store app carries the marker."""
from pathlib import Path
import sys


def is_app_store_build() -> bool:
    root = getattr(sys, '_MEIPASS', None)
    return root is not None and (Path(root) / 'flick-app-store.txt').is_file()
