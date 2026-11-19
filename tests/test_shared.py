"""A SharedStore gives the same memory as the files, and many writers keep it whole."""

import importlib.util
import multiprocessing
import sys
import threading
import zlib
from pathlib import Path

import pytest
import test_custom_store as custom

from vis_optmem import memo as memo_module
from vis_optmem.memo import Memo
from vis_optmem.shared import (
    MemoryTable,
    SharedStore,
    SqliteTable,
    Table,
    WriteConflict,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SPAWN = multiprocessing.get_context("spawn")
# Start children with the interpreter that runs this test. A launcher such as
# `vis-agent python` can show a virtual environment link that runs another Python.
SPAWN.set_executable(getattr(sys, "_base_executable", sys.executable))


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(memo_module, "_loaded", {})


def embed(texts):
    """A small embedding for tests: hashed letter triples, so similar texts are close."""
    vectors = []
    for text in texts:
        vector = [0.0] * 64
        padded = f"  {text.lower()}  "
        for at in range(len(padded) - 2):
            vector[zlib.crc32(padded[at : at + 3].encode()) % 64] += 1.0
        vectors.append(vector)
    return vectors


class Counting(Table):
    """A table that counts its calls and can let another writer go first once."""

    def __init__(self, inner: Table) -> None:
        self.inner = inner
        self.location = inner.location
        self.max_writes = inner.max_writes
        self.queries: list[tuple] = []
        self.before_write = None

    def get(self, key):
        return self.inner.get(key)

    def query(self, prefix, start="", limit=None):
        self.queries.append((prefix, start))
        return self.inner.query(prefix, start, limit)

    def transact(self, puts=None, deletes=(), conditions=()):
        if self.before_write is not None:
            first, self.before_write = self.before_write, None
            first()
        return self.inner.transact(puts, deletes, conditions)


def new_store(table, **options):
    store = SharedStore(table, **options)
    store.create()
    return store


@pytest.mark.parametrize("options", [{}, {"embed": embed}], ids=["words", "vectors"])
@pytest.mark.parametrize("kind", ["sqlite", "memory"])
def test_a_shared_store_gives_the_same_memory_as_the_files(tmp_path, kind, options):
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 first imported\n2999-01-02 second imported\n")
    table = SqliteTable(tmp_path / "shared.db") if kind == "sqlite" else MemoryTable()
    files = custom.run(Memo(tmp_path / "memory"), source)
    shared = custom.run(Memo(store=SharedStore(table, **options)), source)
    assert shared == files


def test_the_sqlite_store_loads_by_name(tmp_path):
    store = memo_module.load_store(
        "vis_optmem.shared:sqlite_store", {"path": str(tmp_path / "m.db")}
    )
    assert Memo(store=store).init().path == f"sqlite:{tmp_path / 'm.db'}"


def test_an_instance_reads_the_writes_of_another_one(tmp_path):
    first = new_store(SqliteTable(tmp_path / "m.db"))
    second = SharedStore(SqliteTable(tmp_path / "m.db"))
    first.append([("2026-01-01", "a"), ("2026-01-01", "b")])
    assert second.count() == 2
    assert second.append([("2026-01-02", "c")]) == 2
    assert [e.text for e in first.entries(0, 3)] == ["a", "b", "c"]
    first.write_overrides({"WAKE_LINES": 7})
    assert second.sizes()["WAKE_LINES"] == 7


def test_sync_reads_only_the_new_memories_in_one_query():
    table = Counting(MemoryTable())
    store = new_store(table)
    store.append([("2026-01-01", f"memory {i}") for i in range(5)])
    table.queries.clear()
    other = SharedStore(table.inner)
    other.append([("2026-01-01", "memory 5")])
    assert store.count() == 6
    assert table.queries == [("n#", "n#0000000005")]


def test_a_write_after_a_conflict_takes_the_next_ids():
    table = Counting(MemoryTable())
    store = new_store(table)
    rival = SharedStore(table.inner)
    table.before_write = lambda: rival.append([("2026-01-01", "rival")])
    assert store.append([("2026-01-01", "mine")]) == 1
    assert [e.line for e in store.scan()] == [
        "#0 2026-01-01 rival",
        "#1 2026-01-01 mine",
    ]


@pytest.mark.parametrize("options", [{}, {"embed": embed}], ids=["words", "vectors"])
def test_a_note_after_a_conflict_checks_the_new_memories_again(options):
    table = Counting(MemoryTable())
    store = new_store(table, **options)
    rival = SharedStore(table.inner, **options)
    fact = "The deploy of vis uses port 5432 on staging."
    table.before_write = lambda: rival.note("2026-01-01", fact)
    twin, is_new = store.note("2026-01-01", fact)
    assert (twin.id, is_new) == (0, False)
    assert store.count() == 1


def test_a_write_raises_at_the_deadline():
    class Busy(MemoryTable):
        def transact(self, puts=None, deletes=(), conditions=()):
            return False

    store = SharedStore(Busy(), deadline_s=0.05)
    with pytest.raises(WriteConflict, match="other writers changed the memory first"):
        store.append([("2026-01-01", "never")])


def _append_many(path, writer, count):
    store = SharedStore(SqliteTable(path), deadline_s=60)
    for index in range(count):
        store.append([("2026-01-01", f"writer {writer} note {index}")])


def _note_once(path, fact, start, results):
    store = SharedStore(SqliteTable(path), embed=embed, deadline_s=60)
    store.sync()
    start.wait()
    results.put(store.note("2026-01-01", fact)[1])


def test_processes_that_write_at_once_keep_ids_dense_without_loss(tmp_path):
    path = tmp_path / "m.db"
    new_store(SqliteTable(path))
    workers = [
        SPAWN.Process(target=_append_many, args=(path, writer, 15))
        for writer in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(120)
    assert [worker.exitcode for worker in workers] == [0] * 4
    entries = list(SharedStore(SqliteTable(path)).scan())
    assert [entry.id for entry in entries] == list(range(60))
    assert sorted(entry.text for entry in entries) == sorted(
        f"writer {w} note {i}" for w in range(4) for i in range(15)
    )


def test_processes_that_note_one_fact_at_once_save_it_once(tmp_path):
    path = tmp_path / "m.db"
    new_store(SqliteTable(path), embed=embed).append(
        [("2026-01-01", f"older fact {i}") for i in range(3)]
    )
    start, results = SPAWN.Event(), SPAWN.Queue()
    fact = "The team memory of vis lives in one SQLite file."
    workers = [
        SPAWN.Process(target=_note_once, args=(path, fact, start, results))
        for _ in range(6)
    ]
    for worker in workers:
        worker.start()
    start.set()
    outcomes = sorted(results.get(timeout=120) for _ in workers)
    for worker in workers:
        worker.join(120)
    assert outcomes == [False] * 5 + [True]
    texts = [entry.text for entry in SharedStore(SqliteTable(path)).scan()]
    assert texts.count(fact) == 1
    assert len(texts) == 4


def test_threads_that_note_one_fact_at_once_save_it_once():
    table = MemoryTable()
    new_store(table)
    start = threading.Barrier(8)
    results = []
    fact = "Builds of vis run on GraalVM CE 25."

    def note():
        store = SharedStore(table)
        start.wait()
        results.append(store.note("2026-01-01", fact)[1])

    threads = [threading.Thread(target=note) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False] * 7 + [True]
    assert SharedStore(table).count() == 1


def test_only_one_of_two_equal_summaries_is_saved(tmp_path):
    path = tmp_path / "m.db"
    new_store(SqliteTable(path)).append([("2026-01-01", f"m {i}") for i in range(8)])
    stores = [SharedStore(SqliteTable(path)) for _ in range(2)]
    for lo in range(0, 8, 2):
        start = threading.Barrier(2)
        won = []

        def put(store, text, lo=lo, start=start, won=won):
            start.wait()
            if store.put_summary(lo, lo + 2, text):
                won.append(text)

        threads = [
            threading.Thread(target=put, args=(store, f"by {n}"))
            for n, store in enumerate(stores)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(won) == 1
        assert stores[1].summary(lo, lo + 2) == won[0]
    assert stores[0].level_count(2) == 4
    assert not stores[0].put_summary(2, 4, "late")


def test_dropping_summaries_in_parts_hides_and_then_removes_them():
    table = MemoryTable(max_writes=3)
    store = new_store(table)
    store.append([("2026-01-01", f"m {i}") for i in range(3)])
    store.append([("2026-01-01", f"m {i}") for i in range(3, 6)])
    store.append([("2026-01-01", f"m {i}") for i in range(6, 8)])
    for lo, hi in store.due(8):
        assert store.put_summary(lo, hi, f"s {lo}-{hi}")
    assert store.drop_summaries(0, 2) == [
        (0, 2),
        (2, 4),
        (4, 6),
        (6, 8),
        (0, 4),
        (4, 8),
        (0, 8),
    ]
    assert not table.query("s#") and not table.query("l#")
    assert store.due(8)[0] == (0, 2)
    with pytest.raises(ValueError, match="at most 3 memories in one call"):
        store.append([("2026-01-01", "x")] * 4)


def test_vectors_are_saved_so_other_instances_do_not_embed_again():
    calls = []

    def counted(texts):
        calls.append(len(texts))
        return embed(texts)

    table = MemoryTable()
    new_store(table, embed=embed).append(
        [("2026-01-01", "Postgres runs on port 5432."), ("2026-01-02", "Redis cache")]
    )
    assert "vector" in table.get("n#0000000000")
    store = SharedStore(table, embed=counted)
    assert [e.id for e in store.search("which port does postgres use", 1)] == [0]
    assert calls == [1]


def test_vector_search_puts_newer_memories_first_on_equal_scores():
    store = new_store(MemoryTable(), embed=embed, search_min=0.5)
    store.append([("2026-01-01", "same text")] * 3 + [("2026-01-01", "other")])
    assert [e.id for e in store.search("same text", 5)] == [2, 1, 0]
    assert store.search("zzzz qqqq", 5) == []


def test_a_vector_repeat_needs_the_same_names():
    store = new_store(MemoryTable(), embed=embed)
    store.note("2026-01-01", "Postgres of vis listens on port 5432 on staging.")
    assert store.duplicate("Postgres of vis listens on port 5432 on staging!")
    assert store.duplicate("Postgres of vis listens on port 5433 on staging.") is None
    assert (
        store.duplicate("Postgres of vis listens on port 5432 on production.") is None
    )


def test_a_snapshot_starts_an_instance_with_one_short_sync(tmp_path):
    table = Counting(MemoryTable())
    calls = []

    def counted(texts):
        calls.append(len(texts))
        return embed(texts)

    first = new_store(table, embed=embed)
    first.append([("2026-01-01", f"memory {i}") for i in range(10)])
    snapshot = first.save_snapshot(tmp_path / "snap.json")
    first.append([("2026-01-02", "memory 10")])
    table.queries.clear()
    second = SharedStore(table, embed=counted, snapshot=snapshot)
    assert second.count() == 11
    assert table.queries == [("n#", "n#0000000010")]
    assert calls == []
    assert [e.text for e in second.search("memory 3", 1)] == ["memory 3"]


def test_a_damaged_snapshot_is_ignored(tmp_path):
    first = new_store(MemoryTable())
    first.append([("2026-01-01", "memory 0")])
    snapshot = tmp_path / "snap.json"
    for damaged in ("{not json", '{"version": 1, "id": "x", "memories": [1]}'):
        snapshot.write_text(damaged)
        store = SharedStore(first.table, snapshot=snapshot)
        assert [e.text for e in store.scan()] == ["memory 0"]


def test_a_snapshot_of_another_memory_is_not_used(tmp_path):
    first = new_store(MemoryTable())
    first.append([("2026-01-01", "old memory")])
    snapshot = first.save_snapshot(tmp_path / "snap.json")
    other = new_store(MemoryTable())
    other.append([("2026-01-01", "new memory"), ("2026-01-01", "second")])
    store = SharedStore(other.table, snapshot=snapshot)
    assert [e.text for e in store.scan()] == ["new memory", "second"]


@pytest.fixture
def dynamodb(monkeypatch):
    pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    spec = importlib.util.spec_from_file_location(
        "dynamodb_store", EXAMPLES / "dynamodb_store.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    with moto.mock_aws():
        example.create_table("team-memory")
        yield example


def test_the_dynamodb_example_gives_the_same_memory_as_the_files(tmp_path, dynamodb):
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 first imported\n2999-01-02 second imported\n")
    store = dynamodb.dynamodb_store("team-memory")
    files = custom.run(Memo(tmp_path / "memory"), source)
    assert custom.run(Memo(store=store), source) == files
    assert store.location == "dynamodb:team-memory/team"


def test_the_dynamodb_example_refuses_a_stale_write(dynamodb):
    make = dynamodb.dynamodb_store
    store = Counting(make("team-memory", memory="a").table)
    shared = new_store(store)
    rival = make("team-memory", memory="a")
    store.before_write = lambda: rival.append([("2026-01-01", "rival")])
    assert shared.append([("2026-01-01", "mine")]) == 1
    assert shared.put_summary(0, 2, "s 0-1")
    assert not rival.put_summary(0, 2, "again")
    assert rival.drop_summaries(0, 2) == [(0, 2)]
    assert make("team-memory", memory="b").count() == 0
