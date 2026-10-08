"""OptMem memory operations as Python functions with typed results.

Each result has a ``text`` field: the result, written for an agent to read, with
the next step when one is due.
"""

from __future__ import annotations

import datetime
import importlib
import importlib.util
import json
import os
import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Annotated

from vis_optmem.cover import cover
from vis_optmem.store import (
    DEFAULT_DIRECTORY,
    SIZE_NAMES,
    SIZES,
    DamagedSummary,
    FileStore,
    MemoryStore,
    parse_size,
    pretty,
)

NAMESPACE = "memo"
"""The name that Vis gives the tools. Next-step calls in ``text`` use it."""

RAW_BLOCK_LIMIT = 16
"""Blocks of at most this many memories are summarized from the memories themselves.

Larger blocks are summarized from the summaries of their two halves.
"""

STORE_GROUP = "vis_optmem.stores"
"""Entry point group of named stores: ``MEMORY_STORE=<name>`` selects one."""

VIS_PART_BYTES = 8000
"""Largest part of one result, in bytes.

Vis shows at most 4,096 tokens of one print. Lines with many digits can use one
token for each two bytes, so 8,000 bytes stays below that limit.
"""


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _block_name(lo: int, hi: int) -> str:
    return f"{lo}-{hi - 1}"


def parse_block(block: str) -> tuple[int, int]:
    """Read a block name such as ``16-31`` or ``#16-31``: both ends are included.

    Return the half-open range ``(lo, hi)``. A block holds 2, 4, 8 ... memories and
    starts at a multiple of its size.
    """
    match = re.fullmatch(r"#?([0-9]+)-([0-9]+)", str(block).strip())
    if not match:
        raise ValueError(
            f"{block!r} is not a block. Copy one from the memory, like 16-31."
        )
    lo, hi = int(match[1]), int(match[2]) + 1
    size = hi - lo
    if size < 2 or size & (size - 1) or lo % size:
        raise ValueError(
            f"{block} is not a block. A block holds 2, 4, 8 ... memories "
            "and starts at a multiple of its size, like 16-31."
        )
    return lo, hi


def check_line(text: str, limit: int, *, what: str) -> str:
    """Return ``text`` without outer whitespace, or raise ValueError if it is not one short line."""
    if not isinstance(text, str):
        raise TypeError(f"The {what} must be a string.")
    line = text.strip()
    if not line:
        raise ValueError(f"The {what} is empty. Write one line of text.")
    if "\n" in line or "\r" in line:
        lines = len(line.splitlines())
        raise ValueError(
            f"The {what} has {lines} lines. Write one line: merge them, or save them one by one."
        )
    size = len(line.encode("utf-8"))
    if size > limit:
        raise ValueError(
            f"The {what} is too long: {size} bytes, and the limit is {limit}. "
            "Letters with accents use 2 bytes. Make it shorter."
        )
    return line


def paginate(lines: list[str], max_lines: int, max_bytes: int) -> list[list[str]]:
    """Split lines into parts of at most ``max_lines`` lines and ``max_bytes`` bytes."""
    parts: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in lines:
        size = len(line.encode("utf-8")) + 1
        if current and (len(current) >= max_lines or used + size > max_bytes):
            parts.append(current)
            current, used = [], 0
        current.append(line)
        used += size
    if current:
        parts.append(current)
    return parts


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """A summary that the memory needs. Write it, then save it with memo.nap()."""

    block: Annotated[str, "Block to summarize, both ends included, for example 16-31."]
    sources: Annotated[
        tuple[str, ...],
        "Lines to summarize: the memories, or the summaries of the two halves.",
    ]
    max_bytes: Annotated[int, "Longest summary, in UTF-8 bytes."]
    remaining: Annotated[int, "Summaries that are due after this one."]
    text: Annotated[str, "The request as instructions, with the call that saves it."]


@dataclass(frozen=True, slots=True)
class Wake:
    """One part of the memory, or the summary that wake needs first."""

    text: Annotated[str, "What to read: the memory lines and the next step."]
    lines: Annotated[
        tuple[str, ...],
        "Memory lines of this part, oldest first. Empty when wake needs a summary first.",
    ]
    part: Annotated[int, "Number of this part, from 1."]
    parts: Annotated[int, "Number of parts. 0 when wake needs a summary first."]
    at: Annotated[
        int, "Number of memories that this read covers. Give it for the next part."
    ]
    is_awake: Annotated[bool, "True when the read is complete."]
    request: Annotated[
        SummaryRequest | None,
        "A summary that is due. When lines is empty, wake needs it first.",
    ]


