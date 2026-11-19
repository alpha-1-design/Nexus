"""Skill pre-loading matcher — fast, offline, zero-extra-dependency keyword
matching over the bundled skill catalog.

The owner's ask was: "Does Nexus load a skill before working? It should."
This module is the piece that answers that: before the first LLM call of
each user turn, the engine (orchestrator + REPL) asks this matcher which
bundled skills are relevant to the task text and injects a compact
``### RELEVANT SKILLS`` section into the system prompt, plus a visible
"skills loaded" hint in the TUI/REPL.

Design constraints:
- Zero deps beyond PyYAML (already a core dependency). No vectors, no
  network: token/keyword overlap against skill names + descriptions +
  tags + categories from the local catalog.
- The ~400-skill catalog indexes cold in well under 50ms: frontmatter is
  parsed with libyaml's ``CSafeLoader`` when available (~25ms for 394
  files vs ~250ms with the pure-Python SafeLoader the generic loader
  uses), with an automatic fallback when libyaml is missing.
- The index is cached in memory for the life of the process (see
  :func:`get_skill_matcher`), so warm matches are microsecond work.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import SkillLoader

# --- scoring model -----------------------------------------------------------
# A task token contributes, per skill, the *highest* field weight it hits
# (name > tag > category > description). Scores are the sum over distinct
# task tokens, so:
#   one exact skill-name hit            -> 4.0  (fires)
#   name hit + tag/description hit      -> 5.0+ (fires, strongly ranked)
#   four separate description-word hits -> 4.0  (fires)
#   one description word alone          -> 1.0  (never fires; noise guard)
WEIGHT_NAME = 4.0
WEIGHT_TAG = 2.0
WEIGHT_CATEGORY = 1.5
WEIGHT_DESCRIPTION = 1.0

DEFAULT_TOP_N = 3
DEFAULT_MIN_SCORE = 4.0
# Cap for the injected prompt section (mission spec: ~1200 chars).
MAX_PROMPT_CHARS = 1200

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words and chat filler that carry no topical signal. Deliberately
# small and conservative: domain words (test, code, build, file, git, api…)
# must stay scoreable.
STOPWORDS = frozenset(
    """
    a about after again all also an and any are as at be been being but by
    can could did do does doing for from get got had has have how i if in
    into is it its me my of on or our out over so some such than that the
    their them then there these they this those to too under until up us
    very was we were what when where which while who why will with would
    you your yours am please thanks thank hello hi hey just actually
    basically really okay ok
    """.split()
)


def _tokenize(text: str) -> list[str]:
    """Lowercase alnum tokens, minus stopwords and 1-char noise."""
    return [
        t
        for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= 2 and t not in STOPWORDS
    ]


def _stem(token: str) -> str:
    """Light normalization so plural forms match: databases -> database.

    Applied identically to the index side and the task side, so consistency
    is what matters ("postgres" becomes "postgre" on both sides and still
    matches). Only trailing 's' on longer tokens — no real stemming library.
    """
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _yaml_loader():
    """libyaml's safe loader when compiled in (10x faster), else SafeLoader.

    Both are "safe" loaders with identical semantics for frontmatter use;
    only speed differs. Matched on attribute presence, not version sniffing.
    """
    return getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# --- fast frontmatter subset parser ------------------------------------------
# Skill frontmatter in the wild (and across the whole bundled catalog) uses a
# tiny YAML subset: `key: value` scalars and `key:` + `- item` lists. Parsing
# just that subset is ~5x faster than yaml.CSafeLoader across 394 files, which
# is what gets the cold index build comfortably under the 50ms budget.
# Anything outside the subset (multi-line quoted values, inline lists, anchors)
# makes the parser return None and the caller falls back to real YAML for that
# file — correctness is never traded for speed.

_FM_KEY_RE = re.compile(r"^([A-Za-z][\w-]*)\s*:(.*)$")
_FM_LIST_RE = re.compile(r"^-\s*(.*)$")
_FM_SPECIAL_RE = re.compile(r"[{}[\]&*!|>%@`]")
# Keys the matcher actually reads; lets the parser stop early once all are in.
_FM_NEEDED = frozenset({"name", "description", "category", "tags"})


def _fm_scalar(val: str) -> str | None:
    """Parse one scalar value; None = "not in the subset, use YAML"."""
    if not val:
        return ""
    quote = val[0]
    if quote in ("'", '"'):
        if len(val) < 2 or val[-1] != quote:
            return None  # multi-line quoted value
        body = val[1:-1]
        if quote == "'":
            return body.replace("''", "'")
        if "\\" in body:
            import json

            try:
                unescaped = json.loads(val)
                return unescaped if isinstance(unescaped, str) else None
            except Exception:
                return None
        return body
    if " #" in val:  # trailing YAML comment
        val = val.split(" #", 1)[0].rstrip()
    if _FM_SPECIAL_RE.search(val):
        return None  # flow lists, anchors, aliases, block scalars…
    return val


def _parse_frontmatter_subset(raw: str) -> dict | None:
    """Parse the simple frontmatter subset, or None if it's real YAML."""
    data: dict = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        item = _FM_LIST_RE.match(stripped)
        if item and current_list_key:
            value = _fm_scalar(item.group(1))
            if value is None:
                return None
            data[current_list_key].append(value)
            continue
        key_match = _FM_KEY_RE.match(stripped)
        if not key_match:
            return None  # continuation line (multi-line value) etc.
        key, val = key_match.group(1), key_match.group(2).strip()
        if val:
            current_list_key = None
            value = _fm_scalar(val)
            if value is None:
                return None
            data[key] = value
            # Early exit: every field the matcher reads is already parsed.
            if _FM_NEEDED <= data.keys() and current_list_key is None:
                break
        else:
            data[key] = []
            current_list_key = key
    return data


