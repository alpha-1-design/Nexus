"""Tests for BM25 lexical recall in nexus.memory.vectors (replaces LIKE search)."""

import asyncio
import time

from nexus.memory.vectors import MemoryEntry, SimpleKeywordBackend, VectorMemory


def make_backend(tmp_path):
    return SimpleKeywordBackend(str(tmp_path / "mem.db"))


def add(backend, content, created_at=None, entry_id=None, metadata=None):
    entry = MemoryEntry(
        id=entry_id or content[:12].replace(" ", "_"),
        content=content,
        metadata=metadata or {},
        created_at=created_at if created_at is not None else time.time(),
    )
    asyncio.run(backend.add(entry))


def test_bm25_ranks_relevant_above_irrelevant(tmp_path):
    b = make_backend(tmp_path)
    add(b, "The pytest framework makes it easy to write small Python tests with fixtures and assertions.")
    add(b, "Simmer the pasta sauce for twenty minutes and add fresh basil before serving.")
    add(b, "Python packaging guide: build wheels with setuptools and publish to PyPI.")
    results = asyncio.run(b.search("pytest writing tests", limit=3))
    assert results, "expected at least one hit"
    assert results[0].content.startswith("The pytest")
    assert "pasta" not in results[0].content


def test_recency_boost_newer_first(tmp_path):
    b = make_backend(tmp_path)
    now = time.time()
    add(b, "Deploy checklist: run the test suite then tag the release.", created_at=now - 90 * 86400, entry_id="old")
    add(b, "Deploy checklist: run the test suite then tag the release.", created_at=now, entry_id="new")
    results = asyncio.run(b.search("deploy checklist release", limit=2))
    assert [r.id for r in results] == ["new", "old"]


def test_exact_phrase_bonus(tmp_path):
    b = make_backend(tmp_path)
    # The phrase doc is longer, so plain BM25 would favor the short doc; the
    # exact-phrase bonus must flip the ranking.
    add(b, "Our machine learning pipeline ingests data from many sources every night and produces metrics.", entry_id="phrase")
    add(b, "machine learning diagrams and pipeline sketches.", entry_id="scatter")
    results = asyncio.run(b.search("machine learning pipeline", limit=2))
    assert results[0].id == "phrase"


def test_stemming_matches_plurals(tmp_path):
    b = make_backend(tmp_path)
    add(b, "Fix the database migrations for user accounts.", entry_id="dbs")
    results = asyncio.run(b.search("database migration", limit=2))
    assert results and results[0].id == "dbs"


def test_metadata_filters(tmp_path):
    b = make_backend(tmp_path)
    add(b, "alpha notebook entry", metadata={"k": "a"}, entry_id="a")
    add(b, "beta notebook entry", metadata={"k": "b"}, entry_id="b")
    res = asyncio.run(b.search("notebook", limit=5, filters={"k": "b"}))
    assert [r.id for r in res] == ["b"]


def test_empty_query_returns_nothing(tmp_path):
    b = make_backend(tmp_path)
    add(b, "some content here")
    assert asyncio.run(b.search("   ", limit=5)) == []


def test_vector_memory_interface_unchanged(tmp_path):
    vm = VectorMemory(db_path=str(tmp_path / "vm.db"))
    eid = asyncio.run(vm.store("Nexus uses BM25 for lexical memory recall now."))
    assert asyncio.run(vm.count()) == 1
    hits = asyncio.run(vm.recall("BM25 lexical recall", limit=3))
    assert hits and "BM25" in hits[0].content
    asyncio.run(vm.forget(eid))
    assert asyncio.run(vm.count()) == 0
