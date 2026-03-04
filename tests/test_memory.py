"""Comprehensive tests for vector memory: add, dedup, search, delete, bump, decay."""

import asyncio
import random
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.memory_manager import Memory
from nanobot.providers.vector_store import DECAY_RATES, MemoryMetadata, MemoryRecord, VectorStore

# ── Helpers ────────────────────────────────────────────────────────

DIMS = 32  # Small dims for fast tests


def _rand_embedding(dims: int = DIMS) -> list[float]:
    """Generate a random unit-ish embedding vector."""
    vec = [random.gauss(0, 1) for _ in range(dims)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


def _deterministic_embedding(seed: int, dims: int = DIMS) -> list[float]:
    """Generate a deterministic embedding for repeatable search tests."""
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dims)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


def _make_mock_embeddings() -> AsyncMock:
    """Create a mock EmbeddingProvider that returns deterministic vectors."""
    mock = AsyncMock()
    mock.dimensions = DIMS
    # Each call gets a hash-based deterministic vector
    call_count = 0

    async def _embed(text, model=None):
        nonlocal call_count
        call_count += 1
        return _deterministic_embedding(hash(text), DIMS)

    mock.embed = _embed
    return mock


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path) -> VectorStore:
    """Create a fresh VectorStore with small dimensions."""
    db = tmp_path / "test_memory.db"
    s = VectorStore(db_path=db, dimensions=DIMS)
    s.connect()
    yield s
    s.close()


@pytest.fixture
def memory(tmp_path) -> Memory:
    """Create a Memory manager with mock embeddings and real VectorStore."""
    db = tmp_path / "test_memory.db"
    vs = VectorStore(db_path=db, dimensions=DIMS)
    vs.connect()
    embeddings = _make_mock_embeddings()
    mem = Memory(embeddings=embeddings, store=vs)
    yield mem
    vs.close()


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── VectorStore: insert and get ───────────────────────────────────


def test_store_insert_and_get(store):
    record = MemoryRecord(
        id="mem-1",
        content="The cat likes fish",
        metadata=MemoryMetadata(
            tag="default",
            weight=0.5,
            timestamp="2024-01-01T00:00:00",
            author="test",
            categories=["preference"],
            content_hash=MemoryRecord.content_hash("The cat likes fish"),
        ),
    )
    store.insert(record, _rand_embedding())

    fetched = store.get("mem-1")
    assert fetched is not None
    assert fetched.content == "The cat likes fish"
    assert fetched.metadata.tag == "default"
    assert fetched.metadata.weight == 0.5


def test_store_get_nonexistent(store):
    assert store.get("nonexistent") is None


def test_store_get_by_hash(store):
    content = "Unique content for hash test"
    ch = MemoryRecord.content_hash(content)
    record = MemoryRecord(
        id="hash-1",
        content=content,
        metadata=MemoryMetadata(
            tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash=ch,
        ),
    )
    store.insert(record, _rand_embedding())

    fetched = store.get_by_hash(ch)
    assert fetched is not None
    assert fetched.id == "hash-1"


def test_store_get_by_hash_miss(store):
    assert store.get_by_hash("nonexistent_hash") is None


# ── VectorStore: delete ───────────────────────────────────────────


def test_store_delete(store):
    record = MemoryRecord(
        id="del-1",
        content="to be deleted",
        metadata=MemoryMetadata(
            tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash="h1",
        ),
    )
    store.insert(record, _rand_embedding())
    assert store.get("del-1") is not None

    assert store.delete("del-1") is True
    assert store.get("del-1") is None


def test_store_delete_nonexistent(store):
    assert store.delete("nonexistent") is False


# ── VectorStore: update_weight ────────────────────────────────────


def test_store_update_weight(store):
    record = MemoryRecord(
        id="w-1",
        content="weight test",
        metadata=MemoryMetadata(
            tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash="w1",
        ),
    )
    store.insert(record, _rand_embedding())

    assert store.update_weight("w-1", 0.9) is True
    fetched = store.get("w-1")
    assert fetched.metadata.weight == pytest.approx(0.9)


def test_store_update_weight_nonexistent(store):
    assert store.update_weight("nonexistent", 0.5) is False


# ── VectorStore: count ────────────────────────────────────────────


def test_store_count_empty(store):
    assert store.count() == 0


def test_store_count_with_filters(store):
    for i, tag in enumerate(["default", "default", "crucial", "core"]):
        record = MemoryRecord(
            id=f"cnt-{i}",
            content=f"memory {i}",
            metadata=MemoryMetadata(
                tag=tag, weight=0.5, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"ch{i}",
                project="proj-a" if i < 2 else None,
            ),
        )
        store.insert(record, _rand_embedding())

    assert store.count() == 4
    assert store.count(tag="default") == 2
    assert store.count(tag="crucial") == 1
    assert store.count(tag="core") == 1
    assert store.count(project="proj-a") == 2


