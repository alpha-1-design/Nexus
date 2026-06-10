"""Shared validation utilities for Nexus.

Consolidates Refiner's Fire (integrity checks) and tool failure
recovery hints used by both the REPL and AgentOrchestrator.
"""

import ast
import json
import os


async def run_refiners_fire(path: str | None) -> tuple[bool, str | None]:
    """Verify file integrity after write/edit operations.

    Checks syntax for Python, validity for JSON/YAML.
    Returns (passed, error_message).
    """
    if not path or not os.path.exists(path):
        return True, None
    try:
        if path.endswith(".py"):
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read())
        elif path.endswith(".json"):
            with open(path, encoding="utf-8") as f:
                json.load(f)
        elif path.endswith((".yaml", ".yml")):
            import yaml
            with open(path, encoding="utf-8") as f:
                yaml.safe_load(f)
        return True, None
    except SyntaxError as e:
        return False, f"Syntax Error: {e.msg} (line {e.lineno})"
    except Exception as e:
        return False, str(e)


def get_tool_failure_hint(tool_name: str, args: dict, error: str) -> str:
    """Generate deterministic recovery hints for common tool failures."""
    hint = f"Error: {error}"

    if tool_name == "edit" and "context mismatch" in error.lower():
        path = args.get("path")
        old_string = args.get("old_string")
        if path and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            if old_string and old_string in content:
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if old_string in line:
                        context_block = "\n".join(
                            lines[max(0, i - 2): min(len(lines), i + 3)]
                        )
                        hint += (
                            f"\n\n[RECOVERY HINT] 'old_string' was found at "
                            f"line {i + 1}, but context mismatch occurred. "
                            f"Here is the actual context in the file:\n"
                            f"{context_block}"
                        )
                        break
            else:
                hint += (
                    "\n\n[RECOVERY HINT] 'old_string' was NOT found in "
                    "the file at all. Please use the 'read' tool to verify "
                    "the current file content."
                )

    elif tool_name == "bash" and "not found" in error.lower():
        hint += (
            "\n\n[RECOVERY HINT] The command was not found. If this is a "
            "new tool, you might need to install it via 'apt install' or "
            "'pip install'. In Termux, try 'pkg install'."
        )

    elif "file not found" in error.lower() or "no such file" in error.lower():
        hint += (
            "\n\n[RECOVERY HINT] Verify the path exists using 'list' or "
            "'glob'. Paths should usually be relative to the project root."
        )

    return hint
