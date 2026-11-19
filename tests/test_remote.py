"""RemoteStore keeps a memory in a service, and lambda_response serves it."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_custom_store import run

from vis_optmem import remote
from vis_optmem.memo import TEAM, DuplicateMemory, Memo
from vis_optmem.remote import RemoteStore, answer, lambda_response
from vis_optmem.store import FileStore

TOKEN = "s3cret-token"


@pytest.fixture
def service(tmp_path):
    """A local HTTP service that passes each request to lambda_response, like AWS."""
    calls = []

    def stores(memory):
        if memory not in ("personal", "team"):
            raise LookupError(f"No memory {memory!r} in this service.")
        return FileStore(tmp_path / "service" / memory)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(json.loads(body)["op"])
            event = {
                "headers": dict(self.headers),
                "requestContext": {"http": {"method": "POST"}},
                "body": base64.b64encode(body).decode(),
                "isBase64Encoded": True,
            }
            response = lambda_response(event, stores, TOKEN)
            data = response["body"].encode()
            self.send_response(response["statusCode"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/", calls
    server.shutdown()
    server.server_close()


def test_a_remote_store_gives_the_same_memory_as_the_files(tmp_path, service):
    url, _ = service
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 first imported\n2999-01-02 second imported\n")
    files = run(Memo(tmp_path / "memory"), source)
    remote = run(Memo(store=RemoteStore(url, "personal", token=TOKEN)), source)
    assert remote == files


def test_the_team_memory_can_live_in_the_service(tmp_path, service, monkeypatch):
    url, calls = service
    monkeypatch.setenv("MEMORY_STORE_TOKEN", TOKEN)
    tool = Memo(tmp_path / "mine", team_store=RemoteStore(url))
    tool.init()
    assert tool.init(scope=TEAM).is_new
    assert tool.note("Deploy with the blue-green script.", scope=TEAM).id == 0
    with pytest.raises(DuplicateMemory):
        tool.note("Deploy with the blue green script.", scope=TEAM)
    assert "#0 " in tool.recall("blue", scope=TEAM).text
    assert "[team] #0 " in tool.recall(about="deploy script", scope="all").text
    assert {"note", "matches", "search"} <= set(calls)
    assert "scan" not in calls and "entries" not in calls


def test_the_service_refuses_a_wrong_token_and_an_unknown_memory(service):
    url, _ = service
    with pytest.raises(PermissionError, match=r"\$MEMORY_STORE_TOKEN"):
        RemoteStore(url, token="wrong").exists()
    with pytest.raises(LookupError, match="No memory 'other'"):
        RemoteStore(url, "other", token=TOKEN).exists()


def test_a_missing_service_names_its_url():
    with pytest.raises(OSError, match="Cannot reach the memory service at"):
        RemoteStore("http://127.0.0.1:9/", token=TOKEN, timeout_s=2).exists()


def test_answer_checks_each_request(tmp_path):
    def stores(memory):
        return FileStore(tmp_path / memory)

    good = {"version": 1, "memory": "team", "op": "count", "args": {}}
    assert answer({**good, "op": "create"}, stores) == (200, {"result": True})
    assert answer(good, stores) == (200, {"result": 0})
    bad = [
        ([], "JSON object"),
        ({**good, "version": 2}, "version 1"),
        ({**good, "op": "drop"}, "Unknown op"),
        ({**good, "memory": ""}, "non-empty"),
        ({**good, "args": {"lo": 0}}, "count takes the arguments: none"),
        ({**good, "op": "entries", "args": {"lo": "0", "hi": 1}}, "lo is not"),
        ({**good, "op": "entries", "args": {"lo": True, "hi": 1}}, "lo is not"),
        ({**good, "op": "append", "args": {"items": [["2026-01-01"]]}}, "items"),
        ({**good, "op": "append", "args": {"items": [["1\n2", "Use uv."]]}}, "items"),
        (
            {
                **good,
                "op": "note",
                "args": {"date": "2026-01-01\n#9", "text": "Use uv.", "force": False},
            },
            "date is not a valid date",
        ),
    ]
    for request, message in bad:
        status, body = answer(request, stores)
        assert status == 400 and message in body["error"]["message"], request


def test_a_store_failure_becomes_a_remote_error(tmp_path):
    class Broken(FileStore):
        def count(self):
            raise RuntimeError("table is gone")

    status, body = answer(
        {"version": 1, "memory": "x", "op": "count", "args": {}},
        lambda memory: Broken(tmp_path),
    )
    assert status == 500 and body["error"] == {
        "type": "RemoteError",
        "message": "table is gone",
    }
    event = {"headers": {}, "body": "{}", "requestContext": {"http": {"method": "GET"}}}
    assert (
        lambda_response(event, lambda memory: Broken(tmp_path), TOKEN)["statusCode"]
        == 400
    )


def test_the_service_refuses_what_memo_refuses(tmp_path):
    def stores(memory):
        store = FileStore(tmp_path / memory)
        store.create()
        return store

    def ask(op, language="", **args):
        request = {"version": 1, "memory": "team", "op": op, "args": args}
        return answer(request, stores, language=language)

    token = "ghp_" + "a1B2c3D4e5" * 4
    refused = [
        (
            ask("append", items=[["2026-01-01", f"The token is {token}"]]),
            "GitHub token",
        ),
        (ask("note", date="2026-01-01", text="x" * 341, force=False), "limit is 340"),
        (ask("put_summary", lo=0, hi=2, text=" padded "), "outer whitespace"),
        (
            ask(
                "note",
                "en",
                date="2026-01-01",
                text="To jest notatka po polsku, nie po angielsku.",
                force=False,
            ),
            "keeps English only",
        ),
        (
            ask("search", "en", query="jak się wdraża na produkcję", limit=5),
            "Not searched",
        ),
    ]
    for (status, body), message in refused:
        assert status == 400 and message in body["error"]["message"], message
        assert token not in body["error"]["message"]
    status, body = ask(
        "note", "en", date="2026-01-01", text="Use uv for Python.", force=False
    )
    assert (status, body["result"][1]) == (200, True)
    assert ask("count") == (200, {"result": 1})


def test_a_note_checks_and_saves_in_one_request(tmp_path, service):
    url, calls = service
    store = RemoteStore(url, "team", token=TOKEN)
    store.create()
    entry, is_new = store.note("2026-01-01", "Postgres listens on port 5432.")
    assert (entry.id, is_new) == (0, True)
    twin, is_new = store.note("2026-01-02", "Postgres listens on port 5432.")
    assert (twin.id, twin.date, is_new) == (0, "2026-01-01", False)
    assert (
        store.note("2026-01-02", "Postgres listens on port 5432.", force=True)[0].id
        == 1
    )
    assert calls.count("note") == 3 and "duplicate" not in calls


def test_a_service_without_the_note_op_still_saves_notes(
    tmp_path, service, monkeypatch
):
    url, calls = service
    monkeypatch.delitem(remote.OPS, "note")
    store = RemoteStore(url, "team", token=TOKEN)
    store.create()
    assert store.note("2026-01-01", "Use uv for Python.") == (
        remote.Entry(0, "2026-01-01", "Use uv for Python."),
        True,
    )
    assert store.note("2026-01-01", "Use uv for Python.")[1] is False
    assert {"note", "duplicate", "append"} <= set(calls)
