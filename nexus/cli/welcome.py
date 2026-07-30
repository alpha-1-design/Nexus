"""Nexus Boot Sequence — Professional animated startup."""

import os
import shutil
import sys
import time
from pathlib import Path

_BOOT_MARKER = Path.home() / ".nexus" / ".boot_seen"


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


def _spinner(label: str, duration: float = 0.35):
    """A single compact spinner line -- used for the fast/repeat boot path."""
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    steps = max(4, int(duration / 0.05))
    for i in range(steps):
        frame = frames[i % len(frames)]
        sys.stdout.write(f"\r  {_color_text(frame, 36, True)} {label}")
        sys.stdout.flush()
        time.sleep(duration / steps)
    sys.stdout.write(f"\r  {_color_text('✔', 32, True)} {label}\n")
    sys.stdout.flush()


def _is_interactive_tty() -> bool:
    """Whether it's safe/sensible to run an animated terminal sequence.

    Skips animation under: piped/redirected stdout, `NO_COLOR`, `CI`, or
    explicit `NEXUS_BOOT=off`. Running a multi-second ANSI animation into a
    non-TTY (a script, a log file, a CI runner) previously wasted several
    real seconds on every invocation and dumped raw escape codes into the
    output.
    """
    if os.environ.get("NEXUS_BOOT", "").lower() == "off":
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("CI"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _print_minimal_banner():
    """Instant, non-animated banner for non-interactive contexts."""
    print("Nexus Neural OS -- awaiting directive. Type /help for commands.")


def _has_seen_boot() -> bool:
    try:
        return _BOOT_MARKER.exists()
    except Exception:
        return False


def _mark_boot_seen():
    try:
        _BOOT_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _BOOT_MARKER.touch()
    except Exception:
        pass


def display_welcome(force_full: bool = False):
    """Display the Nexus boot sequence.

    The full cinematic sequence (ASCII logo + per-subsystem progress bars)
    plays once, the first time Nexus is ever started on a machine. Every
    launch after that uses a fast ~0.5s condensed version instead, so the
    animation stays a delightful first impression without becoming a
    multi-second tax on every single command. Set `NEXUS_BOOT=full` to
    always play the full sequence, or `NEXUS_BOOT=off` to disable
    animation entirely (also auto-disabled for non-TTY output).
    """
    boot_mode = os.environ.get("NEXUS_BOOT", "").lower()

    if not _is_interactive_tty():
        _print_minimal_banner()
        return

    show_full = force_full or boot_mode == "full" or not _has_seen_boot()

    if show_full:
        _display_full_boot()
        _mark_boot_seen()
    else:
        _display_fast_boot()


def _display_full_boot():
    """The full first-run cinematic boot sequence (~1.3s)."""
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

    # Condensed from the original 0.4s/step (~2.8s total) to keep the
    # first-run "wow" moment snappy rather than a multi-second wait.
    for name, label in subsystems:
        _render_subsystem(name, label, duration=0.16)

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


def _display_fast_boot():
    """Condensed boot sequence for returning users (~0.5s total).

    Keeps the same visual language (logo, spinner, welcome line) as the
    full sequence but skips the seven sequential progress bars, since the
    user has already seen the full show once.
    """
    clear()
    print(get_logo())
    print()

    _spinner("Reconnecting neural link...", duration=0.45)

    w = _get_terminal_width()
    line = _color_text("\u2500" * min(w - 4, 60), 90)
    print(f"\n    {line}\n")

    welcome = _color_text("Welcome back to Nexus.", 36, True)
    print(f"    {welcome}")
    tip = _color_text("Tip: Type /help to see all available commands.", 90)
    print(f"    {tip}\n")


if __name__ == "__main__":
    display_welcome()
