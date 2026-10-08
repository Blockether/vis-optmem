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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from itertools import zip_longest
from pathlib import Path
from typing import Annotated, Literal

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

RELATED_LIMIT = 10
"""Most related memories that one recall shows."""

RAW_BLOCK_LIMIT = 16
"""Blocks of at most this many memories are summarized from the memories themselves.

Larger blocks are summarized from the summaries of their two halves.
"""

PERSONAL = "personal"
"""The memory of one person: ``MEMORY_DIR`` or ``MEMORY_STORE``. Tools use it by default."""

TEAM = "team"
"""The memory that the whole team shares: ``MEMORY_TEAM_DIR`` or ``MEMORY_TEAM_STORE``.

Agents search it with ``memo.recall`` and do not read it at the start of a session, so
its notes ask for no summaries. A scheduled job can write them with ``memo.nap``.
"""

ALL = "all"
"""Both memories at once. Only ``memo.recall`` takes it: a write goes to one memory."""

SCOPES = (PERSONAL, TEAM)

VARIABLES = {
    PERSONAL: ("MEMORY_DIR", "MEMORY_STORE", "MEMORY_STORE_CONFIG"),
    TEAM: (
        "MEMORY_TEAM_DIR",
        "MEMORY_TEAM_STORE",
        "MEMORY_TEAM_STORE_CONFIG",
    ),
}
"""The environment variables that choose each memory: folder, custom store, store config."""

TOOLS = (
    "init",
    "wake",
    "note",
    "nap",
    "recall",
    "zoom",
    "forget",
    "config",
    "import_memories",
)
"""Every memo tool. ``MEMORY_DISABLED_TOOLS`` can turn any of them off."""

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


MemoryScope = Annotated[
    Literal["personal", "team"],
    'Which memory: "personal" (yours, the default) or "team" (shared by every person).',
]

SearchScope = Annotated[
    Literal["personal", "team", "all"],
    'Which memory to search: "personal" (the default), "team", or "all" for both at once.',
]

Block = Annotated[
    str,
    'A block of memories, both ends included, like "16-31". Copy it from a summary line.',
]


def check_scope(scope: str) -> str:
    """Return ``scope`` if it names a memory, or raise ValueError."""
    if scope == ALL:
        raise ValueError(
            f"scope={ALL!r} works only with {NAMESPACE}.recall. "
            f"Use {PERSONAL!r} or {TEAM!r}."
        )
    if scope not in SCOPES:
        raise ValueError(
            f"scope={scope!r} is not a memory. Use {PERSONAL!r} or {TEAM!r}."
        )
    return scope


def _call(method: str, *args: str, scope: str = PERSONAL) -> str:
    """A tool call for a next step. It names the memory when it is not personal."""
    if scope != PERSONAL:
        args = (*args, f'scope="{scope}"')
    return f"{NAMESPACE}.{method}({', '.join(args)})"


class MemoryNotSet(LookupError):
    """No folder and no store is set for the team memory."""


class ToolDisabled(PermissionError):
    """The person who set up the memory turned this tool off."""