# ── VectorStore: search ──────────────────────────────────────────


def test_store_search_returns_results(store):
    for i in range(5):
        emb = _deterministic_embedding(i)
        record = MemoryRecord(
            id=f"s-{i}",
            content=f"search memory {i}",
            metadata=MemoryMetadata(
                tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"sh{i}",
            ),
        )
        store.insert(record, emb)

    # Search with the same embedding as s-0 should return s-0 first
    results = store.search(_deterministic_embedding(0), limit=3)
    assert len(results) > 0
    assert results[0].id == "s-0"


def test_store_search_respects_limit(store):
    for i in range(10):
        record = MemoryRecord(
            id=f"lim-{i}",
            content=f"limit test {i}",
            metadata=MemoryMetadata(
                tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"lh{i}",
            ),
        )
        store.insert(record, _rand_embedding())

    results = store.search(_rand_embedding(), limit=3)
    assert len(results) <= 3


def test_store_search_min_weight_filter(store):
    # One low-weight, one high-weight
    for i, w in enumerate([0.05, 0.8]):
        record = MemoryRecord(
            id=f"mw-{i}",
            content=f"weight filter {i}",
            metadata=MemoryMetadata(
                tag="default", weight=w, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"mwh{i}",
            ),
        )
        store.insert(record, _rand_embedding())

    results = store.search(_rand_embedding(), limit=10, min_weight=0.5)
    assert all(r.metadata.weight >= 0.5 for r in results)


# ── VectorStore: decay_weights ────────────────────────────────────


def test_store_decay_weights_by_tag(store):
    """Decay should reduce default/crucial weights but not core."""
    records = [
        ("d-core", "core memory", "core", 0.8),
        ("d-crucial", "crucial memory", "crucial", 0.8),
        ("d-default", "default memory", "default", 0.8),
    ]
    for mid, content, tag, weight in records:
        record = MemoryRecord(
            id=mid, content=content,
            metadata=MemoryMetadata(
                tag=tag, weight=weight, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"dh-{mid}",
            ),
        )
        store.insert(record, _rand_embedding())

    results = store.decay_weights()

    # Core should not be decayed
    assert results["core"] == 0
    core = store.get("d-core")
    assert core.metadata.weight == pytest.approx(0.8)

    # Crucial should decay by 0.01
    assert results["crucial"] == 1
    crucial = store.get("d-crucial")
    assert crucial.metadata.weight == pytest.approx(0.8 - DECAY_RATES["crucial"])

    # Default should decay by 0.05
    assert results["default"] == 1
    default = store.get("d-default")
    assert default.metadata.weight == pytest.approx(0.8 - DECAY_RATES["default"])


def test_store_decay_floor(store):
    """Weight should not drop below 0.1 floor."""
    record = MemoryRecord(
        id="floor-1",
        content="low weight",
        metadata=MemoryMetadata(
            tag="default", weight=0.12, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash="fh1",
        ),
    )
    store.insert(record, _rand_embedding())

    store.decay_weights()
    fetched = store.get("floor-1")
    assert fetched.metadata.weight == pytest.approx(0.1)


def test_store_decay_at_floor_no_change(store):
    """Memories at the floor should not be decayed further."""
    record = MemoryRecord(
        id="at-floor",
        content="at floor",
        metadata=MemoryMetadata(
            tag="default", weight=0.1, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash="afh",
        ),
    )
    store.insert(record, _rand_embedding())

    results = store.decay_weights()
    assert results["default"] == 0  # Already at floor, no update
    fetched = store.get("at-floor")
    assert fetched.metadata.weight == pytest.approx(0.1)


def test_store_decay_multiple_rounds(store):
    """Multiple decay rounds should progressively lower weight."""
    record = MemoryRecord(
        id="multi-decay",
        content="multi round",
        metadata=MemoryMetadata(
            tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
            author="test", categories=[], content_hash="mdh",
        ),
    )
    store.insert(record, _rand_embedding())

    rate = DECAY_RATES["default"]
    expected = 0.5

    for _ in range(5):
        store.decay_weights()
        expected = max(0.1, expected - rate)

    fetched = store.get("multi-decay")
    assert fetched.metadata.weight == pytest.approx(expected)


# ── VectorStore: decisions and projects ───────────────────────────


