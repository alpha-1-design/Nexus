"""Paste chips — collapse long pasted blocks into compact inline chips.

A terminal paste of a big block should never dump dozens of raw lines into
a single-line input. Instead the input holds a short chip token —
`[Pasted text #1 +42 lines]` — while the full text is kept in an
IN-MEMORY store (never files). Chips resolve back to the full text when
the message is sent to the agent, so the model always sees the whole
paste; the transcript keeps rendering the compact chip. ctrl+o toggles
chip expansion in the transcript.

This module is deliberately stdlib-only (no Textual import) so the REPL
can share the exact same chip format and store numbering.
"""

import re

# Chip token: `[Pasted text #12 +42 lines]`. Recognized wherever it appears
# (mixed with typed text, queued messages, transcript content).
CHIP_RE = re.compile(r"\[Pasted text #(\d+) \+(\d+) lines?\]")


def make_chip(number: int, lines: int) -> str:
    """Chip token for a stored paste. One canonical format everywhere."""
    return f"[Pasted text #{number} +{lines} lines]"


class PasteStore:
    """In-memory {number: full_text} store for collapsed pastes.

    Numbering is session-monotonic: it never resets or reuses a number,
    even after clear() — a transcript full of chips must stay unambiguous
    for the whole session.
    """

    def __init__(self):
        self._pastes: dict[int, str] = {}
        self._next_number = 1
        # ctrl+o transcript state: False → chips, True → dim full text.
        self.expanded = False

    def add(self, text: str) -> int:
        """Store a pasted block and return its chip number."""
        number = self._next_number
        self._next_number += 1
        self._pastes[number] = text
        return number

    def get(self, number: int) -> str | None:
        return self._pastes.get(number)

    def clear(self) -> None:
        """Drop stored texts; numbering stays monotonic."""
        self._pastes.clear()

    def __len__(self) -> int:
        return len(self._pastes)


_store: PasteStore | None = None


def get_paste_store() -> PasteStore:
    """Process-global store singleton (TUI and REPL share the format)."""
    global _store
    if _store is None:
        _store = PasteStore()
    return _store


def reset_paste_store() -> None:
    """Drop the singleton (tests)."""
    global _store
    _store = None


def should_collapse(text: str, threshold: int) -> bool:
    """True when a pasted block must collapse to a chip.

    Only MULTI-line blocks collapse: a single (long) line is a normal edit
    and stays raw. `threshold` is the paste_collapse_lines setting — a
    block collapses when its line count EXCEEDS it.
    """
    lines = text.splitlines()
    return len(lines) > max(1, threshold)


def collapse_paste(text: str, threshold: int, store: PasteStore | None = None) -> str | None:
    """Return the chip token for a block over the threshold, else None.

    The full text is stored in the paste store (in memory only).
    """
    if not should_collapse(text, threshold):
        return None
    store = store or get_paste_store()
    number = store.add(text)
    return make_chip(number, len(text.splitlines()))


def resolve_chips(text: str, store: PasteStore | None = None) -> str:
    """Replace every known chip token with its full stored text.

    This is the AGENT payload path: the model receives the whole paste.
    Unknown chip numbers (never pasted, or typed by hand) are left as-is.
    """
    store = store or get_paste_store()

    def _sub(match: re.Match) -> str:
        stored = store.get(int(match.group(1)))
        return stored if stored is not None else match.group(0)

    if not CHIP_RE.search(text):
        return text
    return CHIP_RE.sub(_sub, text)


def expand_segments(text: str, store: PasteStore | None = None) -> list[tuple[str, bool]]:
    """Split text into (segment, is_expanded_paste) runs for the transcript.

    Collapsed mode the caller renders `text` verbatim; expanded mode renders
    these runs, with pasted blocks dim-wrapped between blank-line separators.
    """
    store = store or get_paste_store()
    runs: list[tuple[str, bool]] = []
    pos = 0
    found = False
    for match in CHIP_RE.finditer(text):
        stored = store.get(int(match.group(1)))
        if stored is None:
            continue
        found = True
        if match.start() > pos:
            runs.append((text[pos:match.start()], False))
        runs.append((f"\n{stored}\n", True))
        pos = match.end()
    if not found:
        return [(text, False)]
    if pos < len(text):
        runs.append((text[pos:], False))
    return runs


def summarize_for_queue(text: str, limit: int = 60) -> str:
    """First ~`limit` chars of a queued message for the queue rows.

    Newlines flatten to spaces so one queued message is always exactly one
    dim row, even when it holds a multi-line paste chip mix.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"
