"""The extension registers nine typed memo methods, each with an explicit activity."""

import json
import runpy
import tomllib
from pathlib import Path

import blockether.vis.extension as vis
import pytest
from conftest import fill

from vis_optmem.memo import EVERYONE, Memo, Recall, Saved, Wake

ROOT = Path(__file__).resolve().parents[1]
TAGS = {
    "init": "mutation",
    "wake": "observation",
    "note": "mutation",
    "nap": "mutation",
    "recall": "observation",
    "zoom": "observation",
    "forget": "mutation",
    "config": "mutation",
    "import_memories": "mutation",
}
ENV = (
    "MEMORY_DIR",
    "MEMORY_STORE",
    "MEMORY_STORE_CONFIG",
    "MEMORY_EVERYONE_DIR",
    "MEMORY_EVERYONE_STORE",
    "MEMORY_EVERYONE_STORE_CONFIG",
    "MEMORY_DISABLED_TOOLS",
)


@pytest.fixture
def registered(monkeypatch):
    extensions = []
    monkeypatch.setattr(vis, "register_extension", extensions.append)
    runpy.run_path(str(ROOT / "extension.py"))
    assert len(extensions) == 1
    return extensions[0]


def activity(method):
    return getattr(Memo, method).__vis_symbol_activity__


def shown(method, result):
    """The summary and the content texts of a successful call."""
    presentation = activity(method).render(phase="success", result=result)
    return presentation.summary, [part.text for part in presentation.content]


def test_registration_exports_nine_typed_methods(registered):
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = tomllib.loads(pyproject)["project"]["version"]
    assert (registered.name, registered.alias) == ("vis-optmem", "memo")
    assert registered.version == version
    assert tuple(registered.env) == ENV
    assert "Victor Taelin" in registered.description
    members = registered.symbols[0].contract["members"]
    assert {member["name"]: member["tag"] for member in members} == {
        f"memo.{name}": tag for name, tag in TAGS.items()
    }
    vis.testing.assert_catalog(
        vis.Catalog(registered.symbols),
        names=[f"memo.{name}" for name in TAGS],
        mutations=[f"memo.{name}" for name, tag in TAGS.items() if tag == "mutation"],
    )


class Host:
    """A host that resolves MEMORY_DIR from its own configuration, as the engine does."""

    def __init__(self, folder):
        self.folder = folder

    def declare_env(self, names_json):
        assert json.loads(names_json) == list(ENV)
        return json.dumps({"MEMORY_DIR": str(self.folder)})


def test_the_host_registration_resolves_memory_dir_and_runs_the_tools(
    monkeypatch, folder, tmp_path
):
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "process"))
    monkeypatch.setattr(vis, "_host", Host(folder))
    monkeypatch.setattr(vis, "_registration", {"spec": None})
    runpy.run_path(str(ROOT / "extension.py"))
    spec = vis._registration["spec"]
    assert (spec["name"], spec["alias"], spec["env"]) == (
        "vis-optmem",
        "memo",
        list(ENV),
    )
    tools = {item["name"]: item["fn"] for item in spec["symbols"][0]["methods"]}
    assert sorted(tools) == sorted(TAGS)
    assert tools["init"]().is_new
    assert tools["note"]("a fact").id == 0
    assert tools["wake"]().lines[0].endswith(" a fact")
    assert (folder / "LOG.txt").exists() and not (tmp_path / "process").exists()


@pytest.mark.parametrize("method", TAGS)
def test_every_activity_shows_the_end_and_keeps_the_error(registered, method):
    declared = activity(method)
    assert declared.label[0].isupper() and "_" not in declared.label
    assert declared.show_start is False
    assert declared.render(phase="start").summary == "Running"
    failure = declared.render(phase="failure", error=ValueError("No summary at #0-1."))
    assert (failure.headline, failure.summary) == (declared.label, "Failed")
    assert [part.text for part in failure.content] == ["No summary at #0-1."]
    blank = declared.render(phase="failure", error=ValueError())
    assert [part.text for part in blank.content] == ["No detail"]
    assert declared.render(phase="success", result=None) is None