@dataclass(frozen=True, slots=True)
class Saved:
    """A memory that note saved."""

    text: Annotated[str, "What to read: the new id and any summary that is due."]
    id: Annotated[int, "Id of the new memory."]
    date: Annotated[str, "Date of the new memory, as YYYY-MM-DD."]
    memory: Annotated[str, "The saved memory, without outer whitespace."]
    request: Annotated[SummaryRequest | None, "A summary that is due now."]


@dataclass(frozen=True, slots=True)
class Nap:
    """The result of a summary, and the next summary that is due."""

    text: Annotated[str, "What to read: the result and the next request."]
    saved: Annotated[str | None, "Block whose summary this call saved, or None."]
    summary: Annotated[str | None, "The saved summary, or None."]
    request: Annotated[SummaryRequest | None, "The next summary that is due, or None."]


@dataclass(frozen=True, slots=True)
class Recall:
    """Memories that match a pattern."""

    text: Annotated[str, "What to read: the matching memories and their count."]
    pattern: Annotated[str, "The regular expression, matched without case."]
    matches: Annotated[
        tuple[str, ...],
        "The newest matching memories that fit one print, oldest first.",
    ]
    total: Annotated[int, "Number of all matching memories."]


@dataclass(frozen=True, slots=True)
class Zoom:
    """The two halves of a block."""

    text: Annotated[str, "What to read: the two halves."]
    block: Annotated[str, "The opened block, both ends included."]
    halves: Annotated[
        tuple[str, ...],
        "A summary, or a memory when a half holds one. A half after the newest memory is absent.",
    ]


@dataclass(frozen=True, slots=True)
class Forgot:
    """Summaries that forget dropped."""

    text: Annotated[str, "What to read: the result and the next step."]
    blocks: Annotated[tuple[str, ...], "Dropped blocks, smallest first."]


@dataclass(frozen=True, slots=True)
class SizeValue:
    """One size of the memory."""

    name: Annotated[str, "Name in the config file."]
    value: Annotated[int, "Value in use."]
    default: Annotated[int, "Value when the config file does not set it."]
    meaning: Annotated[str, "What the size controls."]


@dataclass(frozen=True, slots=True)
class Sizes:
    """The sizes of the memory."""

    text: Annotated[str, "What to read: one size on each line."]
    sizes: Annotated[tuple[SizeValue, ...], "Every size."]
    changed: Annotated[tuple[str, ...], "Names that this call changed."]


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """The memory folder."""

    text: Annotated[str, "What to read: whether the folder is new."]
    path: Annotated[
        str, "Where the memory is: its folder, or the place of a custom store."
    ]
    memories: Annotated[int, "Number of memories."]
    is_new: Annotated[bool, "True when this call created the folder."]


@dataclass(frozen=True, slots=True)
class Imported:
    """Memories that an import added."""

    text: Annotated[str, "What to read: the new ids and the summaries that are due."]
    first: Annotated[int, "Id of the first imported memory."]
    last: Annotated[int, "Id of the last imported memory."]
    due: Annotated[int, "Number of summaries that are due."]


Base = Callable[[], str | os.PathLike[str]]

_loaded: dict[tuple[str, str, str], MemoryStore] = {}


