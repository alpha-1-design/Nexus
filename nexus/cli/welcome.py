"""Nexus Boot Sequence — Professional animated startup."""

import os
import shutil
import sys
import time


def clear():
    os.system("clear" if os.name == "posix" else "cls")


def _get_terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _color_text(text: str, code: int, bold: bool = False) -> str:
    b = "1;" if bold else ""
    return f"\033[{b}{code}m{text}\033[0m"


def fade_print(text: str, delay: float = 0.005, end: str = "\n"):
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write(end)
    sys.stdout.flush()


def _draw_progress_bar(current: int, total: int, width: int = 16) -> str:
    filled = int(current / total * width) if total > 0 else 0
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    pct = int(current / total * 100) if total > 0 else 0
    color = "32" if pct == 100 else "33" if pct > 50 else "36"
    return f"[{bar}] {_color_text(f'{pct:2d}%', color, bold=True)}"


def get_logo():
    """Return the Nexus ASCII logo in cyan."""
    c = "\033[36m"
    r = "\033[0m"
    return f"""{c}
   {r}   ███╗   ██╗{c}███████╗{r}██╗  ██╗{c}██╗   ██╗{r}███████╗{c}{r}
   {r}   ████╗  ██║{c}██╔════╝{r}╚██╗██╔╝{c}██║   ██║{r}██╔════╝{c}{r}
   {r}   ██╔██╗ ██║{c}█████╗  {r} ╚███╔╝ {c}██║   ██║{r}███████╗{c}{r}
   {r}   ██║╚██╗██║{c}██╔══╝  {r} ██╔██╗ {c}██║   ██║{r}╚════██║{c}{r}
   {r}   ██║ ╚████║{c}███████╗{r}██╔╝ ██╗{c}╚██████╔╝{r}███████║{c}{r}
   {r}   ╚═╝  ╚═══╝{c}╚══════╝{r}╚═╝  ╚═╝{c} ╚═════╝ {r}╚══════╝{c}{r}
{c}                      ∞  N E U R A L   O S  ∞{r}"""


def _render_subsystem(name: str, label: str, duration: float = 0.6):
    """Animate a single subsystem check."""
    width = 20
    steps = 10
    base = f"    {_color_text('[', 90)}{_color_text(name, 36, True)}{_color_text(']', 90)}  {label:<30}"
    for i in range(steps + 1):
        bar = _draw_progress_bar(i, steps, width)
        sys.stdout.write(f"\r{base} {bar}")
        sys.stdout.flush()
        time.sleep(duration / steps)
    status = _color_text("OK", 32, True)
    sys.stdout.write(f"  {status}\n")
    sys.stdout.flush()


def display_welcome():
    """Display the full animated boot sequence."""
    clear()

    logo = get_logo()
    print(logo)
    print()

    w = _get_terminal_width()
    dash = _color_text("\u2500" * min(w - 4, 60), 90)
    print(f"    {dash}")
    print()

    version_line = _color_text("N E X U S   N E U R A L   O S   v2", 36, True)
    build_line = _color_text("[build 2026.06.10]", 90)
    print(f"    {version_line}  {build_line}")

    license_line = _color_text("Apache 2.0  |  https://github.com/alpha-1-design/Nexus", 90)
    print(f"    {license_line}")
    print()

    subsystems = [
        ("CORE", "Synaptic weight initialization"),
        ("MESH", "Vector-mesh topology discovery"),
        ("EXEC", "Tool registry verification"),
        ("COMM", "Provider interface handshake"),
        ("MEM ", "Memory graph hydration"),
        ("SYST", "Environment resilience check"),
        ("BEAT", "Heartbeat service startup"),
    ]

    for name, label in subsystems:
        _render_subsystem(name, label, duration=0.4)

    print()
    line = _color_text("\u2500" * min(w - 4, 60), 90)
    print(f"    {line}")
    print()

    welcome = _color_text("Welcome to Nexus. Neural link established.", 36, True)
    directive = _color_text("Awaiting directive.", 90)
    print(f"    {welcome}")
    print(f"    {directive}")

    tip = _color_text("Tip: Type /help to see all available commands.", 90)
    print(f"\n    {tip}\n")


if __name__ == "__main__":
    display_welcome()
