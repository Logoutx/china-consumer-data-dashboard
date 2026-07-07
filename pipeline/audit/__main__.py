"""`python -m pipeline.audit` entry point."""
from __future__ import annotations

import sys

from pipeline.audit.cli import main

if __name__ == "__main__":
    sys.exit(main())