def disabled_tools(names: str | Iterable[str] | None = None) -> frozenset[str]:
    """The tools that are turned off: ``names``, else ``$MEMORY_DISABLED_TOOLS``.

    A string lists tool names separated by commas or spaces, like
    ``init,import_memories``. An unknown name raises ValueError, so a typo cannot
    leave a tool on.
    """
    if names is None:
        names = os.environ.get("MEMORY_DISABLED_TOOLS", "")
    if isinstance(names, str):
        names = re.split(r"[\s,]+", names)
    chosen = frozenset(name.strip() for name in names if name.strip())
    unknown = sorted(chosen - set(TOOLS))
    if unknown:
        raise ValueError(
            f"MEMORY_DISABLED_TOOLS: {', '.join(unknown)} is not a memo tool. "
            f"Use names from: {', '.join(TOOLS)}."
        )
    return chosen


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
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class Saved:
    """A memory that note saved."""

    text: Annotated[str, "What to read: the new id and any summary that is due."]
    id: Annotated[int, "Id of the new memory."]
    date: Annotated[str, "Date of the new memory, as YYYY-MM-DD."]
    memory: Annotated[str, "The saved memory, without outer whitespace."]
    request: Annotated[SummaryRequest | None, "A summary that is due now."]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class Nap:
    """The result of a summary, and the next summary that is due."""

    text: Annotated[str, "What to read: the result and the next request."]
    saved: Annotated[str | None, "Block whose summary this call saved, or None."]
    summary: Annotated[str | None, "The saved summary, or None."]
    request: Annotated[SummaryRequest | None, "The next summary that is due, or None."]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class Recall:
    """Memories that match a pattern, and memories related to a query.

    With ``scope="all"``, each line starts with its memory, like ``[team] #3 ...``,
    because both memories number their memories from 0.
    """

    text: Annotated[str, "What to read: the matching and the related memories."]
    pattern: Annotated[
        str | None, "The regular expression, matched without case, or None."
    ]
    matches: Annotated[
        tuple[str, ...],
        "The newest matching memories that fit one print, oldest first.",
    ]
    total: Annotated[int, "Number of all matching memories."]
    scope: Annotated[str, "The memory: personal, team or all."] = PERSONAL
    about: Annotated[str | None, "The query for related memories, or None."] = None
    related: Annotated[tuple[str, ...], "Memories related to about, best first."] = ()


@dataclass(frozen=True, slots=True)
class Zoom:
    """The two halves of a block."""

    text: Annotated[str, "What to read: the two halves."]
    block: Annotated[str, "The opened block, both ends included."]
    halves: Annotated[
        tuple[str, ...],
        "A summary, or a memory when a half holds one. A half after the newest memory is absent.",
    ]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class Forgot:
    """Summaries that forget dropped."""

    text: Annotated[str, "What to read: the result and the next step."]
    blocks: Annotated[tuple[str, ...], "Dropped blocks, smallest first."]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


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
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """The memory that init found or created."""

    text: Annotated[str, "What to read: whether the memory is new, and where it is."]
    path: Annotated[
        str, "Where the memory is: its folder, or the place of a custom store."
    ]
    memories: Annotated[int, "Number of memories."]
    is_new: Annotated[bool, "True when this call created the memory."]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


@dataclass(frozen=True, slots=True)
class Imported:
    """Memories that an import added."""

    text: Annotated[str, "What to read: the new ids and the summaries that are due."]
    first: Annotated[int, "Id of the first imported memory."]
    last: Annotated[int, "Id of the last imported memory."]
    due: Annotated[int, "Number of summaries that are due."]
    scope: Annotated[str, "The memory: personal or team."] = PERSONAL


Base = Callable[[], str | os.PathLike[str]]

_loaded: dict[tuple[str, str, str], MemoryStore] = {}


