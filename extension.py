"""Vis entry point. The memory operations live in vis_optmem."""

import blockether.vis.extension as vis

from vis_optmem.memo import Memo, status
from vis_optmem.store import SIZES

CONTENT_LIMIT = 4000
DEFAULT_LIMIT = next(size.default for size in SIZES if size.name == "ENTRY_CHARS")

PROMPT = """memo surface active: permanent memory, shared by every session on this machine.
  memo.wake(part=1, at=None)   read your memory
  memo.note(memory)            save one memory: one line of at most {limit} bytes
  memo.nap(block, summary)     save a summary that memo asks for
  memo.recall(pattern)         search every memory with a regex, word for word
  memo.zoom(block)             open a summary into its two halves
  memo.forget(block)           drop a wrong summary; memo asks for it again
Each result has a `text` field. Print it and do what it says. Find the other tools with apropos(r"^memo\\.").
- Read your memory once in each session, before other work: print((await memo.wake()).text). Continue until it says "You are awake."
- Save a memory for each new fact of lasting value: decisions, results of real work, what the user teaches you, facts about the user's life, events with lasting effect.
- Do not save a memory that you already have.
- When a result asks for a summary, save it with memo.nap() before your next action. session["memo"]["summaries_due"] counts the summaries that are due.
- If session["agent"]["role"] is "subagent", do not use memo.
- Do not change the memory files yourself. memo owns them."""


def _count(number, one, many):
    return f"{number} {one if number == 1 else many}"


def _clip(text):
    return text if len(text) <= CONTENT_LIMIT else text[:CONTENT_LIMIT] + "\n(clipped)"


def _render(label, build):
    """Show `build(result)` on success and the error on failure."""

    def render(*, phase, result=None, error=None, **_):
        if phase == "start":
            return vis.ActivityPresentation(label, "Running")
        if phase == "failure":
            detail = _clip(str(error) or "No detail")
            return vis.ActivityPresentation(
                label, "Failed", (vis.ActivityText(detail),)
            )
        if phase == "success" and result is not None:
            summary, body = build(result)
            content = (vis.ActivityText(_clip(body)),) if body else ()
            return vis.ActivityPresentation(label, summary, content)
        return None

    return render


def _due(request):
    return f" · summary #{request.block} due" if request is not None else ""


def _init(result):
    if result.is_new:
        return f"Created {result.path}", ""
    return f"Found {_count(result.memories, 'memory', 'memories')} in {result.path}", ""


def _wake(result):
    if result.at == 0:
        return "No memories yet", ""
    if not result.lines:
        return f"Needs summary #{result.request.block} first", result.request.text
    lines = _count(len(result.lines), "line", "lines")
    memories = _count(result.at, "memory", "memories")
    where = f"Part {result.part} of {result.parts}: " if result.parts > 1 else ""
    return f"{where}{lines} for {memories}{_due(result.request)}", "\n".join(
        result.lines
    )


def _note(result):
    return f"Saved as #{result.id}{_due(result.request)}", result.memory


def _nap(result):
    if result.saved is not None:
        return f"Saved #{result.saved}{_due(result.request)}", result.summary
    if result.request is not None:
        return f"Summary #{result.request.block} due", result.request.text
    return result.text.splitlines()[0].rstrip("."), ""


def _recall(result):
    if not result.total:
        return f"No match for {result.pattern}", ""
    found = _count(result.total, "match", "matches")
    shown = (
        f", newest {len(result.matches)} shown"
        if len(result.matches) < result.total
        else ""
    )
    return f"{found} for {result.pattern}{shown}", "\n".join(result.matches)


def _zoom(result):
    return (
        f"#{result.block}: {_count(len(result.halves), 'half', 'halves')}",
        result.text,
    )


def _forget(result):
    dropped = _count(len(result.blocks), "summary", "summaries")
    return f"Dropped {dropped} from #{result.blocks[0]}", "\n".join(
        f"#{b}" for b in result.blocks
    )


def _config(result):
    if result.changed:
        return f"Changed {', '.join(result.changed)}", result.text
    return f"{len(result.sizes)} sizes", result.text


def _import(result):
    added = _count(result.last - result.first + 1, "memory", "memories")
    due = f" · {_count(result.due, 'summary', 'summaries')} due" if result.due else ""
    return f"Imported {added}, #{result.first} to #{result.last}{due}", ""


BINDINGS = (
    ("init", "Create memory store", "mutation", _init),
    ("wake", "Read memory", "observation", _wake),
    ("note", "Save memory", "mutation", _note),
    ("nap", "Save memory summary", "mutation", _nap),
    ("recall", "Search memories", "observation", _recall),
    ("zoom", "Open memory summary", "observation", _zoom),
    ("forget", "Drop memory summary", "mutation", _forget),
    ("config", "Configure memory sizes", "mutation", _config),
    ("import_memories", "Import memories", "mutation", _import),
)

for name, label, tag, build in BINDINGS:
    setattr(
        Memo,
        name,
        vis.method(
            tag=tag,
            activity=vis.Activity(
                label=label, show_start=False, render=_render(label, build)
            ),
        )(getattr(Memo, name)),
    )


def _workspace(env):
    """The session workspace from a callback `env`, or None outside a session."""
    cwd = env.get("cwd")
    return (lambda: cwd) if cwd else None


def _prompt(env):
    limit = status(base=_workspace(env)).get("max_bytes", DEFAULT_LIMIT)
    return PROMPT.format(limit=limit)


def _ctx(env):
    return {"memo": status(base=_workspace(env))}


vis.register_extension(
    vis.Extension(
        name="vis-optmem",
        description=(
            "Permanent memory for agents, shared by every session on this machine. "
            "Based on OptMem by Victor Taelin."
        ),
        version="0.1.1",
        alias="memo",
        symbols=[vis.Symbol(Memo(base=vis.workspace_root), name="memo")],
        prompt=_prompt,
        ctx=_ctx,
        env=["MEMORY_DIR"],
    )
)
