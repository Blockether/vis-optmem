"""The public operations: results, next steps, limits and failures."""

import datetime

import pytest
from conftest import fill

from vis_optmem import memo as memo_module
from vis_optmem.memo import Memo, parse_block, status
from vis_optmem.store import TREE_RECORD, FileStore


def test_only_init_creates_a_memory(folder):
    tool = Memo(folder)
    with pytest.raises(FileNotFoundError, match=r"call memo.init\(\)"):
        tool.wake()
    with pytest.raises(FileNotFoundError):
        tool.note("lost")
    assert not folder.exists()
    created = tool.init()
    assert (created.is_new, created.memories) == (True, 0)
    tool.note("kept")
    again = tool.init()
    assert (again.is_new, again.memories) == (False, 1)
    assert "1 memory" in again.text


def test_memory_dir_selects_the_folder(folder, monkeypatch):
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    assert Memo().init().path == str(folder)
    assert status() == {"memories": 0, "summaries_due": 0, "max_bytes": 280}


def refuse_base():
    raise AssertionError("A path that is not relative must not read the base.")


def test_a_relative_memory_dir_starts_at_the_base(tmp_path, monkeypatch):
    # Blockether/vis-optmem#1: a relative MEMORY_DIR started at the gateway directory.
    project = tmp_path / "project"
    monkeypatch.setenv("MEMORY_DIR", "memory")
    assert Memo(base=lambda: project).init().path == str(project / "memory")
    assert status(base=lambda: str(project)) == {
        "memories": 0,
        "summaries_due": 0,
        "max_bytes": 280,
    }
    assert Memo("other", base=lambda: project)._path() == project / "other"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{tmp}/absolute", "absolute"),
        ("~/memory", "home/memory"),
        (None, "home/.optmem/memory"),
    ],
)
def test_absolute_home_and_default_folders_ignore_the_base(
    tmp_path, monkeypatch, value, expected
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if value is None:
        monkeypatch.delenv("MEMORY_DIR", raising=False)
    else:
        monkeypatch.setenv("MEMORY_DIR", value.format(tmp=tmp_path))
    assert Memo(base=refuse_base)._path() == tmp_path / expected
    assert status(base=refuse_base)["path"].endswith(expected.split("/")[-1])


def test_without_a_base_a_relative_memory_dir_starts_at_the_process_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORY_DIR", "memory")
    assert Memo()._path() == tmp_path / "memory"


def test_an_empty_memory_is_awake_at_once(memo):
    woken = memo.wake()
    assert (woken.lines, woken.is_awake, woken.at) == ((), True, 0)
    assert woken.text.endswith("You are awake.")


def test_note_saves_one_line_with_the_date_and_the_next_id(memo):
    saved = memo.note("  The user prefers short answers.  ")
    assert (saved.id, saved.memory, saved.request) == (
        0,
        "The user prefers short answers.",
        None,
    )
    assert saved.date == datetime.date.today().isoformat()
    assert saved.text == "Saved as #0."


@pytest.mark.parametrize(
    ("memory", "message"),
    [
        ("   ", "empty"),
        ("one\ntwo", "2 lines"),
        ("x" * 281, "281 bytes, and the limit is 280"),
        ("ż" * 141, "282 bytes"),
    ],
)
def test_note_refuses_what_is_not_one_short_line(memo, memory, message):
    with pytest.raises(ValueError, match=message):
        memo.note(memory)
    assert memo.wake().at == 0


def test_a_full_pair_asks_for_its_summary_at_once(memo):
    memo.note("first")
    saved = memo.note("second")
    request = saved.request
    assert (request.block, request.remaining, request.max_bytes) == ("0-1", 0, 280)
    assert request.sources == (
        f"#0 {saved.date} first",
        f"#1 {saved.date} second",
    )
    assert request.text.endswith('Next: await memo.nap("0-1", "<your line>")')
    assert request.text in saved.text


def test_nap_saves_in_order_and_hands_over_the_next_request(memo):
    for index in range(4):
        memo.note(f"m{index}")
    first = memo.nap()
    assert (first.saved, first.request.block, first.request.remaining) == (
        None,
        "0-1",
        2,
    )
    with pytest.raises(ValueError, match="the next is 0-1"):
        memo.nap("2-3", "too early")
    saved = memo.nap("#0-1", "pair zero")
    assert (saved.saved, saved.summary, saved.request.block) == (
        "0-1",
        "pair zero",
        "2-3",
    )
    assert saved.text.startswith("#0-1 saved.\n\n")
    saved = memo.nap("2-3", "pair two")
    assert (saved.request.block, len(saved.request.sources)) == ("0-3", 4)
    done = memo.nap("0-3", "quad")
    assert (done.request, done.text) == (
        None,
        "#0-3 saved.\nNothing left to summarize.",
    )
    assert memo.nap("0-1", "again").text == "Nothing left to summarize."


def test_nap_keeps_a_summary_that_is_already_saved(memo):
    for index in range(4):
        memo.note(f"m{index}")
    memo.nap("0-1", "pair zero")
    kept = memo.nap("0-1", "other text")
    assert kept.saved is None
    assert kept.text.startswith("#0-1 is already saved.")
    assert memo.zoom("0-3").halves[0] == "#0-1 pair zero"


def test_nap_needs_block_and_summary_together(memo):
    with pytest.raises(ValueError, match="both block and summary"):
        memo.nap("0-1")


def test_large_blocks_are_summarized_from_their_two_halves(memo):
    fill(memo, 31)
    memo.note("memory 31")
    blocks = []
    request = memo.nap().request
    while request.block != "0-31":
        blocks.append(request.block)
        request = memo.nap(request.block, f"summary {request.block}").request
    assert blocks == ["30-31", "28-31", "24-31", "16-31"]
    assert request.sources == ("#0-15 summary 0-15", "#16-31 summary 16-31")


@pytest.mark.parametrize("block", ["3", "1-1", "1-2", "0-2", "4-11", "a-b"])
def test_only_aligned_power_of_two_ranges_are_blocks(block):
    with pytest.raises(ValueError, match="not a block"):
        parse_block(block)


def test_wake_shows_recent_memories_in_full_and_old_ones_as_summaries(memo):
    memo.config({"WAKE_LINES": 9})
    fill(memo, 37)
    woken = memo.wake()
    assert woken.is_awake and woken.parts == 1
    assert woken.lines[0] == "#0-15 summary 0-15"
    assert woken.lines[-1].endswith(" memory 36")
    assert len(woken.lines) == 9
    assert woken.text.endswith("You are awake.")


def test_wake_waits_for_a_summary_that_the_read_needs(memo):
    memo.config({"WAKE_LINES": 2})
    for index in range(4):
        memo.note(f"m{index}")
    blocked = memo.wake()
    assert (blocked.lines, blocked.parts, blocked.is_awake) == ((), 0, False)
    assert blocked.request.block == "0-1"
    assert "Write the 3 summaries that are due" in blocked.text
    request = blocked.request
    while request is not None:
        request = memo.nap(request.block, f"summary {request.block}").request
    # Two halves fit the budget of two lines, so the read splits #0-3.
    assert memo.wake().lines == ("#0-1 summary 0-1", "#2-3 summary 2-3")


def test_a_large_memory_comes_in_parts_that_fit_one_print(memo):
    memo.config({"WAKE_LINES": 200})
    fill(memo, 200, text="memory {} " + "detail " * 30)
    first = memo.wake()
    assert first.parts == 6 and not first.is_awake
    assert first.text.startswith(
        "Your memory, part 1 of 6, oldest first (200 memories)."
    )
    assert first.text.endswith("Not awake yet. Next: await memo.wake(part=2, at=200)")
    memo.note("arrives during the read")
    parts = [first] + [memo.wake(part=number, at=200) for number in range(2, 7)]
    lines = [line for part in parts for line in part.lines]
    assert len(lines) == 200 and lines[-1].startswith("#199 ")
    assert all(
        len(part.text.encode()) < memo_module.VIS_PART_BYTES + 200 for part in parts
    )
    assert parts[-1].is_awake and "You are awake." in parts[-1].text


def test_wake_refuses_a_part_or_count_that_does_not_exist(memo):
    memo.note("only")
    with pytest.raises(ValueError, match="No part 2"):
        memo.wake(part=2)
    with pytest.raises(ValueError, match="at=5, but the memory holds 1 memory"):
        memo.wake(at=5)
    with pytest.raises(ValueError, match="part starts at 1"):
        memo.wake(part=0)


def test_recall_searches_raw_memories_without_case(memo):
    memo.note("Deploys go through GitHub Actions.")
    memo.note("The user lives in Warsaw.")
    found = memo.recall("github|WARSAW")
    assert found.total == 2 and len(found.matches) == 2
    assert found.text.endswith("2 matches.")
    nothing = memo.recall("berlin")
    assert (nothing.total, nothing.matches, nothing.text) == (0, (), "No match.")
    with pytest.raises(ValueError, match="Bad regular expression"):
        memo.recall("(")


def test_recall_keeps_the_newest_matches_that_fit_one_print(memo):
    FileStore(memo._path()).append(
        [("2026-01-02", f"match {index} " + "x" * 200) for index in range(100)]
    )
    found = memo.recall("match")
    assert found.total == 100 and len(found.matches) < 100
    assert found.matches[-1].startswith("#99 ")
    assert found.text.endswith(
        f"Newest {len(found.matches)} of 100 matches. Use a narrower pattern."
    )


def test_zoom_opens_a_block_into_its_halves(memo):
    for index in range(5):
        memo.note(f"m{index}")
    memo.nap("0-1", "pair zero")
    opened = memo.zoom("0-3")
    assert opened.halves == ("#0-1 pair zero", "#2-3 (no summary yet)")
    assert memo.zoom("4-7").halves == ("#4-5 (no summary yet)",)
    (newest,) = memo.zoom("4-5").halves
    assert newest.endswith(" m4")
    with pytest.raises(ValueError, match="after the newest memory"):
        memo.zoom("8-15")


def test_forget_drops_a_summary_and_nap_asks_for_it_again(memo):
    fill(memo, 4)
    dropped = memo.forget("2-3")
    assert dropped.blocks == ("2-3", "0-3")
    assert dropped.text.endswith("Call memo.nap() to write them again.")
    assert memo.nap().request.block == "2-3"
    with pytest.raises(ValueError, match="No summary at 2-3"):
        memo.forget("2-3")


def test_a_damaged_summary_tells_how_to_repair_it(memo):
    memo.config({"WAKE_LINES": 1})
    fill(memo, 2)
    FileStore(memo._path()).level_path(2).write_bytes(b" " * (TREE_RECORD - 1) + b"\n")
    with pytest.raises(RuntimeError, match=r'memo.forget\("0-1"\)'):
        memo.wake()
    memo.forget("0-1")
    memo.nap("0-1", "rebuilt")
    assert memo.wake().lines == ("#0-1 rebuilt",)


def test_config_shows_changes_and_restores_sizes(memo):
    shown = memo.config()
    assert [size.name for size in shown.sizes] == [
        "WAKE_LINES",
        "ENTRY_CHARS",
        "PART_CHARS",
        "PART_LINES",
    ]
    assert shown.changed == ()
    changed = memo.config({"wake_lines": 300, "ENTRY_CHARS": "200"})
    assert changed.changed == ("WAKE_LINES", "ENTRY_CHARS")
    assert changed.sizes[0].value == 300 and "(default 96)" in changed.text
    with pytest.raises(ValueError, match="limit is 200"):
        memo.note("x" * 201)
    restored = memo.config({"WAKE_LINES": None})
    assert restored.sizes[0].value == 96
    with pytest.raises(ValueError, match="SPEED is not a size"):
        memo.config({"SPEED": 2})
    with pytest.raises(ValueError, match="positive whole number"):
        memo.config({"PART_LINES": 0})
    assert memo.config().sizes[3].value == 500


def test_import_adds_dated_memories_in_order(memo, tmp_path):
    source = tmp_path / "old.txt"
    source.write_text(
        "2024-01-01 first old memory\n\n2024-02-01 second old memory\n2024-02-01 third\n"
    )
    imported = memo.import_memories(str(source))
    assert (imported.first, imported.last, imported.due) == (0, 2, 1)
    assert imported.text == (
        "Imported 3 memories, #0 to #2.\n1 summary is due. Call memo.nap() to write it."
    )
    assert memo.recall("old").matches[0] == "#0 2024-01-01 first old memory"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("2024-01-01 fine\n2023-12-31 earlier\n", "Line 2: 2023-12-31 is before"),
        ("2024-02-30 no such day\n", "Line 1: 2024-02-30 is not a real date"),
        ("yesterday something\n", "Line 1: expected"),
        ("2024-01-01 " + "x" * 281 + "\n", "Line 1: 281 bytes"),
        ("\n\n", "has no memories"),
    ],
)
def test_import_adds_nothing_when_one_line_is_wrong(memo, tmp_path, content, message):
    source = tmp_path / "old.txt"
    source.write_text(content)
    with pytest.raises(ValueError, match=message):
        memo.import_memories(str(source))
    assert memo.wake().at == 0


def test_import_needs_utf8_text(memo, tmp_path):
    source = tmp_path / "old.txt"
    source.write_bytes(b"2024-01-01 caf\xe9\n")
    with pytest.raises(ValueError, match="not UTF-8"):
        memo.import_memories(str(source))


def test_status_reports_counts_and_problems(folder):
    assert status(folder)["store"] == "missing"
    tool = Memo(folder)
    tool.init()
    tool.note("a")
    tool.note("b")
    assert status(folder) == {"memories": 2, "summaries_due": 1, "max_bytes": 280}
    (folder / "config").write_text("SPEED = 1\n")
    assert "SPEED is not a size" in status(folder)["config_error"]


def test_status_reports_an_unreadable_folder_and_does_not_raise(folder, monkeypatch):
    Memo(folder).init()

    def refuse(store):
        raise PermissionError(13, "Permission denied", str(store.log_path))

    monkeypatch.setattr(FileStore, "count", refuse)
    facts = status(folder)
    assert facts["store"] == "unreadable"
    assert "Permission denied" in facts["error"]