def test_store_list_decisions(store):
    for i, is_decision in enumerate([True, False, True]):
        record = MemoryRecord(
            id=f"dec-{i}",
            content=f"decision {i}",
            metadata=MemoryMetadata(
                tag="default", weight=0.5, timestamp=f"2024-01-0{i+1}T00:00:00",
                author="test", categories=[], content_hash=f"dech{i}",
                decision=is_decision,
            ),
        )
        store.insert(record, _rand_embedding())

    decisions = store.list_decisions()
    assert len(decisions) == 2
    assert all(d.metadata.decision for d in decisions)


def test_store_list_by_project(store):
    for i, proj in enumerate(["alpha", "alpha", "beta"]):
        record = MemoryRecord(
            id=f"proj-{i}",
            content=f"project memory {i}",
            metadata=MemoryMetadata(
                tag="default", weight=0.5, timestamp="2024-01-01T00:00:00",
                author="test", categories=[], content_hash=f"prjh{i}",
                project=proj,
            ),
        )
        store.insert(record, _rand_embedding())

    alpha = store.list_by_project("alpha")
    assert len(alpha) == 2
    beta = store.list_by_project("beta")
    assert len(beta) == 1


# ── Memory manager: add ──────────────────────────────────────────


def test_memory_add_basic(memory):
    record = _run(memory.add(content="Skye likes coffee", author="test"))
    assert record is not None
    assert record.content == "Skye likes coffee"
    assert record.metadata.author == "test"
    assert record.metadata.tag == "default"


def test_memory_add_with_tag_and_categories(memory):
    record = _run(memory.add(
        content="Critical system config",
        author="system",
        tag="core",
        categories=["system", "config"],
    ))
    assert record is not None
    assert record.metadata.tag == "core"
    assert "system" in record.metadata.categories


def test_memory_add_empty_rejected(memory):
    assert _run(memory.add(content="", author="test")) is None
    assert _run(memory.add(content="   ", author="test")) is None


def test_memory_add_none_rejected(memory):
    assert _run(memory.add(content=None, author="test")) is None


# ── Memory manager: deduplication ─────────────────────────────────


def test_memory_dedup_hash_based(memory):
    """Second add of same content should bump weight, not create duplicate."""
    r1 = _run(memory.add(content="duplicate content", author="test"))
    assert r1 is not None
    original_weight = r1.metadata.weight

    # Clear timing cache so hash-based dedup kicks in (not timing dedup)
    memory._recent_hashes.clear()

    r2 = _run(memory.add(content="duplicate content", author="test"))
    assert r2 is not None
    assert r2.id == r1.id  # Same record returned

    # Weight should have been bumped
    fetched = memory.store.get(r1.id)
    assert fetched.metadata.weight == pytest.approx(original_weight + 0.1)


def test_memory_dedup_timing_based(memory):
    """Rapid duplicate within cooldown should be rejected."""
    r1 = _run(memory.add(content="rapid duplicate", author="test"))
    assert r1 is not None

    # Second add within cooldown — timing dedup rejects before hash check
    r2 = _run(memory.add(content="rapid duplicate", author="test"))
    assert r2 is None


def test_memory_dedup_case_insensitive_hash(memory):
    """Content hash is case-insensitive, so 'Hello' and 'hello' dedup."""
    r1 = _run(memory.add(content="Hello World", author="test"))
    assert r1 is not None
    memory._recent_hashes.clear()

    r2 = _run(memory.add(content="hello world", author="test"))
    # Should be deduped (same hash after .lower().strip())
    assert r2 is not None
    assert r2.id == r1.id


# ── Memory manager: search ────────────────────────────────────────


def test_memory_search_finds_added(memory):
    _run(memory.add(content="The weather is sunny today", author="test"))
    _run(memory.add(content="Python is a programming language", author="test"))

    # Search with same text should find the exact memory
    results = _run(memory.search("The weather is sunny today"))
    assert len(results) > 0
    assert any("weather" in r.content.lower() for r in results)


def test_memory_search_empty_store(memory):
    results = _run(memory.search("anything"))
    assert results == []


def test_memory_search_respects_limit(memory):
    for i in range(10):
        _run(memory.add(content=f"Memory number {i} with unique content", author="test"))

    results = _run(memory.search("Memory number", limit=3))
    assert len(results) <= 3


# ── Memory manager: delete ────────────────────────────────────────


def test_memory_delete(memory):
    record = _run(memory.add(content="to be forgotten", author="test"))
    assert record is not None

    assert memory.delete(record.id) is True
    assert memory.get(record.id) is None


def test_memory_delete_nonexistent(memory):
    assert memory.delete("nonexistent-id") is False


# ── Memory manager: bump_weight ───────────────────────────────────


