"""A memory in one SQLite database: a template for a store in any SQL database.

Use it in Vis with:

    MEMORY_STORE=examples/sqlite_store.py:SqliteStore
    MEMORY_STORE_CONFIG={"path": "~/memory.db"}

For a database on a server, for example PostgreSQL on Amazon RDS, keep the three
tables and the transactions, and change the connection and the SQL placeholders.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence

from vis_optmem.store import Entry, MemoryStore, parse_size

SCHEMA = """
create table if not exists memories (id integer primary key, date text, text text);
create table if not exists summaries (
  size integer, k integer, text text, primary key (size, k));
create table if not exists sizes (name text primary key, value integer);
"""


class SqliteStore(MemoryStore):
    """One memory in a SQLite file. Each write is one immediate transaction."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(os.path.expanduser(path))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.executescript(SCHEMA)
        return connection

    @contextlib.contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One atomic write. The transaction takes the write lock at its start."""
        connection = self._connect()
        try:
            connection.execute("begin immediate")
            yield connection
            connection.execute("commit")
        except BaseException:
            if connection.in_transaction:
                connection.execute("rollback")
            raise
        finally:
            connection.close()

    @property
    def location(self) -> str:
        return f"sqlite:{self.path}"

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def create(self) -> bool:
        is_new = not self.exists()
        self._connect().close()
        return is_new

    def overrides(self) -> dict[str, int]:
        with self._read() as connection:
            rows = connection.execute("select name, value from sizes").fetchall()
        return {name: parse_size(name, value, self.location) for name, value in rows}

    def write_overrides(self, overrides: Mapping[str, int]) -> None:
        with self._write() as connection:
            connection.execute("delete from sizes")
            connection.executemany("insert into sizes values (?, ?)", overrides.items())

    def count(self) -> int:
        with self._read() as connection:
            return connection.execute("select count(*) from memories").fetchone()[0]

    def entries(self, lo: int, hi: int) -> list[Entry]:
        with self._read() as connection:
            rows = connection.execute(
                "select id, date, text from memories where id >= ? and id < ? order by id",
                (lo, hi),
            ).fetchall()
        return [Entry(*row) for row in rows]

    def append(self, items: Sequence[tuple[str, str]]) -> int:
        with self._write() as connection:
            first = connection.execute("select count(*) from memories").fetchone()[0]
            connection.executemany(
                "insert into memories values (?, ?, ?)",
                [
                    (first + offset, date, text)
                    for offset, (date, text) in enumerate(items)
                ],
            )
        return first

    def level_count(self, size: int) -> int:
        with self._read() as connection:
            return connection.execute(
                "select count(*) from summaries where size = ?", (size,)
            ).fetchone()[0]

    def summary(self, lo: int, hi: int) -> str | None:
        size = hi - lo
        with self._read() as connection:
            row = connection.execute(
                "select text from summaries where size = ? and k = ?",
                (size, lo // size),
            ).fetchone()
        return row[0] if row else None

    def put_summary(self, lo: int, hi: int, text: str) -> bool:
        size = hi - lo
        with self._write() as connection:
            have = connection.execute(
                "select count(*) from summaries where size = ?", (size,)
            ).fetchone()[0]
            if have != lo // size:
                return False
            connection.execute(
                "insert into summaries values (?, ?, ?)", (size, lo // size, text)
            )
        return True

    def drop_summaries(self, lo: int, hi: int) -> list[tuple[int, int]]:
        dropped = []
        size = hi - lo
        with self._write() as connection:
            total = connection.execute("select count(*) from memories").fetchone()[0]
            while size <= total:
                keep = lo // size
                rows = connection.execute(
                    "select k from summaries where size = ? and k >= ? order by k",
                    (size, keep),
                ).fetchall()
                dropped += [(k * size, (k + 1) * size) for (k,) in rows]
                connection.execute(
                    "delete from summaries where size = ? and k >= ?", (size, keep)
                )
                size *= 2
        return dropped
