"""Professional loading states and progress indicators for REPL."""

import sys
import threading
import time


class Spinner:
    """Thread-safe animated spinner for terminal."""

    FRAMES = ["\u25D0", "\u25D3", "\u25D1", "\u25D2"]
    DOTS = ["   ", ".  ", ".. ", "...", " ..", "  .", "   "]

    def __init__(self, message: str = "Working"):
        self.message = message
        self._running = False
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, final: str = "Done"):
        self._running = False
        self._done.set()
        if self._thread:
            self._thread.join(timeout=1)
        sys.stdout.write(f"\r\033[K\033[32m\u2713\033[0m {final}\n")
        sys.stdout.flush()

    def _spin(self):
        i = 0
        while self._running:
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r\033[36m{frame}\033[0m {self.message}...")
            sys.stdout.flush()
            time.sleep(0.12)
            i += 1


class LoadingIndicator:
    """Legacy wrapper — delegates to Spinner."""

    def __init__(self, message: str = "Working"):
        self._spinner = Spinner(message)

    def start(self):
        self._spinner.start()

    def stop(self, final: str = "Done"):
        self._spinner.stop(final)


def with_loading(func):
    """Decorator to wrap a function with loading indicator."""

    def wrapper(*args, **kwargs):
        indicator = LoadingIndicator(f"Running {func.__name__}")
        indicator.start()
        try:
            result = func(*args, **kwargs)
            indicator.stop()
            return result
        except Exception as e:
            indicator.stop(f"Error: {e}")
            raise

    return wrapper


class ProgressTracker:
    """Track multi-step progress with professional bar."""

    def __init__(self, total: int, description: str = ""):
        self.total = total
        self.current = 0
        self.description = description
        self.start_time = time.time()

    def step(self, label: str = "") -> None:
        self.current += 1
        pct = self.current / self.total if self.total > 0 else 0
        filled = "\u2588" * int(pct * 20)
        empty = "\u2591" * (20 - int(pct * 20))
        elapsed = time.time() - self.start_time
        label_str = f" \033[90m\u2014 {label}\033[0m" if label else ""
        color = "32" if pct >= 1 else "33" if pct > 0.5 else "36"
        sys.stdout.write(
            f"\r\033[{color}m[{filled}{empty}]\033[0m {pct:.0%}"
            f"{label_str} \033[90m({elapsed:.1f}s)\033[0m"
        )
        sys.stdout.flush()

    def finish(self) -> None:
        elapsed = time.time() - self.start_time
        sys.stdout.write(f"\r\033[K\033[32m\u2713\033[0m Done in {elapsed:.1f}s\n")
        sys.stdout.flush()


def render_status_line(parts: list[tuple[str, str | None]]) -> str:
    """Render a status line with colored segments.

    Each tuple is (text, color_code_or_none).
    """
    segments = []
    for text, color in parts:
        if color:
            segments.append(f"\033[{color}m{text}\033[0m")
        else:
            segments.append(text)
    return " ".join(segments)


def prompt_line(prefix: str = "nexus", provider: str | None = None, status: str = "ready") -> str:
    """Generate a professional REPL prompt string."""
    c = "\033[36m"
    g = "\033[32m"
    y = "\033[33m"
    d = "\033[90m"
    r = "\033[0m"

    status_map = {
        "ready": f"{g}\u25CF{r}",
        "busy": f"{y}\u25CF{r}",
        "error": "\033[31m\u25CF\033[0m",
    }
    indicator = status_map.get(status, status_map["ready"])

    if provider:
        return f"\n{d}\u2502{r} {c}{prefix}{r} {d}@{r} {y}{provider}{r} {indicator} {d}\u2771{r} "
    return f"\n{d}\u2502{r} {c}{prefix}{r} {indicator} {d}\u2771{r} "