def test_init_says_whether_it_created_the_folder(folder):
    tool = Memo(folder)
    created = tool.init()
    assert shown("init", created) == (f"Created {created.path}", [])
    tool.note("a fact")
    assert shown("init", tool.init()) == (f"Found 1 memory in {created.path}", [])


def test_wake_shows_an_empty_memory_a_blocked_read_and_parts(memo):
    assert shown("wake", memo.wake()) == ("No memories yet", [])
    memo.note("first")
    first = memo.wake()
    assert shown("wake", first) == ("1 line for 1 memory", [first.lines[0]])
    memo.note("second")
    assert shown("wake", memo.wake())[0] == "2 lines for 2 memories · summary #0-1 due"
    memo.config({"WAKE_LINES": 2})
    memo.note("third")
    memo.note("fourth")
    blocked = memo.wake()
    assert shown("wake", blocked) == (
        "Needs summary #0-1 first",
        [blocked.request.text],
    )
    part = Wake(
        text="", lines=("a", "b"), part=1, parts=2, at=9, is_awake=False, request=None
    )
    assert shown("wake", part) == ("Part 1 of 2: 2 lines for 9 memories", ["a\nb"])


def test_note_and_nap_show_the_saved_text_and_the_next_request(memo):
    assert shown("note", memo.note("first")) == ("Saved as #0", ["first"])
    second = memo.note("second")
    assert shown("note", second) == ("Saved as #1 · summary #0-1 due", ["second"])
    due = memo.nap()
    assert shown("nap", due) == ("Summary #0-1 due", [due.request.text])
    assert shown("nap", memo.nap("0-1", "both")) == ("Saved #0-1", ["both"])
    assert shown("nap", memo.nap()) == ("Nothing left to summarize", [])


def test_recall_shows_no_match_every_match_and_a_partial_list(memo):
    memo.note("alpha one")
    memo.note("beta two")
    assert shown("recall", memo.recall("gamma")) == ("No match for gamma", [])
    found = memo.recall("alpha")
    assert shown("recall", found) == ("1 match for alpha", [found.matches[0]])
    partial = Recall(text="", pattern="a", matches=("#1 b",), total=2)
    assert shown("recall", partial) == ("2 matches for a, newest 1 shown", ["#1 b"])
    both = Recall("", "a", ("#1 b",), 1, about="b c", related=("#1 b", "#2 c"))
    assert shown("recall", both) == (
        "1 match for a · 2 memories related to b c",
        ["#1 b\n#2 c"],
    )


def test_zoom_and_forget_name_the_block(memo):
    fill(memo, 2)
    opened = memo.zoom("0-1")
    assert shown("zoom", opened) == ("#0-1: 2 halves", [opened.text])
    assert shown("forget", memo.forget("0-1")) == (
        "Dropped 1 summary from #0-1",
        ["#0-1"],
    )


def test_config_lists_the_sizes_and_names_the_changes(memo):
    sizes = memo.config()
    assert shown("config", sizes) == ("4 sizes", [sizes.text])
    changed = memo.config({"WAKE_LINES": 50})
    assert shown("config", changed) == ("Changed WAKE_LINES", [changed.text])