@dataclass
class MatchedSkill:
    """One skill the matcher considers relevant, and why."""

    name: str
    description: str
    score: float
    matched_tokens: list[str] = field(default_factory=list)
    category: str = ""
    source_file: str = ""


class SkillMatcher:
    """Keyword-overlap matcher over the bundled skill catalog.

    Usage:
        matcher = get_skill_matcher()          # process-wide, lazy index
        matches = matcher.match("debug a slow postgres query")

    The first :meth:`match` (or :meth:`build_index`) call parses the local
    catalog once; every later call is pure dictionary work.
    """

    def __init__(
        self,
        loader: SkillLoader | None = None,
        top_n: int = DEFAULT_TOP_N,
        min_score: float = DEFAULT_MIN_SCORE,
    ):
        self.loader = loader or SkillLoader()
        self.top_n = top_n
        self.min_score = min_score
        # Index state (parallel lists + token -> {skill_idx: weight} postings)
        self._names: list[str] = []
        self._descriptions: list[str] = []
        self._categories: list[str] = []
        self._sources: list[str] = []
        self._postings: dict[str, dict[int, float]] = {}
        # Cold-build duration in ms, exposed for tests/diagnostics.
        self.index_ms: float = 0.0

    # ── index ────────────────────────────────────────────────────────────

    @property
    def is_indexed(self) -> bool:
        return bool(self._names)

    @property
    def skill_count(self) -> int:
        return len(self._names)

    def categories(self) -> list[str]:
        """Sorted category names across the indexed catalog."""
        return sorted({c for c in self._categories if c})

    def _candidate_dirs(self) -> list[Path]:
        """Mirror SkillLoader's directory resolution (explicit dir wins,
        otherwise every known location that exists)."""
        explicit = getattr(self.loader, "_explicit_dir", None)
        if explicit:
            return [explicit]
        return [
            d
            for d in getattr(self.loader, "SKILL_DIRS", [])
            if d.exists() and d.is_dir()
        ]

    def _parse_frontmatter(self, path: Path, loader_cls) -> tuple | None:
        """Extract (name, description, category, tags, source) from one .md.

        Mirrors SkillLoader._parse_skill_file for the fields the matcher
        needs, minus the skill body (which the matcher never reads). Uses
        the fast subset parser first and falls back to real YAML per file
        whenever the frontmatter uses anything outside the subset.
        """
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None
        match = SkillLoader.FRONTMATTER_RE.match(content)
        if not match:
            fm: dict = {}
        else:
            raw = match.group(1)
            fm = _parse_frontmatter_subset(raw)
            if fm is None:
                try:
                    fm = yaml.load(raw, Loader=loader_cls) or {}
                except yaml.YAMLError:
                    fm = {}
        tags = fm.get("tags") or []
        try:
            tag_list = [str(t) for t in tags]
        except Exception:
            tag_list = []
        return (
            str(fm.get("name", path.stem) or path.stem),
            str(fm.get("description", "") or ""),
            str(fm.get("category", "general") or ""),
            tag_list,
            str(path),
        )

    def build_index(self) -> int:
        """Parse the catalog and build token postings. Returns skill count.

        Same-name collision rule as SkillLoader.discover(): later
        directories override earlier ones, so user skills always win.

        GC is paused for the loop: the build allocates hundreds of thousands
        of acyclic dicts/lists/strings, and a generation-0 collection landing
        mid-loop was measured to add ~25ms spikes on modest hardware (the
        whole build is otherwise ~25ms warm). Objects created here form no
        reference cycles, so deferring collection is safe.
        """
        import gc

        started = time.perf_counter()
        loader_cls = _yaml_loader()
        by_name: dict[str, tuple] = {}
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        try:
            for skills_dir in self._candidate_dirs():
                for path in skills_dir.rglob("*.md"):
                    parsed = self._parse_frontmatter(path, loader_cls)
                    if parsed:
                        by_name[parsed[0]] = parsed

            self._names = []
            self._descriptions = []
            self._categories = []
            self._sources = []
            self._postings = {}
            for name, desc, category, tags, source in by_name.values():
                idx = len(self._names)
                self._names.append(name)
                self._descriptions.append(desc)
                self._categories.append(category)
                self._sources.append(source)
                self._index_skill(idx, name, desc, category, tags)
        finally:
            if gc_was_enabled:
                gc.enable()

        self.index_ms = (time.perf_counter() - started) * 1000
        return len(self._names)

    def _index_skill(self, idx: int, name: str, desc: str, category: str, tags: list[str]) -> None:
        fields = (
            (_tokenize(name), WEIGHT_NAME),
            (_tokenize(" ".join(tags)), WEIGHT_TAG),
            (_tokenize(category), WEIGHT_CATEGORY),
            (_tokenize(desc), WEIGHT_DESCRIPTION),
        )
        for tokens, weight in fields:
            for token in {_stem(t) for t in tokens}:
                posting = self._postings.setdefault(token, {})
                if posting.get(idx, 0.0) < weight:
                    posting[idx] = weight

    def ensure_index(self) -> None:
        if not self.is_indexed:
            self.build_index()

    # ── matching ─────────────────────────────────────────────────────────

    def match(
        self,
        task_text: str,
        top_n: int | None = None,
        min_score: float | None = None,
    ) -> list[MatchedSkill]:
        """Return the top-N bundled skills relevant to ``task_text``.

        Empty list when nothing scores above ``min_score`` (callers must
        treat that as "inject nothing" — no noise on unrelated tasks).
        """
        self.ensure_index()
        top_n = self.top_n if top_n is None else top_n
        min_score = self.min_score if min_score is None else min_score

        # Unique task tokens by stem (so "test" and "tests" can't both
        # score), keeping the first original form for display.
        task_tokens: dict[str, str] = {}
        for token in _tokenize(task_text):
            task_tokens.setdefault(_stem(token), token)

        scores: dict[int, tuple[float, list[str]]] = {}
        for stem, original in task_tokens.items():
            posting = self._postings.get(stem)
            if not posting:
                continue
            for idx, weight in posting.items():
                score, hits = scores.get(idx, (0.0, []))
                scores[idx] = (score + weight, hits + [original])

        results = [
            MatchedSkill(
                name=self._names[idx],
                description=self._descriptions[idx],
                score=round(score, 2),
                matched_tokens=hits,
                category=self._categories[idx],
                source_file=self._sources[idx],
            )
            for idx, (score, hits) in scores.items()
            if score >= min_score
        ]
        results.sort(key=lambda m: (-m.score, m.name))
        return results[: max(0, top_n)]


def format_relevant_skills(
    matches: list[MatchedSkill],
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    """Format matches as the compact ``### RELEVANT SKILLS`` prompt section.

    Returns "" for no matches so callers can skip injection entirely.
    """
    if not matches:
        return ""
    lines = [
        "### RELEVANT SKILLS",
        (
            "The bundled skill library has expertise matching this task. "
            "Follow the relevant guidance below; the user can load any skill "
            "on request with /skill <name>."
        ),
    ]
    for m in matches:
        desc = " ".join(m.description.split())
        if len(desc) > 90:
            desc = desc[:89].rstrip() + "…"
        lines.append(f"- {m.name} — {desc}")
    section = "\n".join(lines)
    if len(section) > max_chars:
        section = section[: max_chars - 1].rstrip() + "…"
    return section


# ── process-wide matcher ─────────────────────────────────────────────────

_shared_matcher: SkillMatcher | None = None


def get_skill_matcher() -> SkillMatcher:
    """Return the shared SkillMatcher (index built lazily, kept in memory)."""
    global _shared_matcher
    if _shared_matcher is None:
        _shared_matcher = SkillMatcher()
    return _shared_matcher
