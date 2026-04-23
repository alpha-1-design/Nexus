"""Nexus CLI entry point."""

import sys
from pathlib import Path

<<<<<<< HEAD
# Add nexus to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.cli import main
=======

def ensure_package_path():
    """
    Ensure the nexus package is discoverable regardless of installation method.
    If running from source in a 'src' layout, we dynamically inject the src path.
    """
    # Get the absolute path to the current file
    current_file = Path(__file__).resolve()

    # If we are in src/nexus/__main__.py, the package root is the 'src' directory
    # current_file: /.../src/nexus/__main__.py
    # parent: /.../src/nexus/
    # parent.parent: /.../src/
    src_path = str(current_file.parent.parent)

    if src_path not in sys.path:
        sys.path.insert(0, src_path)


def main():
    """Main entry point for the Nexus CLI."""
    try:
        ensure_package_path()
        from nexus.cli import main as cli_main

        cli_main()
    except ImportError:
        print("\n\033[91m[CRITICAL ERROR] Package 'nexus' not found in Python path.\033[0m")
        print("\nThis usually happens when the project is moved to a 'src' layout")
        print("without being installed.")
        print("\n\033[1mFIX:\033[0m Run the following command in the project root:")
        print("\033[92mpip install -e .\033[0m\n")
        sys.exit(1)
    except Exception:
        print("\n\033[91m[FATAL] Unexpected system failure\033[0m")
        print("Please check your configuration and try again.")
        sys.exit(1)

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)

if __name__ == "__main__":
    main()