def test_import_counts_the_memories_and_the_summaries_due(memo, tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("2024-01-01 a\n2024-01-02 b\n2024-01-03 c\n", encoding="utf-8")
    imported = memo.import_memories(str(source))
    assert shown("import_memories", imported) == (
        "Imported 3 memories, #0 to #2 · 1 summary due",
        [],
    )


def test_long_content_is_clipped_only_in_the_activity():
    result = Recall(text="", pattern="x", matches=("x" * 5000,), total=1)
    assert shown("recall", result)[1] == ["x" * 4000 + "\n(clipped)"]
    assert len(result.matches[0]) == 5000


def test_prompt_and_context_follow_the_memory_folder(registered, folder, monkeypatch):
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    assert registered.ctx({})["memo"]["personal"]["store"] == "missing"
    assert registered.ctx({})["memo"]["everyone"] == {"store": "unset"}
    assert "one line of at most 280 bytes" in registered.prompt({})
    Memo().init()
    Memo().config({"ENTRY_CHARS": 200})
    assert "one line of at most 200 bytes" in registered.prompt({})
    assert registered.ctx({}) == {
        "memo": {
            "personal": {"memories": 0, "summaries_due": 0, "max_bytes": 200},
            "everyone": {"store": "unset"},
        }
    }


class Workspace(Host):
    """A host with a bound session in `project`."""

    def __init__(self, project):
        super().__init__("memory")
        self.project = project

    def workspace_root(self):
        return str(self.project)


def test_a_relative_memory_dir_is_in_the_session_workspace(monkeypatch, tmp_path):
    # Blockether/vis-optmem#1: a relative MEMORY_DIR started at the gateway directory.
    project = tmp_path / "project"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vis, "_host", Workspace(project))
    monkeypatch.setattr(vis, "_registration", {"spec": None})
    runpy.run_path(str(ROOT / "extension.py"))
    spec = vis._registration["spec"]
    tools = {item["name"]: item["fn"] for item in spec["symbols"][0]["methods"]}
    assert tools["init"]().path == str(project / "memory")
    tools["note"]("a fact")
    assert spec["ctx"]({"cwd": str(project)})["memo"]["personal"] == {
        "memories": 1,
        "summaries_due": 0,
        "max_bytes": 280,
    }
    assert "one line of at most 280 bytes" in spec["prompt"]({"cwd": str(project)})
    assert spec["ctx"]({})["memo"]["personal"]["store"] == "missing"
    assert not (tmp_path / "memory").exists()


def test_the_context_and_the_activity_show_the_memory_for_everyone(
    registered, folder, tmp_path, monkeypatch
):
    monkeypatch.setenv("MEMORY_DIR", str(folder))
    monkeypatch.setenv("MEMORY_EVERYONE_DIR", "team")
    project = tmp_path / "project"
    tool = Memo(base=lambda: project)
    tool.init(scope=EVERYONE)
    saved = tool.note("team fact", scope=EVERYONE)
    assert shown("note", saved) == ("Saved as #0 · everyone", ["team fact"])
    personal = Saved("Saved as #0.", 0, "2024-01-01", "my fact", None)
    assert shown("note", personal) == ("Saved as #0", ["my fact"])
    context = registered.ctx({"cwd": str(project)})["memo"]
    assert context["everyone"] == {"memories": 1, "summaries_due": 0, "max_bytes": 280}
    assert context["personal"]["store"] == "missing"
    assert 'scope="everyone"' in registered.prompt({"cwd": str(project)})
    prompt = registered.prompt({"cwd": str(project)})
    assert "general knowledge that stays useful in other projects" in prompt
    assert (
        'memo.recall(r"postgres|migration", about="change a database schema safely", '
        'scope="all")'
    ) in prompt


def test_the_prompt_leaves_out_the_tools_that_are_turned_off(registered, monkeypatch):
    assert "memo.forget(block" in registered.prompt({})
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "init,import_memories,forget")
    prompt = registered.prompt({})
    assert "memo.forget(block" not in prompt
    assert "memo.note(memory" in prompt
    assert prompt.endswith(
        "- These tools are turned off here: memo.forget, memo.import_memories, "
        "memo.init. Do not call them."
    )
    monkeypatch.setenv("MEMORY_DISABLED_TOOLS", "imports")
    assert registered.prompt({}).endswith(
        "Every memo tool fails until the setting is fixed."
    )