def _factory(reference: str, base: Base | None) -> Callable[..., MemoryStore]:
    if ":" not in reference:
        found = metadata.entry_points(group=STORE_GROUP, name=reference)
        if not found:
            names = sorted(
                point.name for point in metadata.entry_points(group=STORE_GROUP)
            )
            known = f" Installed stores: {', '.join(names)}." if names else ""
            raise ValueError(
                f"MEMORY_STORE={reference} names no installed store.{known} "
                "Use module:factory, path/to/store.py:factory or an installed store name."
            )
        return next(iter(found)).load()
    source, _, name = reference.rpartition(":")
    if source.endswith(".py"):
        path = os.path.expanduser(source)
        if not os.path.isabs(path) and base is not None:
            path = os.path.join(os.fspath(base()), path)
        path = os.path.abspath(path)
        spec = importlib.util.spec_from_file_location(
            f"vis_optmem_store_{abs(hash(path))}", path
        )
        if spec is None or spec.loader is None or not os.path.isfile(path):
            raise ValueError(f"MEMORY_STORE: no Python file at {pretty(Path(path))}.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(source)
    try:
        return getattr(module, name)
    except AttributeError:
        raise ValueError(f"MEMORY_STORE: {source} has no {name}.") from None


def load_store(
    reference: str,
    config: Mapping[str, object] | None = None,
    *,
    base: Base | None = None,
) -> MemoryStore:
    """Build a custom store and keep it for the next calls.

    ``reference`` is ``module:factory``, ``path/to/store.py:factory`` or the name of an
    entry point in the ``vis_optmem.stores`` group. A relative file path starts at
    ``base``. The factory is a ``MemoryStore`` subclass or a function that returns a
    store; ``config`` gives its keyword arguments.
    """
    options = dict(config or {})
    where = os.fspath(base()) if base is not None else ""
    key = (reference, json.dumps(options, sort_keys=True, default=str), where)
    if key not in _loaded:
        store = _factory(reference, base)(**options)
        if not isinstance(store, MemoryStore):
            raise TypeError(
                f"MEMORY_STORE={reference} made a {type(store).__name__}, not a MemoryStore."
            )
        _loaded[key] = store
    return _loaded[key]


def _store_config() -> dict[str, object]:
    raw = os.environ.get("MEMORY_STORE_CONFIG", "").strip()
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"MEMORY_STORE_CONFIG is not valid JSON: {error}.") from None
    if not isinstance(config, dict):
        raise ValueError("MEMORY_STORE_CONFIG must be a JSON object.")
    return config


