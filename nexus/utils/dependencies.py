"""Dependency Guard - Handles just-in-time installation of missing libraries."""

import subprocess
import sys
import importlib

def ensure_dependency(package_name: str, import_name: str | None = None) -> bool:
    """Checks if a package is installed, offers to install if missing."""
    import_name = import_name or package_name
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        cyan = "\033[36m"
        blue = "\033[34m"
        bold = "\033[1m"
        reset = "\033[0m"
        dim = "\033[90m"
        
        print(f"\n  {blue}╼{reset} {cyan}nexus/system{reset} {bold}module '{package_name}' missing{reset}")
        choice = input(f"    {bold}Initialize extension?{reset} (y/N): ").strip().lower()
        
        if choice in ('y', 'yes'):
            print(f"    {dim}Installing {package_name}...{reset}")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
                print(f"    {blue}✔{reset} {package_name} initialized. Restarting module...")
                return True
            except Exception as e:
                print(f"    {blue}✘{reset} Installation failed: {e}")
                return False
        return False
