"""RemoteStore keeps a memory in a service, and lambda_response serves it."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_custom_store import run

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
    assert {"duplicate", "matches", "search"} <= set(calls)
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