def _factory(
    reference: str, base: Base | None, variable: str
) -> Callable[..., MemoryStore]:
    if ":" not in reference:
        found = metadata.entry_points(group=STORE_GROUP, name=reference)
        if not found:
            names = sorted(
                point.name for point in metadata.entry_points(group=STORE_GROUP)
            )
            known = f" Installed stores: {', '.join(names)}." if names else ""
            raise ValueError(
                f"{variable}={reference} names no installed store.{known} "
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
            raise ValueError(f"{variable}: no Python file at {pretty(Path(path))}.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(source)
    try:
        return getattr(module, name)
    except AttributeError:
        raise ValueError(f"{variable}: {source} has no {name}.") from None


def load_store(
    reference: str,
    config: Mapping[str, object] | None = None,
    *,
    base: Base | None = None,
    variable: str = "MEMORY_STORE",
) -> MemoryStore:
    """Build a custom store and keep it for the next calls.

    ``reference`` is ``module:factory``, ``path/to/store.py:factory`` or the name of an
    entry point in the ``vis_optmem.stores`` group. A relative file path starts at
    ``base``. The factory is a ``MemoryStore`` subclass or a function that returns a
    store; ``config`` gives its keyword arguments. Errors name ``variable``.
    """
    options = dict(config or {})
    where = os.fspath(base()) if base is not None else ""
    key = (reference, json.dumps(options, sort_keys=True, default=str), where)
    if key not in _loaded:
        store = _factory(reference, base, variable)(**options)
        if not isinstance(store, MemoryStore):
            raise TypeError(
                f"{variable}={reference} made a {type(store).__name__}, not a MemoryStore."
            )
        _loaded[key] = store
    return _loaded[key]


def _store_config(variable: str = "MEMORY_STORE_CONFIG") -> dict[str, object]:
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{variable} is not valid JSON: {error}.") from None
    if not isinstance(config, dict):
        raise ValueError(f"{variable} must be a JSON object.")
    return config


class Memo:
    """Permanent memory in the OptMem format, in two memories: personal and team.

    Each tool takes ``scope``. ``"personal"``, the default, is the memory of one
    person. ``"team"`` is a second memory that every person who reaches its
    folder or store shares. The two memories never mix: each has its own memories
    and summaries. Agents read the personal memory and search the team memory.

    The personal memory is ``store`` when you give one: a ``MemoryStore``, or a
    function that returns one for each call. Else ``$MEMORY_STORE`` selects a custom
    store (see ``load_store``) with the JSON object in ``$MEMORY_STORE_CONFIG`` as its
    arguments. Else the memory is the OptMem folder ``directory``, else
    ``$MEMORY_DIR``, else ``~/.optmem/memory``: the same folder that the ``memo``
    tool uses.

    The team memory has no default place. ``team_store``,
    ``$MEMORY_TEAM_STORE`` with ``$MEMORY_TEAM_STORE_CONFIG``,
    ``team_directory`` and ``$MEMORY_TEAM_DIR`` choose it in the same order.

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
        team_directory: str | os.PathLike[str] | None = None,
        team_store: MemoryStore | Callable[[], MemoryStore] | None = None,
        disabled: str | Iterable[str] | None = None,
    ) -> None:
        self._directory = {PERSONAL: directory, TEAM: team_directory}
        self._base = base
        self._custom = {PERSONAL: store, TEAM: team_store}
        self._disabled = None if disabled is None else disabled_tools(disabled)

    def _allow(self, tool: str) -> None:
        """Raise ToolDisabled when ``tool`` is turned off."""
        off = self._disabled if self._disabled is not None else disabled_tools()
        if tool in off:
            raise ToolDisabled(
                f"{NAMESPACE}.{tool} is turned off in this setup (MEMORY_DISABLED_TOOLS). "
                "Do not call it again."
            )

    def _path(self, scope: str = PERSONAL) -> Path | None:
        folder, _, _ = VARIABLES[scope]
        default = DEFAULT_DIRECTORY if scope == PERSONAL else None
        chosen = self._directory[scope] or os.environ.get(folder) or default
        if not chosen:
            return None
        chosen = os.path.expanduser(os.fspath(chosen))
        if not os.path.isabs(chosen) and self._base is not None:
            chosen = os.path.join(os.fspath(self._base()), chosen)
        return Path(os.path.abspath(chosen))

    def _reference(self, scope: str) -> str:
        """The custom store in the environment, or "" when a folder or object wins."""
        if self._custom[scope] is not None or self._directory[scope] is not None:
            return ""
        return os.environ.get(VARIABLES[scope][1], "").strip()

    def _is_set(self, scope: str = TEAM) -> bool:
        """True when the memory has a place: always for personal, by setting for team."""
        check_scope(scope)
        return bool(
            self._custom[scope] is not None
            or self._reference(scope)
            or self._path(scope) is not None
        )

    def _store(self, scope: str = PERSONAL) -> MemoryStore:
        check_scope(scope)
        custom = self._custom[scope]
        if isinstance(custom, MemoryStore):
            return custom
        if custom is not None:
            return custom()
        folder, variable, config = VARIABLES[scope]
        reference = self._reference(scope)
        if reference:
            return load_store(
                reference, _store_config(config), base=self._base, variable=variable
            )
        path = self._path(scope)
        if path is None:
            raise MemoryNotSet(
                "No team memory is set, so use the personal memory. To set one, "
                "set MEMORY_TEAM_DIR to a folder that every person reaches, or "
                "MEMORY_TEAM_STORE to a shared store."
            )
        if (
            scope == TEAM
            and self._custom[PERSONAL] is None
            and not self._reference(PERSONAL)
            and path == self._path(PERSONAL)
        ):
            raise ValueError(
                f"{folder} is the personal memory folder {pretty(path)}. "
                "Give the team memory its own folder."
            )
        return FileStore(path)

    def _open(self, scope: str = PERSONAL) -> tuple[MemoryStore, dict[str, int]]:
        store = self._store(scope)
        if not store.exists():
            folder, variable, _ = VARIABLES[scope]
            off = self._disabled if self._disabled is not None else disabled_tools()
            if "init" in off:
                create = "Ask the person who set up the memory to create it."
            else:
                create = f"To create it, call {_call('init', scope=scope)}."
            raise FileNotFoundError(
                f"No memory at {store.location}. {create} "
                f"To use another memory, set {folder} or {variable}."
            )
        store.prepare()
        return store, store.sizes()

    def _request(
        self,
        store: MemoryStore,
        sizes: dict[str, int],
        total: int,
        scope: str = PERSONAL,
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
        save = _call("nap", f'"{block}"', '"<your line>"', scope=scope)
        lines.append(f"Next: await {save}")
        return SummaryRequest(block, sources, limit, remaining, "\n".join(lines))

    def init(self, scope: MemoryScope = PERSONAL) -> StoreInfo:
        """Create a memory when it does not exist. Calling it again changes nothing.

        Call it once for each memory, before its first note. wake and note never
        create a memory, so a wrong MEMORY_DIR or MEMORY_TEAM_DIR stops them
        instead of starting an empty memory.
        """
        self._allow("init")
        store = self._store(scope)
        is_new = store.create()
        store.sizes()
        count = store.count()
        place = store.location
        if is_new:
            text = f"Created {place}: " + (
                "one memory for every session on this machine."
                if scope == PERSONAL
                else "one team memory who reaches it."
            )
        else:
            text = f"Found {place}: {_plural(count, 'memory', 'memories')}."
        path = str(store.directory) if isinstance(store, FileStore) else place
        return StoreInfo(text, path, count, is_new, scope)

    def wake(
        self,
        part: Annotated[
            int,
            "Part to read, from 1. Each part fits one print and names the next call.",
        ] = 1,
        at: Annotated[
            int | None,
            "Memory count of part 1. Copy it from the Next: call. Leave it out for part 1.",
        ] = None,
        scope: MemoryScope = PERSONAL,
    ) -> Wake:
        """Read a memory: recent memories in full, older periods as summaries.

        Read the personal memory at the start of each session. Search the team
        memory with memo.recall() instead of reading it. A large memory comes in parts that each fit one print, and each part gives the
        call for the next one. When the read needs a summary that is not written
        yet, the result has no lines and asks for that summary first.
        """
        self._allow("wake")
        store, sizes = self._open(scope)
        now = store.count()
        if part < 1:
            raise ValueError(
                f"part starts at 1. Call {_call('wake', scope=scope)} for the first part."
            )
        total = now if at is None else at
        if not 0 <= total <= now:
            raise ValueError(
                f"at={at}, but the memory holds {_plural(now, 'memory', 'memories')}. "
                f"Call {_call('wake', scope=scope)} without at."
            )
        if total == 0:
            first = _call("note", '"<one line>"', scope=scope)
            text = f"No memories yet. Save the first one with {first}.\nYou are awake."
            return Wake(text, (), 1, 1, 0, True, None, scope)
        lines = []
        for lo, hi in cover(total, sizes["WAKE_LINES"]):
            if hi - lo == 1:
                lines.append(store.entry(lo).line)
                continue
            summary = store.summary(lo, hi)
            if summary is None:
                request = self._request(store, sizes, total, scope)
                if request is not None:
                    due = _plural(request.remaining + 1, "summary", "summaries")
                    text = (
                        f"Cannot read the memory yet: it needs the summary of #{_block_name(lo, hi)}, "
                        f"which is not written.\nWrite the {due} that are due, one at a time. "
                        f"Then call {_call('wake', scope=scope)} again.\n\n{request.text}"
                    )
                    return Wake(text, (), part, 0, total, False, request, scope)
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
                f"Call {_call('wake', scope=scope)}."
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
            request = self._request(store, sizes, total, scope)
            if request is not None:
                out += ["", request.text]
        else:
            out.append(
                f"Not awake yet. Next: await "
                f"{_call('wake', f'part={part + 1}', f'at={total}', scope=scope)}"
            )
        return Wake(
            "\n".join(out),
            tuple(shown),
            part,
            len(parts),
            total,
            is_awake,
            request,
            scope,
        )

    def note(
        self,
        memory: Annotated[
            str,
            "One fact on one line, at most ENTRY_CHARS bytes (280 by default). "
            "It can never change.",
        ],
        scope: MemoryScope = PERSONAL,
    ) -> Saved:
        """Save one fact as a new memory, with the date of today.

        A saved memory never changes and nothing removes it, so save only facts of
        lasting value. Save to ``scope="team"`` only general knowledge that
        every person may read. The result can ask for a summary: save it with
        memo.nap() before your next action.
        """
        self._allow("note")
        store, sizes = self._open(scope)
        line = check_line(memory, sizes["ENTRY_CHARS"], what="memory")
        date = datetime.date.today().isoformat()
        number = store.append([(date, line)])
        # The team memory is searched, not read, so its notes ask for no summary.
        request = (
            self._request(store, sizes, number + 1, scope)
            if scope == PERSONAL
            else None
        )
        text = f"Saved as #{number}" + (
            "." if scope == PERSONAL else " in the team memory."
        )
        if request is not None:
            text += "\n\n" + request.text
        return Saved(text, number, date, line, request, scope)

    def nap(
        self,
        block: Annotated[
            str | None,
            'Block that the request names, like "16-31". Leave out both arguments '
            "to see the next request.",
        ] = None,
        summary: Annotated[
            str | None,
            "One line that summarizes the block, from the lines of the request only.",
        ] = None,
        scope: MemoryScope = PERSONAL,
    ) -> Nap:
        """Save a summary that a result asked for, and get the next request.

        Without arguments, it shows the next summary that is due. Summaries are
        saved in a fixed order, so a block other than the next one is refused.
        When another session saved the summary first, that summary stays.
        """
        self._allow("nap")
        if (block is None) != (summary is None):
            raise ValueError(
                "Give both block and summary, or neither. "
                f"Call {_call('nap', scope=scope)} to see the block that is due."
            )
        store, sizes = self._open(scope)
        total = store.count()
        notes = []
        saved = None
        saved_summary = None
        if block is not None:
            lo, hi = parse_block(block)
            name = _block_name(lo, hi)
            due = store.due(total, limit=1)
            if not due:
                return Nap("Nothing left to summarize.", None, None, None, scope)
            if (lo, hi) != due[0]:
                if store.summary(lo, hi) is None:
                    raise ValueError(
                        f"Wrong block: {block}. Summaries are written in order, and the "
                        f"next is {_block_name(*due[0])}. "
                        f"Call {_call('nap', scope=scope)} to see it."
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
        request = self._request(store, sizes, total, scope)
        if request is None:
            notes.append("Nothing left to summarize.")
            return Nap("\n".join(notes), saved, saved_summary, None, scope)
        text = "\n\n".join(["\n".join(notes), request.text]) if notes else request.text
        return Nap(text, saved, saved_summary, request, scope)

    def recall(
        self,
        pattern: Annotated[
            str | None,
            'Regular expression, matched without case against "#id date text", '
            'like r"postgres|migration".',
        ] = None,
        about: Annotated[
            str | None,
            'Plain words about the topic, like "change a database schema safely". '
            "Finds related memories by meaning.",
        ] = None,
        scope: SearchScope = PERSONAL,
    ) -> Recall:
        """Search the memories with a regular expression, by meaning, or both.

        ``pattern`` finds the memories that contain its words exactly. It reads
        the memories, not the summaries, so it can also find an id or a date.
        When the matches do not fit one print, the result keeps the newest ones
        and gives the total.

        ``about`` finds up to 10 related memories, best first, also when they use
        other words. The store decides how. The default store ranks memories by
        the words that they share with ``about``.

        ``scope="all"`` searches the personal memory and the team memory
        in one call. Each line then starts with its memory, like ``[team]``.
        """
        self._allow("recall")
        if pattern is None and about is None:
            raise ValueError(
                "Give pattern (a regular expression), about (plain words), or both, "
                'like memo.recall(r"uv|poetry", about="Python packaging").'
            )
        regex = None
        if pattern is not None:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as error:
                raise ValueError(
                    f"Bad regular expression: {error}. To search plain words, "
                    'give them as about="...".'
                ) from None
        if about is not None and not about.strip():
            raise ValueError("about is empty. Write what the memories are about.")
        if scope == ALL:
            scopes = [PERSONAL, *([TEAM] if self._is_set(TEAM) else [])]
        elif scope in SCOPES:
            scopes = [scope]
        else:
            raise ValueError(
                f"scope={scope!r} is not a memory. "
                f"Use {PERSONAL!r}, {TEAM!r} or {ALL!r}."
            )
        opened = [(name, *self._open(name)) for name in scopes]
        limit = min(VIS_PART_BYTES, *(sizes["PART_CHARS"] for _, _, sizes in opened))

        def label(name: str, line: str) -> str:
            return f"[{name}] {line}" if scope == ALL else line

        related: tuple[str, ...] = ()
        if about is not None:
            ranked = [
                [
                    label(name, entry.line)
                    for entry in store.search(about.strip(), RELATED_LIMIT)
                ]
                for name, store, _ in opened
            ]
            # Each store ranks only its own memories, so take the best of each in turn.
            mixed = [line for row in zip_longest(*ranked) for line in row if line]
            related = tuple(mixed[:RELATED_LIMIT])
            limit -= sum(len(line.encode("utf-8")) + 1 for line in related) + 100
        # (date, id, memory, line): on one date, the two memories alternate by id.
        found: list[tuple[str, int, int, str]] = []
        total = 0
        if regex is not None:
            for order, (name, store, _) in enumerate(opened):
                newest: deque[tuple[str, int, int, str]] = deque()
                used = 0
                for entry in store.scan():
                    if not regex.search(entry.line):
                        continue
                    total += 1
                    line = label(name, entry.line)
                    newest.append((entry.date, entry.id, order, line))
                    used += len(line.encode("utf-8")) + 1
                    while used > limit:
                        used -= len(newest.popleft()[3].encode("utf-8")) + 1
                found += newest
        # Keep the newest matches of all memories that fit one print, oldest first.
        found.sort()
        used = sum(len(item[3].encode("utf-8")) + 1 for item in found)
        while used > limit:
            used -= len(found.pop(0)[3].encode("utf-8")) + 1
        kept = [item[3] for item in found]
        out: list[str] = []
        if scope == ALL and len(opened) == 1:
            out += ["Only the personal memory: no team memory is set.", ""]
        if regex is not None:
            if not total:
                out.append("No match.")
            else:
                count = _plural(total, "match", "matches")
                if len(kept) < total:
                    tail = f"Newest {len(kept)} of {count}. Use a narrower pattern."
                else:
                    tail = f"{count}."
                out += [*kept, tail]
        if about is not None:
            seen = set(kept)
            new = [line for line in related if line not in seen]
            if out and out[-1]:
                out.append("")
            if not related:
                out.append(f"Nothing related to {about.strip()!r}.")
            else:
                out.append(f"Related to {about.strip()!r}, best first:")
                out += new or ["(all of them are above)"]
        return Recall(
            "\n".join(out),
            pattern,
            tuple(kept),
            total,
            scope,
            about,
            related,
        )

    def zoom(self, block: Block, scope: MemoryScope = PERSONAL) -> Zoom:
        """Open a summary into its two halves, to see its detail.

        Each half is a summary, or the memory itself when the half holds one
        memory. Zoom into a half again to go down to single memories.
        """
        self._allow("zoom")
        store, _ = self._open(scope)
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
        return Zoom("\n".join(halves), _block_name(lo, hi), tuple(halves), scope)

    def forget(self, block: Block, scope: MemoryScope = PERSONAL) -> Forgot:
        """Drop a wrong summary and every summary that depends on it.

        The memories stay. Later summaries of the same sizes are dropped too, and
        memo.nap() then asks for each of them again.
        """
        self._allow("forget")
        store, _ = self._open(scope)
        lo, hi = parse_block(block)
        dropped = store.drop_summaries(lo, hi)
        if not dropped:
            raise ValueError(
                f"No summary at #{_block_name(lo, hi)}. "
                "Copy a block from a summary line of memo.wake()."
            )
        names = tuple(_block_name(start, end) for start, end in dropped)
        count = _plural(len(names), "summary", "summaries")
        again = "it" if len(names) == 1 else "them"
        text = (
            f"Dropped {count}, from #{names[0]} up. "
            f"Call {_call('nap', scope=scope)} to write {again} again."
        )
        return Forgot(text, names, scope)

    def config(
        self,
        changes: Annotated[
            Mapping[str, int | None] | None,
            'Size names and new values, like {"WAKE_LINES": 128}. None as a value '
            "restores the default. Leave it out to only show the sizes.",
        ] = None,
        scope: MemoryScope = PERSONAL,
    ) -> Sizes:
        """Show the sizes of a memory, or change them.

        Sizes only choose what wake shows and how long a line can be, so a change
        rewrites no memory.
        """
        self._allow("config")
        store, _ = self._open(scope)
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
        return Sizes(text, values, tuple(changed), scope)

    def import_memories(
        self,
        path: Annotated[
            str, 'UTF-8 file with one "YYYY-MM-DD text" line for each memory.'
        ],
        scope: MemoryScope = PERSONAL,
    ) -> Imported:
        """Add dated memories from a file, to start a memory from older records.

        The dates must not go back in time, also not before the newest memory.
        Empty lines are skipped. When one line is wrong, nothing is added.
        """
        self._allow("import_memories")
        store, sizes = self._open(scope)
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
                    f"Line {number}: {date} is earlier than {last}, the date before it. "
                    "Sort the lines by date. New lines cannot go before the newest memory."
                )
            text = text.strip()
            size = len(text.encode("utf-8"))
            if not text:
                raise ValueError(f"Line {number}: there is no text after the date.")
            if size > limit:
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
        if due and scope == PERSONAL:
            again = "it" if due == 1 else "them"
            text += (
                f"\n{_plural(due, 'summary is', 'summaries are')} due. "
                f"Call {_call('nap', scope=scope)} to write {again}."
            )
        return Imported(text, first, final, due, scope)


def status(
    directory: str | os.PathLike[str] | None = None,
    *,
    base: Base | None = None,
    store: MemoryStore | Callable[[], MemoryStore] | None = None,
    scope: str = PERSONAL,
) -> dict[str, object]:
    """Facts for the session context: memory count, summaries due and the memory size limit.

    ``directory``, ``base`` and ``store`` choose the memory of ``scope``, as in ``Memo``.
    A team memory without a place is ``{"store": "unset"}``. It reports a
    problem as a fact and never raises, so a bad folder or an unreachable store cannot
    stop a session.
    """
    try:
        check_scope(scope)
        if scope == PERSONAL:
            memo = Memo(directory, base=base, store=store)
        else:
            memo = Memo(base=base, team_directory=directory, team_store=store)
        chosen = memo._store(scope)
    except MemoryNotSet:
        return {"store": "unset"}
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
