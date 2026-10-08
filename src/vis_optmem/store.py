"""Where a memory lives: the ``MemoryStore`` interface and the OptMem files.

``MemoryStore`` is the interface that ``Memo`` uses for memories, summaries and sizes.
Implement it to keep a memory in another place, for example a database. ``FileStore``
implements it with the OptMem files, so the ``memo`` command-line tool and this package
can share one memory.

``FileStore`` gives each record a fixed width, so the position of a record is its
identity and each lookup is one seek:

- memory ``i`` is the record at byte ``i * LOG_RECORD`` of ``LOG.txt``;
- the summary of block ``[lo, hi)`` is the record at byte
  ``(lo // (hi - lo)) * TREE_RECORD`` of ``TREE/<hi - lo>``.

Its writers hold an exclusive lock on ``.lock``: the same lock that the ``memo`` tool takes.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows has no fcntl.
    fcntl = None

LOG_RECORD = 320
TREE_RECORD = 288
DEFAULT_DIRECTORY = "~/.optmem/memory"
LOCK_TIMEOUT_S = 30.0
STEM_LETTERS = 6
"""Words that share their first letters match in the default search: migrate, migrations."""

DUPLICATE_SIMILARITY = 0.6
"""Share of words that two memories have in common when one repeats the other."""

DUPLICATE_CANDIDATES = 5
"""Closest memories that the default ``duplicate`` compares with a new note."""


@dataclass(frozen=True, slots=True)
class Size:
    """One size that a memory can set in its config file."""

    name: str
    default: int
    meaning: str


SIZES = (
    Size("WAKE_LINES", 96, "lines that wake shows: the reading budget"),
    Size("ENTRY_CHARS", 280, "longest memory or summary, in bytes"),
    Size("PART_CHARS", 20000, "largest part of one output, in bytes"),
    Size("PART_LINES", 500, "largest part of one output, in lines"),
)
SIZE_NAMES = tuple(size.name for size in SIZES)
# A memory must fit one log record after its id and date. A summary must fit one tree record.
MAX_ENTRY_CHARS = min(TREE_RECORD - 8, LOG_RECORD - 40)


class DamagedSummary(RuntimeError):
    """A summary record is blank or is not UTF-8 text."""

    def __init__(self, lo: int, hi: int) -> None:
        block = f"{lo}-{hi - 1}"
        super().__init__(
            f"The summary of #{block} is damaged. "
            f'Call memo.forget("{block}"), then memo.nap() to write it again.'
        )


@dataclass(frozen=True, slots=True)
class Entry:
    """One memory as the log keeps it."""

    id: int
    date: str
    text: str

    @property
    def line(self) -> str:
        return f"#{self.id} {self.date} {self.text}"


def stems(text: str) -> set[str]:
    """The words of ``text`` for the default search: lowercase, first letters only."""
    return {
        word[:STEM_LETTERS]
        for word in re.findall(r"\w+", text.lower())
        if len(word) >= 3
    }


def _words(text: str) -> set[str]:
    """Every word of ``text``, lowercase. Words without digits keep their first letters."""
    return {
        word if any(c.isdigit() for c in word) else word[:STEM_LETTERS]
        for word in re.findall(r"\w+", text.lower())
    }


def pretty(path: Path) -> str:
    """Show a path as a person types it, with the home folder as ``~``."""
    home = Path.home()
    if path == home:
        return "~"
    if path.is_relative_to(home):
        return "~/" + path.relative_to(home).as_posix()
    return str(path)


def parse_size(name: str, value: object, where: str = "") -> int:
    """Check one size value. ``where`` names the config line that holds it."""
    text = str(value).strip()
    prefix = f"{where}: " if where else ""
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
        raise ValueError(
            f"{prefix}{name} must be a positive whole number, not {text!r}."
        )
    number = int(text)
    if name == "ENTRY_CHARS" and number > MAX_ENTRY_CHARS:
        raise ValueError(
            f"{prefix}ENTRY_CHARS is at most {MAX_ENTRY_CHARS}: "
            "each memory must fit one fixed-width record."
        )
    return number


def _record(text: str, width: int) -> bytes:
    data = text.encode("utf-8")
    if len(data) > width - 1:
        raise ValueError(f"Too long: {len(data)} bytes. One record holds {width - 1}.")
    return data + b" " * (width - 1 - len(data)) + b"\n"


def _entry(record: bytes) -> Entry:
    head, _, rest = record.decode("utf-8").rstrip().partition(" ")
    date, _, text = rest.partition(" ")
    return Entry(int(head[1:]), date, text)


def _entries(data: bytes) -> list[Entry]:
    usable = len(data) - len(data) % LOG_RECORD
    return [_entry(data[at : at + LOG_RECORD]) for at in range(0, usable, LOG_RECORD)]


def _count(path: Path, width: int) -> int:
    try:
        return path.stat().st_size // width
    except FileNotFoundError:
        return 0


def _drop_partial(path: Path, width: int) -> None:
    """Remove a partial last record that a crash left. Nobody saw it complete."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size % width:
        with open(path, "r+b") as handle:
            handle.truncate(size - size % width)


