"""A memory in a service: ``RemoteStore`` calls it over HTTP, ``answer`` serves it.

Each ``MemoryStore`` call is one JSON request, sent with POST to one URL::

    {"version": 1, "memory": "team", "op": "append",
     "args": {"items": [["2026-10-08", "Use uv, not Poetry."]]}}

The answer is ``{"result": ...}`` with status 200, or
``{"error": {"type": ..., "message": ...}}`` with status 400, 401, 404 or 500.
An ``Entry`` travels as ``{"id": 0, "date": "2026-10-08", "text": "Use uv, not Poetry."}``.
The header ``Authorization: Bearer <token>`` carries the token. ``OPS`` lists every
operation with its arguments.

``answer`` runs one request on a ``MemoryStore``. ``lambda_response`` serves an AWS
Lambda function URL with it. So a service implements only a ``MemoryStore``.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from vis_optmem.store import Entry, MemoryStore

VERSION = 1
"""The protocol version. A service refuses a request with another version."""

OPS: dict[str, tuple[tuple[str, str], ...]] = {
    "exists": (),
    "create": (),
    "overrides": (),
    "write_overrides": (("overrides", "sizes"),),
    "count": (),
    "entries": (("lo", "int"), ("hi", "int")),
    "append": (("items", "items"),),
    "level_count": (("size", "int"),),
    "summary": (("lo", "int"), ("hi", "int")),
    "put_summary": (("lo", "int"), ("hi", "int"), ("text", "str")),
    "drop_summaries": (("lo", "int"), ("hi", "int")),
    "due": (("total", "int"), ("limit", "int?")),
    "due_count": (("total", "int"),),
    "search": (("query", "str"), ("limit", "int")),
    "duplicate": (("text", "str"),),
    "matches": (("pattern", "str"), ("newest", "int")),
}
"""Every operation: the ``MemoryStore`` method of the same name and its arguments."""

log = logging.getLogger(__name__)


class RemoteError(RuntimeError):
    """The memory service failed. The message gives its reason."""


def _entry(data: Mapping[str, Any]) -> Entry:
    return Entry(int(data["id"]), str(data["date"]), str(data["text"]))


def _entry_data(entry: Entry) -> dict[str, Any]:
    return {"id": entry.id, "date": entry.date, "text": entry.text}


def _blocks(data: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    return [(int(lo), int(hi)) for lo, hi in data]


class RemoteStore(MemoryStore):
    """A memory that a service keeps. Each call is one HTTP request to ``url``.

    ``memory`` names the memory in the service, so one service can keep many.
    ``token`` is the bearer token, else the value of ``$<token_variable>``. Set it
    with ``MEMORY_STORE=vis_optmem.remote:RemoteStore`` and, for example,
    ``MEMORY_STORE_CONFIG={"url": "https://...", "memory": "team"}``.
    """

    def __init__(
        self,
        url: str,
        memory: str = "team",
        token: str | None = None,
        token_variable: str = "MEMORY_STORE_TOKEN",
        timeout_s: float = 30.0,
    ) -> None:
        self.url = url
        self.memory = memory
        self.token = token
        self.token_variable = token_variable
        self.timeout_s = timeout_s

    def call(self, op: str, **args: Any) -> Any:
        """Run ``op`` in the service and give its result."""
        body = {"version": VERSION, "memory": self.memory, "op": op, "args": args}
        headers = {"Content-Type": "application/json"}
        token = self.token or os.environ.get(self.token_variable, "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())["result"]
        except urllib.error.HTTPError as error:
            raise self._error(error) from None
        except urllib.error.URLError as error:
            raise OSError(
                f"Cannot reach the memory service at {self.url}: {error.reason}."
            ) from None

    def _error(self, error: urllib.error.HTTPError) -> Exception:
        try:
            data = json.loads(error.read())["error"]
            kind, message = str(data["type"]), str(data["message"])
        except (ValueError, KeyError, TypeError):
            kind, message = "", f"HTTP {error.code} {error.reason}"
        if kind == "PermissionError":
            return PermissionError(
                f"{message} Check the token in ${self.token_variable}."
            )
        if kind in ("ValueError", "LookupError"):
            return {"ValueError": ValueError, "LookupError": LookupError}[kind](message)
        return RemoteError(f"The memory service at {self.url} failed: {message}")

    @property
    def location(self) -> str:
        return f"{self.memory} at {self.url}"

    def exists(self) -> bool:
        return bool(self.call("exists"))

    def create(self) -> bool:
        return bool(self.call("create"))

    def overrides(self) -> dict[str, int]:
        return {str(k): int(v) for k, v in self.call("overrides").items()}

    def write_overrides(self, overrides: Mapping[str, int]) -> None:
        self.call("write_overrides", overrides=dict(overrides))

    def count(self) -> int:
        return int(self.call("count"))

    def entries(self, lo: int, hi: int) -> list[Entry]:
        return [_entry(data) for data in self.call("entries", lo=lo, hi=hi)]

    def append(self, items: Sequence[tuple[str, str]]) -> int:
        return int(self.call("append", items=[list(item) for item in items]))

    def search(self, query: str, limit: int) -> list[Entry]:
        return [_entry(data) for data in self.call("search", query=query, limit=limit)]

    def duplicate(self, text: str) -> Entry | None:
        data = self.call("duplicate", text=text)
        return None if data is None else _entry(data)

    def matches(self, pattern: str, newest: int) -> tuple[int, list[Entry]]:
        total, entries = self.call("matches", pattern=pattern, newest=newest)
        return int(total), [_entry(data) for data in entries]

    def level_count(self, size: int) -> int:
        return int(self.call("level_count", size=size))

    def summary(self, lo: int, hi: int) -> str | None:
        return self.call("summary", lo=lo, hi=hi)

    def put_summary(self, lo: int, hi: int, text: str) -> bool:
        return bool(self.call("put_summary", lo=lo, hi=hi, text=text))

    def drop_summaries(self, lo: int, hi: int) -> list[tuple[int, int]]:
        return _blocks(self.call("drop_summaries", lo=lo, hi=hi))

    def due(self, total: int, limit: int | None = None) -> list[tuple[int, int]]:
        return _blocks(self.call("due", total=total, limit=limit))

    def due_count(self, total: int) -> int:
        return int(self.call("due_count", total=total))

    def scan(self) -> Iterator[Entry]:
        """Every memory, oldest first, 2048 in each request."""
        total = self.count()
        for lo in range(0, total, 2048):
            yield from self.entries(lo, min(lo + 2048, total))


def _check(kind: str, name: str, value: Any) -> Any:
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if kind == "int?" and value is None:
        return None
    if kind in ("int", "int?") and is_int:
        return value
    if kind == "str" and isinstance(value, str):
        return value
    if kind == "items" and isinstance(value, list):
        if all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) for part in item)
            for item in value
        ):
            return [tuple(item) for item in value]
    if kind == "sizes" and isinstance(value, dict):
        if all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            for k, v in value.items()
        ):
            return value
    raise ValueError(f"Argument {name} is not a valid {kind}.")


def _result(op: str, value: Any) -> Any:
    if op in ("entries", "search"):
        return [_entry_data(entry) for entry in value]
    if op == "duplicate":
        return None if value is None else _entry_data(value)
    if op == "matches":
        total, entries = value
        return [total, [_entry_data(entry) for entry in entries]]
    if op in ("drop_summaries", "due"):
        return [[lo, hi] for lo, hi in value]
    return value


def answer(
    request: Any, stores: Callable[[str], MemoryStore]
) -> tuple[int, dict[str, Any]]:
    """Run one request on the store that ``stores(memory)`` gives. Return status and body.

    ``stores`` raises LookupError for a memory that the service does not keep.
    A store error becomes an error body: ValueError gives 400, LookupError and
    FileNotFoundError give 404, and any other error gives 500.
    """
    try:
        if not isinstance(request, dict):
            raise ValueError("The request must be a JSON object.")
        if request.get("version") != VERSION:
            raise ValueError(f"Use protocol version {VERSION}.")
        op, memory = request.get("op"), request.get("memory")
        if op not in OPS:
            raise ValueError(f"Unknown op {op!r}. Use one of: {', '.join(OPS)}.")
        if not isinstance(memory, str) or not memory:
            raise ValueError("Give the memory as a non-empty string.")
        args = request.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be a JSON object.")
        names = [name for name, _ in OPS[op]]
        if sorted(args) != sorted(names):
            raise ValueError(f"{op} takes the arguments: {', '.join(names) or 'none'}.")
        checked = {name: _check(kind, name, args[name]) for name, kind in OPS[op]}
        store = stores(memory)
        return 200, {"result": _result(op, getattr(store, op)(**checked))}
    except ValueError as error:
        return 400, _failure("ValueError", error)
    except (LookupError, FileNotFoundError) as error:
        return 404, _failure("LookupError", error)
    except Exception as error:  # The client gets the reason; the log keeps the trace.
        log.exception("Memory request failed")
        return 500, _failure("RemoteError", error)


def _failure(kind: str, error: BaseException | str) -> dict[str, Any]:
    return {"error": {"type": kind, "message": str(error)}}


def lambda_response(
    event: Mapping[str, Any],
    stores: Callable[[str], MemoryStore],
    token: str,
) -> dict[str, Any]:
    """Serve one AWS Lambda function URL event with ``answer``.

    The request must use POST and the header ``Authorization: Bearer <token>``.
    Return the response that the Lambda handler gives back.
    """
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    method = (
        event.get("requestContext", {}).get("http", {}).get("method", "POST").upper()
    )
    if method != "POST":
        status, body = 400, _failure("ValueError", "Send each request with POST.")
    elif not token or not hmac.compare_digest(
        headers.get("authorization", "").encode("utf-8"),
        f"Bearer {token}".encode("utf-8"),
    ):
        status, body = 401, _failure("PermissionError", "The token is not valid.")
    else:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        try:
            request = json.loads(raw)
        except ValueError:
            request = None
        status, body = answer(request, stores)
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
