"""
toolcare.__main__ - Entry point when executed as a module: python -m toolcare.
"""

from __future__ import annotations

import sys
from toolcare.cli import main

if __name__ == "__main__":
    sys.exit(main())