class Memo:
    """Permanent memory in the OptMem format, shared by every session on this machine.

    The memory is ``store`` when you give one: a ``MemoryStore``, or a function that
    returns one for each call. Else ``$MEMORY_STORE`` selects a custom store (see
    ``load_store``) with the JSON object in ``$MEMORY_STORE_CONFIG`` as its arguments.
    Else the memory is the OptMem folder ``directory``, else ``$MEMORY_DIR``, else
    ``~/.optmem/memory``: the same folder that the ``memo`` tool uses.

    ``base`` returns the folder for a relative path. Vis passes the session
    workspace, so ``MEMORY_DIR=memory`` is ``<workspace>/memory``. An absolute or
    ``~`` path does not use ``base``. Without ``base``, a relative path starts at
    the working directory of the process, as in the ``memo`` tool.
    """

    def __init__(
        self,
        directory: str | os.PathLike[str] | None = None,
        *,
        base: Base | None = None,
        store: MemoryStore | Callable[[], MemoryStore] | None = None,
    ) -> None:
        self._directory = directory
        self._base = base
        self._custom = store

    def _path(self) -> Path:
        chosen = os.path.expanduser(
            os.fspath(
                self._directory or os.environ.get("MEMORY_DIR") or DEFAULT_DIRECTORY
            )
        )
        if not os.path.isabs(chosen) and self._base is not None:
            chosen = os.path.join(os.fspath(self._base()), chosen)
        return Path(os.path.abspath(chosen))

    def _store(self) -> MemoryStore:
        if isinstance(self._custom, MemoryStore):
            return self._custom
        if self._custom is not None:
            return self._custom()
        reference = os.environ.get("MEMORY_STORE", "").strip()
        if reference and self._directory is None:
            return load_store(reference, _store_config(), base=self._base)
        return FileStore(self._path())

    def _open(self) -> tuple[MemoryStore, dict[str, int]]:
        store = self._store()
        if not store.exists():
            raise FileNotFoundError(
                f"No memory at {store.location}. To create it, call {NAMESPACE}.init(). "
                "To use another memory, set MEMORY_DIR or MEMORY_STORE."
            )
        store.prepare()
        return store, store.sizes()

    def _request(
        self, store: MemoryStore, sizes: dict[str, int], total: int
    ) -> SummaryRequest | None:
        due = store.due(total, limit=1)
        if not due:
            return None
        lo, hi = due[0]
        remaining = store.due_count(total) - 1
        if hi - lo <= RAW_BLOCK_LIMIT:
            sources = tuple(entry.line for entry in store.entries(lo, hi))
        else:
            middle = (lo + hi) // 2
            halves = []
            for start, end in ((lo, middle), (middle, hi)):
                summary = store.summary(start, end)
                if summary is None:
                    # A level holds a dense prefix and halves come first, so this
                    # record exists and is blank.
                    raise DamagedSummary(start, end)
                halves.append(f"#{_block_name(start, end)} {summary}")
            sources = tuple(halves)
        block = _block_name(lo, hi)
        limit = sizes["ENTRY_CHARS"]
        lines = [
            f"Summarize memories #{block} in one line of at most {limit} bytes.",
            "Keep what has lasting effect and drop the rest. Use only facts from these lines.",
            "",
            *(f"  {source}" for source in sources),
            "",
        ]
        if remaining:
            more = _plural(remaining, "more summary is", "more summaries are")
            lines.append(f"{more} due after this one.")
        lines.append(f'Next: await {NAMESPACE}.nap("{block}", "<your line>")')
        return SummaryRequest(block, sources, limit, remaining, "\n".join(lines))

    def init(self) -> StoreInfo:
        """Create the memory folder when it does not exist. Calling it again changes nothing.

        A new folder is a new memory. So wake and note never create one: a wrong
        MEMORY_DIR stops them instead of starting an empty memory.
        """
        store = self._store()
        is_new = store.create()
        store.sizes()
        count = store.count()
        place = store.location
        if is_new:
            text = f"Created {place}: one memory for every session on this machine."
        else:
            text = f"Found {place}: {_plural(count, 'memory', 'memories')}."
        path = str(store.directory) if isinstance(store, FileStore) else place
        return StoreInfo(text, path, count, is_new)

    def wake(self, part: int = 1, at: int | None = None) -> Wake:
        """Read your memory: recent memories in full, older ones as summaries.

        Read it at the start of each session. A large memory comes in parts that
        each fit one print. The text of each part gives the call for the next part.
        ``at`` keeps the memory count of the first part, so notes from other
        sessions cannot move the part boundaries. Without ``at``, wake reads all
        current memories. When a summary that the read needs is not written yet,
        the result has no lines and asks for that summary first.
        """
        store, sizes = self._open()
        now = store.count()
        if part < 1:
            raise ValueError("part starts at 1.")
        total = now if at is None else at
        if not 0 <= total <= now:
            raise ValueError(
                f"at={at}, but the memory holds {_plural(now, 'memory', 'memories')}. "
                f"Call {NAMESPACE}.wake() without at."
            )
        if total == 0:
            text = (
                f'No memories yet. Save the first one with {NAMESPACE}.note("<one line>").\n'
                "You are awake."
            )
            return Wake(text, (), 1, 1, 0, True, None)
        lines = []
        for lo, hi in cover(total, sizes["WAKE_LINES"]):
            if hi - lo == 1:
                lines.append(store.entry(lo).line)
                continue
            summary = store.summary(lo, hi)
            if summary is None:
                request = self._request(store, sizes, total)
                if request is not None:
                    due = _plural(request.remaining + 1, "summary", "summaries")
                    text = (
                        f"Cannot read the memory yet: it needs the summary of #{_block_name(lo, hi)}, "
                        f"which is not written.\nWrite the {due} that are due, one at a time. "
                        f"Then call {NAMESPACE}.wake() again.\n\n{request.text}"
                    )
                    return Wake(text, (), part, 0, total, False, request)
                # Another session can write the summary in the meantime.
                summary = store.summary(lo, hi)
                if summary is None:
                    raise DamagedSummary(lo, hi)
            lines.append(f"#{_block_name(lo, hi)} {summary}")
        parts = paginate(
            lines, sizes["PART_LINES"], min(sizes["PART_CHARS"], VIS_PART_BYTES)
        )
        if part > len(parts):
            raise ValueError(
                f"No part {part}: the memory has {_plural(len(parts), 'part', 'parts')}. "
                f"Call {NAMESPACE}.wake()."
            )
        shown = parts[part - 1]
        out = []
        if len(parts) > 1:
            count = _plural(total, "memory", "memories")
            out.append(
                f"Your memory, part {part} of {len(parts)}, oldest first ({count})."
            )
        out += shown
        is_awake = part == len(parts)
        request = None
        if is_awake:
            out.append("You are awake.")
            request = self._request(store, sizes, total)
            if request is not None:
                out += ["", request.text]
        else:
            out.append(
                f"Not awake yet. Next: await {NAMESPACE}.wake(part={part + 1}, at={total})"
            )
        return Wake(
            "\n".join(out), tuple(shown), part, len(parts), total, is_awake, request
        )

    def note(self, memory: str) -> Saved:
        """Save one memory: one line of at most ENTRY_CHARS bytes (280 by default).

        The memory gets the next id and the date of today. The log only grows:
        nothing can change or remove a saved memory. The result can ask for a
        summary. Write it before your next action.
        """
        store, sizes = self._open()
        line = check_line(memory, sizes["ENTRY_CHARS"], what="memory")
        date = datetime.date.today().isoformat()
        number = store.append([(date, line)])
        request = self._request(store, sizes, number + 1)
        text = f"Saved as #{number}."
        if request is not None:
            text += "\n\n" + request.text
        return Saved(text, number, date, line, request)

    def nap(self, block: str | None = None, summary: str | None = None) -> Nap:
        """Save a summary that the memory asked for, and get the next one.

        Without arguments, return the next summary that is due. Summaries are
        written in a fixed order, so a block other than the next one is refused.
        A summary that another session saved first is kept, not replaced.
        """
        if (block is None) != (summary is None):
            raise ValueError("Give both block and summary, or neither.")
        store, sizes = self._open()
        total = store.count()
        notes = []
        saved = None
        saved_summary = None
        if block is not None:
            lo, hi = parse_block(block)
            name = _block_name(lo, hi)
            due = store.due(total, limit=1)
            if not due:
                return Nap("Nothing left to summarize.", None, None, None)
            if (lo, hi) != due[0]:
                if store.summary(lo, hi) is None:
                    raise ValueError(
                        f"Wrong block: {block}. Summaries are written in order, and the "
                        f"next is {_block_name(*due[0])}. Call {NAMESPACE}.nap() to see it."
                    )
                notes.append(f"#{name} is already saved.")
            else:
                line = check_line(summary, sizes["ENTRY_CHARS"], what="summary")
                if store.put_summary(lo, hi, line):
                    saved, saved_summary = name, line
                    notes.append(f"#{name} saved.")
                else:
                    notes.append(
                        f"#{name} changed meanwhile: another session saved or dropped it."
                    )
        request = self._request(store, sizes, total)
        if request is None:
            notes.append("Nothing left to summarize.")
            return Nap("\n".join(notes), saved, saved_summary, None)
        text = "\n\n".join(["\n".join(notes), request.text]) if notes else request.text
        return Nap(text, saved, saved_summary, request)

    def recall(self, pattern: str) -> Recall:
        """Search every memory with a regular expression, without case.

        The search reads the raw memories, not the summaries. It matches the
        whole line, so it can also find an id or a date. When the matches do not
        fit one print, the result keeps the newest ones and gives the total.
        """
        store, sizes = self._open()
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Bad regular expression: {error}.") from None
        limit = min(sizes["PART_CHARS"], VIS_PART_BYTES)
        kept: deque[str] = deque()
        used = total = 0
        for entry in store.scan():
            line = entry.line
            if not regex.search(line):
                continue
            total += 1
            kept.append(line)
            used += len(line.encode("utf-8")) + 1
            while used > limit:
                used -= len(kept.popleft().encode("utf-8")) + 1
        if not total:
            return Recall("No match.", pattern, (), 0)
        count = _plural(total, "match", "matches")
        if len(kept) < total:
            tail = f"Newest {len(kept)} of {count}. Use a narrower pattern."
        else:
            tail = f"{count}."
        return Recall("\n".join([*kept, tail]), pattern, tuple(kept), total)

    def zoom(self, block: str) -> Zoom:
        """Open a block of the memory into its two halves.

        Each half is a summary, or a memory when it holds one memory. Zoom again
        into a half to go down to single memories.
        """
        store, _ = self._open()
        lo, hi = parse_block(block)
        total = store.count()
        if lo >= total:
            raise ValueError(
                f"#{_block_name(lo, hi)} starts after the newest memory: "
                f"the memory holds {_plural(total, 'memory', 'memories')}."
            )
        middle = (lo + hi) // 2
        halves = []
        for start, end in ((lo, middle), (middle, hi)):
            if start >= total:
                continue
            if end - start == 1:
                halves.append(store.entry(start).line)
            else:
                summary = store.summary(start, end) or "(no summary yet)"
                halves.append(f"#{_block_name(start, end)} {summary}")
        return Zoom("\n".join(halves), _block_name(lo, hi), tuple(halves))

    def forget(self, block: str) -> Forgot:
        """Drop a wrong summary and every summary that depends on it.

        Later summaries of the same sizes go too. The memories do not change,
        so memo.nap() asks for the dropped summaries again.
        """
        store, _ = self._open()
        lo, hi = parse_block(block)
        dropped = store.drop_summaries(lo, hi)
        if not dropped:
            raise ValueError(f"No summary at {_block_name(lo, hi)}.")
        names = tuple(_block_name(start, end) for start, end in dropped)
        count = _plural(len(names), "summary", "summaries")
        again = "it" if len(names) == 1 else "them"
        text = (
            f"Dropped {count}, from #{names[0]} up. "
            f"Call {NAMESPACE}.nap() to write {again} again."
        )
        return Forgot(text, names)

    def config(self, changes: Mapping[str, int | None] | None = None) -> Sizes:
        """Show the sizes of the memory, or change them.

        ``changes`` maps size names to new values. None restores the default.
        Sizes only choose what is shown, so a change rewrites no memory.
        """
        store, _ = self._open()
        overrides = store.overrides()
        changed = []
        for name, value in (changes or {}).items():
            key = str(name).strip().upper()
            if key not in SIZE_NAMES:
                raise ValueError(
                    f"{name} is not a size. Use one of {', '.join(SIZE_NAMES)}."
                )
            if value is None:
                overrides.pop(key, None)
            else:
                overrides[key] = parse_size(key, value)
            changed.append(key)
        if changed:
            store.write_overrides(overrides)
        values = tuple(
            SizeValue(
                size.name,
                overrides.get(size.name, size.default),
                size.default,
                size.meaning,
            )
            for size in SIZES
        )
        text = "\n".join(
            f"{value.name:<12} {value.value:<7} {value.meaning}"
            + (f" (default {value.default})" if value.name in overrides else "")
            for value in values
        )
        return Sizes(text, values, tuple(changed))

    def import_memories(self, path: str) -> Imported:
        """Add dated memories from a UTF-8 file, to start a memory from older records.

        Each line is ``YYYY-MM-DD text``. Dates must not go back in time, also not
        before the newest memory. Empty lines are skipped. If one line is wrong,
        nothing is added.
        """
        store, sizes = self._open()
        source = Path(os.path.expanduser(path))
        try:
            lines = source.read_text(encoding="utf-8").split("\n")
        except UnicodeDecodeError:
            raise ValueError(
                f"{pretty(source)} is not UTF-8 text. Convert it, then import again."
            ) from None
        count = store.count()
        last = store.entry(count - 1).date if count else "0000-00-00"
        limit = sizes["ENTRY_CHARS"]
        items = []
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            date, _, text = raw.partition(" ")
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
                raise ValueError(
                    f"Line {number}: expected 'YYYY-MM-DD text', got: {raw}"
                )
            try:
                datetime.date.fromisoformat(date)
            except ValueError:
                raise ValueError(f"Line {number}: {date} is not a real date.") from None
            if date < last:
                raise ValueError(
                    f"Line {number}: {date} is before the memory before it ({last})."
                )
            text = text.strip()
            size = len(text.encode("utf-8"))
            if not text or size > limit:
                raise ValueError(
                    f"Line {number}: {size} bytes, and the limit is {limit}."
                )
            items.append((date, text))
            last = date
        if not items:
            raise ValueError(f"{pretty(source)} has no memories.")
        first = store.append(items)
        final = first + len(items) - 1
        due = store.due_count(store.count())
        text = f"Imported {_plural(len(items), 'memory', 'memories')}, #{first} to #{final}."
        if due:
            again = "it" if due == 1 else "them"
            text += (
                f"\n{_plural(due, 'summary is', 'summaries are')} due. "
                f"Call {NAMESPACE}.nap() to write {again}."
            )
        return Imported(text, first, final, due)


def status(
    directory: str | os.PathLike[str] | None = None,
    *,
    base: Base | None = None,
    store: MemoryStore | Callable[[], MemoryStore] | None = None,
) -> dict[str, object]:
    """Facts for the session context: memory count, summaries due and the memory size limit.

    ``base`` and ``store`` choose the memory, as in ``Memo``. It reports a problem as a
    fact and never raises, so a bad folder or an unreachable store cannot stop a session.
    """
    try:
        chosen = Memo(directory, base=base, store=store)._store()
    except Exception as error:
        return {"store": "unavailable", "error": str(error)}
    try:
        if not chosen.exists():
            return {"store": "missing", "path": chosen.location}
        total = chosen.count()
        facts: dict[str, object] = {
            "memories": total,
            "summaries_due": chosen.due_count(total),
        }
    except Exception as error:
        return {
            "store": "unreadable",
            "path": chosen.location,
            "error": str(error),
        }
    try:
        facts["max_bytes"] = chosen.sizes()["ENTRY_CHARS"]
    except Exception as error:
        facts["config_error"] = str(error)
    return facts
