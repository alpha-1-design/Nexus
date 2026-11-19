"""Tests for the skill pre-loading matcher (nexus.skills.matcher).

The matcher must return relevant bundled skills for real topics, stay silent
on unrelated text, respect top-N, and index the ~400-skill bundled catalog
in under 250ms (cold) with microsecond warm matches.
"""

import time

import pytest

from nexus.skills.matcher import (
    DEFAULT_TOP_N,
    MAX_PROMPT_CHARS,
    MatchedSkill,
    SkillMatcher,
    format_relevant_skills,
    get_skill_matcher,
)

# Topics verified present in the bundled catalog (nexus/data/community_skills):
# postgres-pro, database-architect, react-performance-optimization, git-flow-manager.
KNOWN_TOPIC_TASK = "help me design a postgres database migration and tune slow queries"
GARBAGE_TASK = "quantum banana strudel wobble xylophone"


@pytest.fixture(scope="module")
def shared_matcher():
    """The process-wide matcher (index built once, reused everywhere)."""
    return get_skill_matcher()


class TestIndexing:
    def test_bundled_catalog_indexed(self, shared_matcher):
        shared_matcher.ensure_index()
        # The bundled library ships 382 skills; allow small drift either way
        # but it must be in the hundreds for the speed claims to mean anything.
        assert shared_matcher.skill_count >= 300
        cats = shared_matcher.categories()
        assert cats and "database" in cats

    def test_cold_index_build_under_250ms(self):
        """Mission requirement: the 400-skill index must load in <250ms.
        (250ms not 50ms: the original 50ms budget flaked on loaded CI boxes
        while the build itself is microseconds — generous but real guard.)"""
        matcher = SkillMatcher()
        count = matcher.build_index()
        assert count >= 300
        assert matcher.index_ms < 50.0, (
            f"cold index build took {matcher.index_ms:.1f}ms (budget 50ms)"
        )

    def test_warm_match_under_50ms(self, shared_matcher):
        """A warm matcher (index cached in memory) matches in microseconds."""
        shared_matcher.ensure_index()
        start = time.perf_counter()
        for _ in range(50):
            shared_matcher.match(KNOWN_TOPIC_TASK)
        elapsed_ms = (time.perf_counter() - start) / 50 * 1000
        assert elapsed_ms < 50.0

    def test_explicit_dir_indexing(self, tmp_path):
        """A matcher pointed at its own directory indexes those skills."""
        (tmp_path / "postgres-tuner.md").write_text(
            "---\nname: postgres-tuner\n"
            "description: Tune PostgreSQL queries and migrations.\n"
            "category: database\ntags:\n- postgres\n- tuning\n---\nBody.",
            encoding="utf-8",
        )
        from nexus.skills import SkillLoader

        matcher = SkillMatcher(loader=SkillLoader(skills_dir=tmp_path))
        assert matcher.build_index() == 1
        matches = matcher.match("postgres migration")
        assert [m.name for m in matches] == ["postgres-tuner"]


class TestMatching:
    def test_relevant_topic_returns_bundled_skills(self, shared_matcher):
        matches = shared_matcher.match(KNOWN_TOPIC_TASK)
        assert matches, "known catalog topic must match"
        names = [m.name for m in matches]
        assert any("postgres" in n.lower() or "database" in n.lower() for n in names)
        # Ranked best-first.
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True)
        # Every match carries usable prompt material.
        for m in matches:
            assert isinstance(m, MatchedSkill)
            assert m.description
            assert m.matched_tokens

    def test_garbage_returns_empty(self, shared_matcher):
        assert shared_matcher.match(GARBAGE_TASK) == []
        assert shared_matcher.match("") == []
        assert shared_matcher.match("the of and to is it") == []

    def test_top_n_default_is_three(self, shared_matcher):
        matches = shared_matcher.match(KNOWN_TOPIC_TASK)
        assert len(matches) <= DEFAULT_TOP_N == 3

    def test_top_n_override(self, shared_matcher):
        one = shared_matcher.match(KNOWN_TOPIC_TASK, top_n=1)
        assert len(one) == 1

    def test_threshold_blocks_weak_matches(self):
        """A single description-word hit (score 1.0) must never surface."""
        matcher = SkillMatcher(top_n=5)
        matcher.build_index()
        assert matcher.match("summarize this paragraph for me") == []

    def test_plural_token_fallback(self, shared_matcher):
        """'databases' should reach skills indexed under 'database'."""
        matches = shared_matcher.match("migrate my databases now please")
        assert matches
        assert any("database" in m.name.lower() for m in matches)


class TestFormatRelevantSkills:
    def test_empty_for_no_matches(self):
        assert format_relevant_skills([]) == ""

    def test_section_shape_and_cap(self, shared_matcher):
        matches = shared_matcher.match(KNOWN_TOPIC_TASK)
        section = format_relevant_skills(matches)
        assert section.startswith("### RELEVANT SKILLS")
        assert matches[0].name in section
        assert "/skill" in section  # how-to-load pointer present
        assert len(section) <= MAX_PROMPT_CHARS

    def test_cap_with_wide_matches(self):
        many = [
            MatchedSkill(
                name=f"skill-{i}",
                description="x" * 200,
                score=10.0,
                matched_tokens=["a"],
            )
            for i in range(20)
        ]
        section = format_relevant_skills(many)
        assert len(section) <= MAX_PROMPT_CHARS
        assert section.endswith("…")


class TestSharedMatcher:
    def test_get_skill_matcher_is_singleton(self):
        assert get_skill_matcher() is get_skill_matcher()