def test_memory_bump_weight(memory):
    record = _run(memory.add(content="important fact", author="test", weight=0.5))
    assert record is not None

    assert memory.bump_weight(record.id, amount=0.2) is True
    fetched = memory.get(record.id)
    assert fetched.metadata.weight == pytest.approx(0.7)


def test_memory_bump_weight_caps_at_one(memory):
    record = _run(memory.add(content="max weight", author="test", weight=0.95))
    assert record is not None

    memory.bump_weight(record.id, amount=0.2)
    fetched = memory.get(record.id)
    assert fetched.metadata.weight == pytest.approx(1.0)


def test_memory_bump_weight_nonexistent(memory):
    assert memory.bump_weight("nonexistent") is False


# ── Memory manager: decay_all ─────────────────────────────────────


def test_memory_decay_all(memory):
    """Full decay test across all tag tiers."""
    _run(memory.add(content="core identity", author="test", tag="core", weight=0.8))
    _run(memory.add(content="important relationship", author="test", tag="crucial", weight=0.8))
    _run(memory.add(content="casual observation", author="test", tag="default", weight=0.8))

    results = memory.decay_all()
    assert results["core"] == 0  # Core never decays
    assert results["crucial"] == 1
    assert results["default"] == 1


def test_memory_decay_all_empty(memory):
    results = memory.decay_all()
    assert sum(results.values()) == 0


# ── Full lifecycle test ───────────────────────────────────────────


def test_full_lifecycle(memory):
    """End-to-end: add → dedup → search → bump → decay → delete."""
    # 1. Add memories with different tags
    core = _run(memory.add(
        content="I am a cognitive agent", author="self", tag="core", weight=0.9,
    ))
    crucial = _run(memory.add(
        content="Skye is my creator", author="self", tag="crucial", weight=0.7,
    ))
    default = _run(memory.add(
        content="Had a conversation about weather", author="daily_summary",
        tag="default", weight=0.5,
    ))
    assert core is not None
    assert crucial is not None
    assert default is not None
    assert memory.count() == 3

    # 2. Dedup — same content returns existing record with bumped weight
    memory._recent_hashes.clear()
    dedup = _run(memory.add(content="Skye is my creator", author="other"))
    assert dedup.id == crucial.id
    fetched_crucial = memory.get(crucial.id)
    assert fetched_crucial.metadata.weight == pytest.approx(0.8)  # 0.7 + 0.1

    # 3. Search — find memories
    results = _run(memory.search("I am a cognitive agent"))
    assert len(results) > 0

    # 4. Bump weight on access (recall reinforcement)
    memory.bump_weight(default.id, amount=0.15)
    fetched_default = memory.get(default.id)
    assert fetched_default.metadata.weight == pytest.approx(0.65)  # 0.5 + 0.15

    # 5. Decay all — tag-based
    decay_results = memory.decay_all()
    assert decay_results["core"] == 0

    # Core stays at 0.9
    assert memory.get(core.id).metadata.weight == pytest.approx(0.9)
    # Crucial: 0.8 - 0.01 = 0.79
    assert memory.get(crucial.id).metadata.weight == pytest.approx(0.79)
    # Default: 0.65 - 0.05 = 0.60
    assert memory.get(default.id).metadata.weight == pytest.approx(0.60)

    # 6. Delete
    assert memory.delete(default.id) is True
    assert memory.count() == 2
    assert memory.get(default.id) is None

    # 7. Remaining memories intact
    assert memory.get(core.id) is not None
    assert memory.get(crucial.id) is not None


# ── MemoryRecord helpers ──────────────────────────────────────────


def test_content_hash_deterministic():
    h1 = MemoryRecord.content_hash("hello world")
    h2 = MemoryRecord.content_hash("hello world")
    assert h1 == h2


def test_content_hash_case_insensitive():
    h1 = MemoryRecord.content_hash("Hello World")
    h2 = MemoryRecord.content_hash("hello world")
    assert h1 == h2


def test_content_hash_strips_whitespace():
    h1 = MemoryRecord.content_hash("  hello world  ")
    h2 = MemoryRecord.content_hash("hello world")
    assert h1 == h2


def test_generate_id_unique():
    id1 = MemoryRecord.generate_id("content a")
    id2 = MemoryRecord.generate_id("content b")
    assert id1 != id2


def test_generate_id_with_prefix():
    mid = MemoryRecord.generate_id("test", prefix="proj")
    assert mid.startswith("proj_")


# ── Decay rate constants ──────────────────────────────────────────


def test_decay_rates_core_zero():
    assert DECAY_RATES["core"] == 0.0


def test_decay_rates_crucial_slow():
    assert DECAY_RATES["crucial"] == 0.01


def test_decay_rates_default_normal():
    assert DECAY_RATES["default"] == 0.05
