"""A memory that many writers on many hosts share: a reference ``MemoryStore``.

``SharedStore`` keeps one memory in a ``Table``: a key-value table with sorted string
keys and atomic conditional transactions, like DynamoDB. Copy it, or subclass it, to
keep a team memory in your database. ``SqliteTable`` keeps the table in one SQLite file
that many processes share. ``examples/dynamodb_store.py`` keeps it in DynamoDB.

The keys of one memory:

- ``meta``: the memory exists. It holds a random ``id``.
- ``config``: the size overrides.
- ``n#<id>``: one memory, with ``date``, ``text`` and an optional ``vector``.
- ``l#<size>``: the number of summaries of level ``size``, in ``count``. No item means 0.
- ``s#<size>#<k>``: the summary of block ``[k * size, (k + 1) * size)``, in ``text``.

Each number has 10 digits, so the key order is the number order.

Each instance keeps every memory in a cache. A memory never changes, so ``sync`` reads
only the memories after the cache, in one query. A write gives the next ids after the
cache, with the condition that those keys are absent. When another writer used them
first, the write syncs and tries again with new ids. So the ids stay dense, and a
write is never lost or saved twice.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import math
import os
import random
import sqlite3
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from vis_optmem.names import same_names
from vis_optmem.store import SIZE_NAMES, Entry, MemoryStore, parse_size

log = logging.getLogger(__name__)

Item = dict[str, object]
"""One item of a table. Its values are strings and whole numbers."""

Embed = Callable[[list[str]], list[list[float]]]
"""A function that gives one vector for each text."""

DEADLINE_S = 20.0
"""How long a write tries again after conflicts. Then it raises ``WriteConflict``."""

BACKOFF_S = (0.005, 0.25)
"""The first and the largest wait between two tries. Each wait is random below its limit."""

DUPLICATE_MIN = 0.85
"""The cosine from which two memories with the same names say one fact."""

EMBED_BATCH = 64
"""Texts that one call of ``embed`` gets."""

SNAPSHOT_VERSION = 1
LAST_KEY = "\uffff"


@dataclass(frozen=True, slots=True)
class Absent:
    """A condition: no item has ``key``."""

    key: str


@dataclass(frozen=True, slots=True)
class Equals:
    """A condition: the item ``key`` exists and its ``field`` is ``value``."""

    key: str
    field: str
    value: object


Condition = Absent | Equals


class WriteConflict(TimeoutError):
    """Other writers changed the memory first at each try, until the deadline."""


class Table(ABC):
    """A table with sorted string keys, like one DynamoDB partition.

    Implement it to keep a ``SharedStore`` in another database. Each read must see
    every transaction that completed before it: use consistent reads.
    """

    location = "table"
    """Where the table is, for people."""

    max_writes: int | None = None
    """The most puts and deletes that one transaction takes, or None for no limit."""

    @abstractmethod
    def get(self, key: str) -> Item | None:
        """The item ``key``, or None."""

    @abstractmethod
    def query(
        self, prefix: str, start: str = "", limit: int | None = None
    ) -> list[tuple[str, Item]]:
        """The ``(key, item)`` pairs whose key starts with ``prefix``, in key order.

        Start at the first key that is not smaller than ``start``. Give at most
        ``limit`` pairs.
        """

    @abstractmethod
    def transact(
        self,
        puts: Mapping[str, Item] | None = None,
        deletes: Iterable[str] = (),
        conditions: Iterable[Condition] = (),
    ) -> bool:
        """Write ``puts`` and remove ``deletes`` in one atomic step, if every condition is true.

        A put replaces the whole item. Return False and change nothing when a condition
        is false, or when a concurrent transaction stops this one.
        """


def _true(condition: Condition, item: Item | None) -> bool:
    if isinstance(condition, Absent):
        return item is None
    return item is not None and item.get(condition.field) == condition.value


class MemoryTable(Table):
    """A ``Table`` in the memory of one process: for tests and as the smallest example."""

    location = "memory-table"

    def __init__(self, max_writes: int | None = None) -> None:
        self.items: dict[str, Item] = {}
        self.max_writes = max_writes
        self._lock = threading.Lock()

    def get(self, key: str) -> Item | None:
        with self._lock:
            item = self.items.get(key)
            return None if item is None else dict(item)

    def query(
        self, prefix: str, start: str = "", limit: int | None = None
    ) -> list[tuple[str, Item]]:
        with self._lock:
            keys = sorted(
                key
                for key in self.items
                if key.startswith(prefix) and key >= max(start, prefix)
            )
            return [(key, dict(self.items[key])) for key in keys[:limit]]

    def transact(self, puts=None, deletes=(), conditions=()) -> bool:
        puts, deletes = dict(puts or {}), list(deletes)
        if self.max_writes is not None and len(puts) + len(deletes) > self.max_writes:
            raise ValueError(f"One transaction takes at most {self.max_writes} writes.")
        with self._lock:
            if not all(_true(c, self.items.get(c.key)) for c in conditions):
                return False
            for key in deletes:
                self.items.pop(key, None)
            self.items.update({key: dict(item) for key, item in puts.items()})
            return True


class SqliteTable(Table):
    """A ``Table`` in one SQLite file that many processes share.

    Each call opens its own connection, so threads and processes can share one
    instance. A transaction starts with ``BEGIN IMMEDIATE``: it takes the write lock
    first, and other writers wait up to ``busy_timeout_s`` for it.
    """

    def __init__(self, path: str | os.PathLike, busy_timeout_s: float = 30.0) -> None:
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_s = busy_timeout_s
        self.location = f"sqlite:{self.path}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("pragma journal_mode=wal")
            connection.execute(
                "create table if not exists items (key text primary key, item text)"
            )

    def _connect(self) -> contextlib.closing[sqlite3.Connection]:
        """A new connection that closes at the end of its ``with`` block."""
        return contextlib.closing(
            sqlite3.connect(
                self.path, timeout=self.busy_timeout_s, isolation_level=None
            )
        )

    def get(self, key: str) -> Item | None:
        with self._connect() as connection:
            row = connection.execute(
                "select item from items where key = ?", (key,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def query(
        self, prefix: str, start: str = "", limit: int | None = None
    ) -> list[tuple[str, Item]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select key, item from items where key >= ? and key < ?"
                " order by key limit ?",
                (max(start, prefix), prefix + LAST_KEY, -1 if limit is None else limit),
            ).fetchall()
        return [(key, json.loads(item)) for key, item in rows]

    def transact(self, puts=None, deletes=(), conditions=()) -> bool:
        puts, deletes = dict(puts or {}), list(deletes)
        with self._connect() as connection:
            connection.execute("begin immediate")
            try:
                for condition in conditions:
                    row = connection.execute(
                        "select item from items where key = ?", (condition.key,)
                    ).fetchone()
                    if not _true(
                        condition, None if row is None else json.loads(row[0])
                    ):
                        connection.execute("rollback")
                        return False
                connection.executemany(
                    "delete from items where key = ?", [(key,) for key in deletes]
                )
                connection.executemany(
                    "insert or replace into items values (?, ?)",
                    [(key, _json(item)) for key, item in puts.items()],
                )
                connection.execute("commit")
                return True
            except BaseException:
                if connection.in_transaction:
                    connection.execute("rollback")
                raise


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def memory_key(index: int) -> str:
    return f"n#{index:010d}"


def level_key(size: int) -> str:
    return f"l#{size:010d}"


def summary_key(size: int, k: int) -> str:
    return f"s#{size:010d}#{k:010d}"


def _normalized(vector: Sequence[float]) -> array:
    values = array("f", vector)
    length = math.sqrt(math.sumprod(values, values))
    return array("f", (v / length for v in values)) if length else values


def pack_vector(vector: array) -> str:
    """A vector as text: little-endian 32-bit floats in base64."""
    data = array("f", vector)
    if sys.byteorder == "big":
        data.byteswap()
    return base64.b64encode(data.tobytes()).decode("ascii")


def unpack_vector(text: str) -> array:
    data = array("f", base64.b64decode(text))
    if sys.byteorder == "big":
        data.byteswap()
    return data


class SharedStore(MemoryStore):
    """One memory in a ``Table`` that many writers on many hosts share.

    ``embed`` gives the vectors for a search by meaning. Without it, the store uses
    the word search and the repeat check of ``MemoryStore``. With it, ``search`` ranks
    memories by the cosine to the query, from ``search_min``, and newer first on equal
    scores. A memory repeats a note when the cosine is at least ``duplicate_min`` and
    both have the same names. Each memory keeps its vector, so other instances do not
    embed it again.

    ``snapshot`` is a file from ``save_snapshot``. A new instance reads the memories
    from it and then syncs only the newer ones.
    """

    def __init__(
        self,
        table: Table,
        *,
        embed: Embed | None = None,
        search_min: float = 0.0,
        duplicate_min: float = DUPLICATE_MIN,
        snapshot: str | os.PathLike | None = None,
        deadline_s: float = DEADLINE_S,
    ) -> None:
        self.table = table
        self.embed = embed
        self.search_min = search_min
        self.duplicate_min = duplicate_min
        self.deadline_s = deadline_s
        self.snapshot = None if snapshot is None else Path(snapshot).expanduser()
        self._entries: list[Entry] = []
        self._vectors: list[array | None] = []
        self._snapshot_id: str | None = None
        self._embedded = 0
        """The cached memories before this index have a vector."""
        self._lock = threading.RLock()
        if self.snapshot is not None and self.snapshot.exists():
            self._load_snapshot(self.snapshot)

    @property
    def location(self) -> str:
        return self.table.location

    def exists(self) -> bool:
        return self.table.get("meta") is not None

    def create(self) -> bool:
        """Write the ``meta`` item if it is missing. Return True for a new memory."""
        return self.table.transact(
            {"meta": {"id": uuid.uuid4().hex}}, conditions=[Absent("meta")]
        )

    # Sizes

    def overrides(self) -> dict[str, int]:
        item = self.table.get("config") or {}
        where = f"{self.location} config"
        return {
            name: parse_size(name, value, where)
            for name, value in item.items()
            if name in SIZE_NAMES
        }

    def write_overrides(self, overrides: Mapping[str, int]) -> None:
        self.table.transact({"config": dict(overrides)})

    # Memories

    def sync(self) -> int:
        """Read the memories that other writers added, in one query. Return the count."""
        with self._lock:
            self._check_snapshot()
            start = len(self._entries)
            for key, item in self.table.query("n#", start=memory_key(start)):
                index = int(key[2:])
                if index != len(self._entries):
                    break  # Ids are dense, so this does not occur.
                self._entries.append(Entry(index, str(item["date"]), str(item["text"])))
                vector = item.get("vector")
                self._vectors.append(None if vector is None else unpack_vector(vector))
            self._add_vectors()
            return len(self._entries)

    def _add_vectors(self) -> None:
        """Embed the cached memories that have no vector. Hold the lock."""
        if self.embed is None:
            return
        end = len(self._vectors)
        missing = [i for i in range(self._embedded, end) if self._vectors[i] is None]
        for at in range(0, len(missing), EMBED_BATCH):
            batch = missing[at : at + EMBED_BATCH]
            vectors = self.embed([self._entries[i].text for i in batch])
            for index, vector in zip(batch, vectors, strict=True):
                self._vectors[index] = _normalized(vector)
        self._embedded = end

    def _embed(self, texts: list[str]) -> list[array | None]:
        if self.embed is None:
            return [None] * len(texts)
        vectors: list[array | None] = []
        for at in range(0, len(texts), EMBED_BATCH):
            batch = self.embed(texts[at : at + EMBED_BATCH])
            if len(batch) != len(texts[at : at + EMBED_BATCH]):
                raise ValueError("embed must give one vector for each text.")
            vectors += [_normalized(vector) for vector in batch]
        return vectors

    def count(self) -> int:
        return self.sync()

    def entries(self, lo: int, hi: int) -> list[Entry]:
        if hi > len(self._entries):
            self.sync()
        return self._entries[lo:hi]

    def scan(self) -> Iterator[Entry]:
        self.sync()
        return iter(self._entries[:])

    def append(self, items: Sequence[tuple[str, str]]) -> int:
        """Add the memories in one transaction. Try again with new ids after a conflict."""
        items = list(items)
        limit = self.table.max_writes
        if limit is not None and len(items) > limit:
            raise ValueError(
                f"{self.location} saves at most {limit} memories in one call. "
                "Split the import into smaller files."
            )
        vectors = self._embed([text for _, text in items])

        def attempt() -> int | None:
            first = self.sync()
            return first if self._write(first, items, vectors) else None

        return self._retry(attempt)

    def note(self, date: str, text: str, force: bool = False) -> tuple[Entry, bool]:
        """Check for a repeat and add the memory, as one atomic step.

        The write has the condition that no memory came after the checked ones. When
        another writer added one first, check again: it can be the same fact.
        """
        vector = self._embed([text])[0]

        def attempt() -> tuple[Entry, bool] | None:
            # A check that syncs more memories makes the write at ``first`` fail.
            first = self.sync()
            twin = None if force else self._twin(text, vector)
            if twin is not None:
                return twin, False
            if self._write(first, [(date, text)], [vector]):
                return Entry(first, date, text), True
            return None

        return self._retry(attempt)

    def _write(
        self,
        first: int,
        items: Sequence[tuple[str, str]],
        vectors: Sequence[array | None],
    ) -> bool:
        """Write the memories from id ``first`` if no memory has those ids."""
        puts = {}
        for offset, ((date, text), vector) in enumerate(zip(items, vectors)):
            item: Item = {"date": date, "text": text}
            if vector is not None:
                item["vector"] = pack_vector(vector)
            puts[memory_key(first + offset)] = item
        if not self.table.transact(puts, conditions=[Absent(key) for key in puts]):
            return False
        with self._lock:
            # The cache takes them only when it ends at ``first``.
            # Else the next sync reads them.
            if len(self._entries) == first:
                for offset, ((date, text), vector) in enumerate(zip(items, vectors)):
                    self._entries.append(Entry(first + offset, date, text))
                    self._vectors.append(vector)
        return True

    def _retry[T](self, attempt: Callable[[], T | None]) -> T:
        """Call ``attempt`` until it gives a value, with random waits, up to the deadline."""
        deadline = time.monotonic() + self.deadline_s
        low, high = BACKOFF_S
        tries = 0
        while True:
            result = attempt()
            if result is not None:
                return result
            tries += 1
            wait = random.uniform(0, min(high, low * 2**tries))
            if time.monotonic() + wait > deadline:
                raise WriteConflict(
                    f"{self.location}: other writers changed the memory first "
                    f"{tries} times in {self.deadline_s:g} s. Try again later."
                )
            time.sleep(wait)

    def search(self, query: str, limit: int) -> list[Entry]:
        """The closest memories by meaning with ``embed``, else by shared words."""
        if self.embed is None:
            return super().search(query, limit)
        if limit < 1 or not query.strip():
            return []
        self.sync()
        return [
            self._entries[index]
            for _, index in self._ranked(self._embed([query])[0], self.search_min)[
                :limit
            ]
        ]

    def _ranked(self, vector: array, minimum: float) -> list[tuple[float, int]]:
        """``(score, id)`` from ``minimum``, best first, newer first on equal scores."""
        with self._lock:
            scored = [
                (math.sumprod(vector, other), index)
                for index, other in enumerate(self._vectors)
                if other is not None
            ]
        return sorted(
            ((score, index) for score, index in scored if score >= minimum),
            key=lambda pair: (-pair[0], -pair[1]),
        )

    def duplicate(self, text: str) -> Entry | None:
        """The memory that already says ``text``, or None."""
        return self._twin(text, self._embed([text])[0])

    def _twin(self, text: str, vector: array | None) -> Entry | None:
        if vector is None:
            return super().duplicate(text)
        self.sync()
        for _, index in self._ranked(vector, self.duplicate_min):
            entry = self._entries[index]
            if same_names(text, entry.text):
                return entry
        return None

    # Summaries

    def level_count(self, size: int) -> int:
        item = self.table.get(level_key(size))
        return 0 if item is None else int(item["count"])

    def summary(self, lo: int, hi: int) -> str | None:
        """The summary of ``[lo, hi)``. A summary after the level count does not count."""
        size = hi - lo
        item = self.table.get(summary_key(size, lo // size))
        if item is None or lo // size >= self.level_count(size):
            return None
        return str(item["text"])

    def put_summary(self, lo: int, hi: int, text: str) -> bool:
        """Move the level count from ``k`` to ``k + 1`` and write the summary, in one step."""
        size = hi - lo
        k = lo // size
        return self.table.transact(
            {level_key(size): {"count": k + 1}, summary_key(size, k): {"text": text}},
            conditions=[_count_is(size, k)],
        )

    def drop_summaries(self, lo: int, hi: int) -> list[tuple[int, int]]:
        """Lower the level counts and remove their summaries, in one transaction.

        When the table takes fewer writes in one transaction, lower the counts first.
        Then remove the summaries of each level in parts, each part only while the
        count stays the same. ``summary`` ignores a summary after its level count, and
        ``put_summary`` writes over it, so a part that stays does no harm.
        """
        result: list[list[tuple[int, int]]] = []

        def attempt() -> bool | None:
            total = self.sync()
            counts = {
                int(key[2:]): int(item["count"]) for key, item in self.table.query("l#")
            }
            puts, counters, conditions, dropped = {}, [], [], []
            summaries: dict[int, list[str]] = {}
            size = hi - lo
            while size <= total:
                keep, have = lo // size, counts.get(size, 0)
                if have > keep:
                    dropped += [(k * size, (k + 1) * size) for k in range(keep, have)]
                    summaries[size] = [summary_key(size, k) for k in range(keep, have)]
                    if keep:
                        puts[level_key(size)] = {"count": keep}
                    else:
                        counters.append(level_key(size))
                    conditions.append(_count_is(size, have))
                size *= 2
            everything = [key for keys in summaries.values() for key in keys]
            limit = self.table.max_writes
            if limit is None or len(puts) + len(counters) + len(everything) <= limit:
                if not self.table.transact(puts, counters + everything, conditions):
                    return None
            else:
                if not self.table.transact(puts, counters, conditions):
                    return None
                for size, keys in summaries.items():
                    for at in range(0, len(keys), limit):
                        self.table.transact(
                            deletes=keys[at : at + limit],
                            conditions=[_count_is(size, lo // size)],
                        )
            result.append(dropped)
            return True

        self._retry(attempt)
        return result[0]

    # Snapshot

    def save_snapshot(self, path: str | os.PathLike | None = None) -> Path:
        """Write every memory to a JSON file, for a quick start of the next instance.

        The file replaces the old one in one rename, so a reader never sees half of it.
        """
        target = Path(path).expanduser() if path is not None else self.snapshot
        if target is None:
            raise ValueError("Give a path, or make the store with snapshot=...")
        self.sync()
        meta = self.table.get("meta") or {}
        with self._lock:
            rows = [
                [
                    entry.date,
                    entry.text,
                    None if vector is None else pack_vector(vector),
                ]
                for entry, vector in zip(self._entries, self._vectors)
            ]
        data = {"version": SNAPSHOT_VERSION, "id": meta.get("id"), "memories": rows}
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(_json(data), encoding="utf-8")
        os.replace(temporary, target)
        return target

    def _load_snapshot(self, path: Path) -> None:
        """Load a snapshot. Ignore a damaged one: the first sync reads the table."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != SNAPSHOT_VERSION or not data.get("id"):
                return
            entries, vectors = [], []
            for index, (date, text, vector) in enumerate(data["memories"]):
                entries.append(Entry(index, date, text))
                vectors.append(None if vector is None else unpack_vector(vector))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            log.warning("Ignored the damaged snapshot %s: %s", path, error)
            return
        self._snapshot_id = data["id"]
        self._entries.extend(entries)
        self._vectors.extend(vectors)

    def _check_snapshot(self) -> None:
        """Forget a snapshot of another memory before the first sync. Hold the lock."""
        if self._snapshot_id is None:
            return
        meta = self.table.get("meta")
        if meta is None or meta.get("id") != self._snapshot_id:
            self._entries, self._vectors, self._embedded = [], [], 0
        self._snapshot_id = None


def _count_is(size: int, k: int) -> Condition:
    """The condition that level ``size`` holds ``k`` summaries. No item means 0."""
    return Absent(level_key(size)) if k == 0 else Equals(level_key(size), "count", k)


def sqlite_store(path: str, snapshot: str | None = None) -> SharedStore:
    """A ``SharedStore`` in one SQLite file, for ``MEMORY_STORE=vis_optmem.shared:sqlite_store``."""
    return SharedStore(SqliteTable(path), snapshot=snapshot)
