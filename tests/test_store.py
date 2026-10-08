"""The OptMem file format: fixed-width records, the shared lock and the config file."""

import sys
import threading
import types

import pytest

from vis_optmem import store as store_module
from vis_optmem.store import LOG_RECORD, TREE_RECORD, FileStore, parse_size


@pytest.fixture
def store(folder):
    created = FileStore(folder)
    assert created.create() is True
    return created


def test_memories_are_fixed_width_records_in_the_optmem_layout(store):
    assert store.append([("2026-01-02", "first"), ("2026-01-03", "zażółć")]) == 0
    data = store.log_path.read_bytes()
    assert len(data) == 2 * LOG_RECORD
    first, second = data[:LOG_RECORD], data[LOG_RECORD:]
    assert first == b"#0 2026-01-02 first".ljust(LOG_RECORD - 1) + b"\n"
    assert second.decode("utf-8").rstrip() == "#1 2026-01-03 zażółć"
    assert [entry.line for entry in store.scan()] == [
        "#0 2026-01-02 first",
        "#1 2026-01-03 zażółć",
    ]


def test_summaries_are_fixed_width_records_of_one_level_file(store):
    store.append([("2026-01-02", f"m{index}") for index in range(4)])
    assert store.put_summary(0, 2, "low pair")
    assert store.put_summary(2, 4, "high pair")
    assert (
        store.level_path(2).read_bytes()[:TREE_RECORD]
        == b"low pair".ljust(TREE_RECORD - 1) + b"\n"
    )
    assert store.summary(2, 4) == "high pair"
    assert store.summary(0, 4) is None


def test_a_summary_out_of_order_is_refused(store):
    store.append([("2026-01-02", f"m{index}") for index in range(4)])
    assert store.put_summary(2, 4, "too early") is False
    assert store.level_count(2) == 0


def test_a_partial_record_from_a_crash_is_dropped_before_the_next_append(store):
    store.append([("2026-01-02", "kept")])
    with open(store.log_path, "ab") as handle:
        handle.write(b"#1 2026-01-02 half writ")
    assert store.count() == 1
    assert store.append([("2026-01-02", "next")]) == 1
    assert [entry.line for entry in store.scan()] == [
        "#0 2026-01-02 kept",
        "#1 2026-01-02 next",
    ]


def test_a_record_that_does_not_fit_writes_nothing(store):
    with pytest.raises(ValueError, match="Too long"):
        store.append([("2026-01-02", "fits"), ("2026-01-02", "x" * LOG_RECORD)])
    assert store.count() == 0


def test_dropping_a_summary_drops_later_ones_and_the_levels_above(store):
    store.append([("2026-01-02", f"m{index}") for index in range(8)])
    for lo in range(0, 8, 2):
        assert store.put_summary(lo, lo + 2, f"pair {lo}")
    assert store.put_summary(0, 4, "quad 0")
    assert store.put_summary(4, 8, "quad 4")
    assert store.put_summary(0, 8, "octet")
    assert store.drop_summaries(2, 4) == [
        (2, 4),
        (4, 6),
        (6, 8),
        (0, 4),
        (4, 8),
        (0, 8),
    ]
    assert [store.level_count(size) for size in (2, 4, 8)] == [1, 0, 0]
    assert store.due(8) == [(2, 4), (4, 6), (6, 8), (0, 4), (4, 8), (0, 8)]
    assert store.due_count(8) == 6


def test_parallel_writers_get_distinct_ids(store):
    def write(worker):
        for index in range(25):
            store.append([("2026-01-02", f"worker {worker} memory {index}")])

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    entries = list(store.scan())
    assert [entry.id for entry in entries] == list(range(200))
    assert len({entry.text for entry in entries}) == 200


def test_windows_takes_the_lock_on_the_first_byte_and_waits_for_it(store, monkeypatch):
    calls = []
    busy = [OSError("locked"), None]

    def locking(fd, mode, size):
        calls.append((mode, size))
        if mode == "try" and busy:
            failure = busy.pop(0)
            if failure:
                raise failure

    fake = types.SimpleNamespace(LK_NBLCK="try", LK_UNLCK="free", locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(store_module, "fcntl", None)
    monkeypatch.setattr(store_module.time, "sleep", lambda seconds: None)
    assert store.append([("2026-01-02", "on windows")]) == 0
    assert calls == [("try", 1), ("try", 1), ("free", 1)]


def test_config_keeps_defaults_as_comments_and_reads_overrides(store):
    assert store.overrides() == {}
    text = store.config_path.read_text()
    assert "# WAKE_LINES = 96" in text
    store.write_overrides({"WAKE_LINES": 300})
    assert store.overrides() == {"WAKE_LINES": 300}
    assert store.sizes()["WAKE_LINES"] == 300
    assert store.sizes()["ENTRY_CHARS"] == 280


def test_a_wrong_config_line_names_its_line(store):
    store.config_path.write_text("WAKE_LINES = 10\nSPEED = 3\n")
    with pytest.raises(ValueError, match="config line 2: SPEED is not a size"):
        store.sizes()
    store.config_path.write_text("wake_lines = ten\n")
    with pytest.raises(
        ValueError, match="config line 1: WAKE_LINES must be a positive"
    ):
        store.sizes()


@pytest.mark.parametrize("value", ["0", "-1", "2.5", "", True, "²"])
def test_sizes_are_positive_whole_numbers(value):
    with pytest.raises(ValueError, match="positive whole number"):
        parse_size("WAKE_LINES", value)


def test_entry_size_cannot_outgrow_a_record():
    assert parse_size("ENTRY_CHARS", 280) == 280
    with pytest.raises(ValueError, match="at most 280"):
        parse_size("ENTRY_CHARS", 281)


def test_a_summary_that_is_not_text_is_reported_as_damaged(store):
    store.append([("2026-01-02", "a"), ("2026-01-02", "b")])
    store.level_path(2).write_bytes(b"\xff" * (TREE_RECORD - 1) + b"\n")
    with pytest.raises(store_module.DamagedSummary, match=r'memo.forget\("0-1"\)'):
        store.summary(0, 2)
