"""Nexus CLI entry point."""

import sys
from pathlib import Path

# Add nexus to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.cli import main

if __name__ == "__main__":
    main()