def _append(path: Path, data: bytes) -> None:
    with open(path, "ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _lock_windows(handle) -> None:
    import msvcrt

    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def _unlock_windows(handle) -> None:
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class MemoryStore(ABC):
    """The place where one memory lives. ``Memo`` reads and writes only through it.

    A memory has three parts:

    - The log: memories with dense ids from 0. A memory never changes.
    - The summaries: one level for each block size 2, 4, 8 and so on. Level ``size`` holds
      the summaries of blocks ``[k * size, (k + 1) * size)`` for ``k`` from 0, with no gaps.
    - The sizes: the overrides of ``SIZES`` that ``memo.config()`` sets.

    Many sessions can use one memory at the same time. So ``append``, ``put_summary``,
    ``drop_summaries`` and ``write_overrides`` must each be atomic in the store: use a
    transaction, a conditional write or a lock. The other methods only read.
    """

    @property
    @abstractmethod
    def location(self) -> str:
        """Where the memory is, for people: a folder, a table name or a URL."""

    @abstractmethod
    def exists(self) -> bool:
        """True when the memory exists. ``Memo`` creates a memory only in ``init()``."""

    @abstractmethod
    def create(self) -> bool:
        """Create the memory, or complete it. Return True for a new memory."""

    def prepare(self) -> None:
        """Complete an existing memory before each use. The default does nothing."""

    # Sizes

    @abstractmethod
    def overrides(self) -> dict[str, int]:
        """The sizes that this memory sets. Check each value with ``parse_size``."""

    @abstractmethod
    def write_overrides(self, overrides: Mapping[str, int]) -> None:
        """Replace the sizes that this memory sets."""

    def sizes(self) -> dict[str, int]:
        """Every size: the override, else the default."""
        return {size.name: size.default for size in SIZES} | self.overrides()

    # Memories

    @abstractmethod
    def count(self) -> int:
        """The number of memories."""

    @abstractmethod
    def entries(self, lo: int, hi: int) -> list[Entry]:
        """Memories ``lo`` to ``hi - 1``, oldest first."""

    @abstractmethod
    def append(self, items: Sequence[tuple[str, str]]) -> int:
        """Add ``(date, text)`` memories after the newest one. Return the id of the first one.

        Give the ids in the same atomic step as the write, so two sessions never get the
        same id. Add all items or none.
        """

    def entry(self, index: int) -> Entry:
        return self.entries(index, index + 1)[0]

    def scan(self) -> Iterator[Entry]:
        """Every memory, oldest first. The default reads 4096 memories at a time."""
        total = self.count()
        for lo in range(0, total, 4096):
            yield from self.entries(lo, min(lo + 4096, total))

    def search(self, query: str, limit: int) -> list[Entry]:
        """Memories related to ``query``, best first: at most ``limit`` of them.

        Override it to search by meaning, for example with embeddings in your
        service. The default ranks memories by the words that they share with
        ``query``, and rare words count more. Words of 3 or more letters match
        when their first 6 letters are the same. It reads every memory once.

        The default ``duplicate`` also uses it, so return the closest memories first.
        """
        wanted = stems(query)
        if not wanted or limit < 1:
            return []
        found = dict.fromkeys(wanted, 0)
        candidates = []
        total = 0
        for entry in self.scan():
            total += 1
            shared = wanted & stems(entry.text)
            if shared:
                for stem in shared:
                    found[stem] += 1
                candidates.append((entry, shared))
        weight = {
            stem: math.log(1 + total / count) for stem, count in found.items() if count
        }
        candidates.sort(
            key=lambda item: (-sum(weight[s] for s in item[1]), -item[0].id)
        )
        return [entry for entry, _ in candidates[:limit]]

    def duplicate(self, text: str) -> Entry | None:
        """The memory that already says ``text``, or None. ``memo.note`` refuses a repeat.

        Override it to decide repeats in your service, for example when two embeddings
        are very close. ``force=True`` in ``memo.note`` skips this check. The default
        compares ``text`` with the DUPLICATE_CANDIDATES closest memories from ``search``.
        A memory repeats ``text`` when it has the same numbers and shares at least
        DUPLICATE_SIMILARITY of the words of both. So "port 5432" and "port 6543" are
        different facts.
        """
        words = _words(text)
        numbers = {word for word in words if any(c.isdigit() for c in word)}
        for entry in self.search(text, DUPLICATE_CANDIDATES):
            other = _words(entry.text)
            if {word for word in other if any(c.isdigit() for c in word)} != numbers:
                continue
            if len(words & other) / len(words | other) >= DUPLICATE_SIMILARITY:
                return entry
        return None

    # Summaries

    @abstractmethod
    def level_count(self, size: int) -> int:
        """The number of summaries of blocks of ``size`` memories."""

    @abstractmethod
    def summary(self, lo: int, hi: int) -> str | None:
        """The summary of block ``[lo, hi)``, or None when it is not written."""

    @abstractmethod
    def put_summary(self, lo: int, hi: int, text: str) -> bool:
        """Save the summary of ``[lo, hi)`` only if it is the next summary of its level.

        The next summary of level ``size`` has ``lo == level_count(size) * size``. Check
        and write in one atomic step. Return False and write nothing when the check fails:
        another session changed that level first.
        """

    @abstractmethod
    def drop_summaries(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """Remove the summary of ``[lo, hi)`` and every summary that depends on it.

        For each level ``size = hi - lo, 2 * (hi - lo), ...`` up to ``count()``, keep the
        first ``lo // size`` summaries and remove the rest. Return the removed blocks as
        ``(lo, hi)`` pairs, smallest level first. Do it in one atomic step.
        """

    def due(self, total: int, limit: int | None = None) -> list[tuple[int, int]]:
        """Complete blocks of the first ``total`` memories without a summary, smallest first."""
        blocks = []
        size = 2
        while size <= total:
            for k in range(self.level_count(size), total // size):
                blocks.append((k * size, (k + 1) * size))
                if limit is not None and len(blocks) >= limit:
                    return blocks
            size *= 2
        return blocks

    def due_count(self, total: int) -> int:
        """How many blocks ``due`` lists, without a list.

        A level can hold more summaries than ``total`` needs when other sessions
        add memories, so each level counts as zero or more.
        """
        count, size = 0, 2
        while size <= total:
            count += max(0, total // size - self.level_count(size))
            size *= 2
        return count


class FileStore(MemoryStore):
    """One memory folder in the OptMem format."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def log_path(self) -> Path:
        return self.directory / "LOG.txt"

    @property
    def config_path(self) -> Path:
        return self.directory / "config"

    def level_path(self, size: int) -> Path:
        return self.directory / "TREE" / str(size)

    @property
    def location(self) -> str:
        return pretty(self.directory)

    def exists(self) -> bool:
        return self.directory.is_dir()

    def prepare(self) -> None:
        """Add the log and the summary folder to an existing memory folder when missing."""
        (self.directory / "TREE").mkdir(exist_ok=True)
        open(self.log_path, "a").close()

    def create(self) -> bool:
        """Create the memory folder, or complete it. Return True for a new folder."""
        is_new = not self.exists()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prepare()
        if not self.config_path.exists():
            self._write_config({})
        return is_new

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the write lock of this memory."""
        with open(self.directory / ".lock", "a") as handle:
            if fcntl is None:
                _lock_windows(handle)
                try:
                    yield
                finally:
                    _unlock_windows(handle)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # Sizes

    def overrides(self) -> dict[str, int]:
        """The sizes that this memory sets in its config file."""
        try:
            lines = self.config_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return {}
        found = {}
        for number, raw in enumerate(lines, 1):
            line = raw.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            name = name.upper()
            where = f"{pretty(self.config_path)} line {number}"
            if name not in SIZE_NAMES:
                raise ValueError(
                    f"{where}: {name} is not a size. "
                    f"Remove the line, or use one of {', '.join(SIZE_NAMES)}."
                )
            found[name] = parse_size(name, value, where)
        return found

    def write_overrides(self, overrides: Mapping[str, int]) -> None:
        with self.locked():
            self._write_config(overrides)

    def _write_config(self, overrides: Mapping[str, int]) -> None:
        """Write the config file. A size without an override stays a comment."""
        lines = [
            "# Sizes of this memory. A line that starts with # uses the default.",
            "# Change them with memo.config() in Vis, or with `memo config NAME=VALUE`.",
            "",
        ]
        for size in SIZES:
            value = overrides.get(size.name, size.default)
            prefix = "" if size.name in overrides else "# "
            lines.append(f"{prefix}{size.name} = {value}  # {size.meaning}")
        temporary = self.config_path.with_name("config.tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, self.config_path)

    # Memories

    def count(self) -> int:
        """The number of memories in the log."""
        return _count(self.log_path, LOG_RECORD)

    def entries(self, lo: int, hi: int) -> list[Entry]:
        """Memories ``lo`` to ``hi - 1``, in one read."""
        with open(self.log_path, "rb") as handle:
            handle.seek(lo * LOG_RECORD)
            return _entries(handle.read((hi - lo) * LOG_RECORD))

    def scan(self) -> Iterator[Entry]:
        """Every memory, oldest first, without holding the whole log in memory."""
        with open(self.log_path, "rb") as handle:
            while chunk := handle.read(LOG_RECORD * 4096):
                yield from _entries(chunk)

    def append(self, items: Sequence[tuple[str, str]]) -> int:
        """Append ``(date, text)`` memories. Return the id of the first one.

        Ids are given inside the lock, so two sessions never get the same id.
        Every record is encoded first: a record that does not fit writes nothing.
        """
        with self.locked():
            _drop_partial(self.log_path, LOG_RECORD)
            first = self.count()
            data = b"".join(
                _record(f"#{first + offset} {date} {text}", LOG_RECORD)
                for offset, (date, text) in enumerate(items)
            )
            _append(self.log_path, data)
            return first

    # Summaries

    def level_count(self, size: int) -> int:
        """The number of summaries of blocks of ``size`` memories."""
        return _count(self.level_path(size), TREE_RECORD)

    def summary(self, lo: int, hi: int) -> str | None:
        """The summary of block ``[lo, hi)``, or None when it is not written."""
        size = hi - lo
        try:
            with open(self.level_path(size), "rb") as handle:
                handle.seek((lo // size) * TREE_RECORD)
                record = handle.read(TREE_RECORD)
        except FileNotFoundError:
            return None
        try:
            return record.decode("utf-8").rstrip() or None
        except UnicodeDecodeError:
            raise DamagedSummary(lo, hi) from None

    def put_summary(self, lo: int, hi: int, text: str) -> bool:
        """Append the summary of ``[lo, hi)`` if it is the next record of its level.

        Return False when another writer changed that level first.
        """
        size = hi - lo
        path = self.level_path(size)
        record = _record(text, TREE_RECORD)
        with self.locked():
            _drop_partial(path, TREE_RECORD)
            if self.level_count(size) != lo // size:
                return False
            _append(path, record)
            return True

    def drop_summaries(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """Remove the summary of ``[lo, hi)`` and every summary that depends on it.

        Later summaries of each level go too, because each level is a dense prefix.
        The log does not change, so the next naps write them again.
        """
        dropped = []
        size = hi - lo
        with self.locked():
            total = self.count()
            while size <= total:
                keep, have = lo // size, self.level_count(size)
                if have > keep:
                    dropped += [(k * size, (k + 1) * size) for k in range(keep, have)]
                    with open(self.level_path(size), "r+b") as handle:
                        handle.truncate(keep * TREE_RECORD)
                size *= 2
        return dropped
