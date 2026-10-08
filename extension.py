"""Vis entry point. The memory operations live in vis_optmem."""

import blockether.vis.extension as vis

from vis_optmem.memo import EVERYONE, NAMESPACE, Memo, disabled_tools, status
from vis_optmem.store import SIZES

CONTENT_LIMIT = 4000
DEFAULT_LIMIT = next(size.default for size in SIZES if size.name == "ENTRY_CHARS")

PROMPT = """memo surface active: permanent memory in two memories.
- scope="personal", the default: your own memory, shared by your sessions.
- scope="everyone": shared knowledge for every person and every project: general lessons that stay useful later.
  memo.wake(part=1, at=None, scope="personal")   read a memory
  memo.note(memory, scope="personal")            save one memory: one line of at most {limit} bytes
  memo.nap(block, summary, scope="personal")     save a summary that memo asks for
  memo.recall(pattern, about=None, scope="personal")   search memories: a regex word for word, about="<plain words>" by meaning, or both
  memo.zoom(block, scope="personal")             open a summary into its two halves
  memo.forget(block, scope="personal")           drop a wrong summary; memo asks for it again
Each result has a `text` field. Print it and do what it says. Find the other tools with apropos(r"^memo\\.").
- Read your memory once in each session, before other work: print((await memo.wake()).text). Continue until it says "You are awake."
- Save a memory for each new fact of lasting value: decisions, results of real work, what the user teaches you, facts about the user's life, events with lasting effect.
- Save to scope="everyone" general knowledge that stays useful in other projects: best practices, reusable solutions, pitfalls with their fixes, team conventions. Every person must be allowed to read it.
- Keep project details, facts about the user, preferences and private data in the personal memory.
- Write each memory for everyone so that it stands alone and a search finds it: name the technology, the problem and the solution in plain words. Leave out local paths and session details.
- Before you plan a task or choose a tool, search both memories for it, for example memo.recall(r"postgres|migration", about="change a database schema safely", scope="everyone").
- Never save passwords, keys or other secrets in either memory.
- Do not save a memory that you already have.
- When a result asks for a summary, save it with the call in its text, before your next action. The call names the memory. session["memo"]["personal"]["summaries_due"] and session["memo"]["everyone"]["summaries_due"] count the summaries that are due.
- If session["memo"]["everyone"]["store"] is "unset", there is no memory for everyone. Do not use scope="everyone".
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
            if getattr(result, "scope", None) == EVERYONE:
                summary += " · everyone"
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
    parts = []
    if result.pattern is not None:
        if not result.total:
            parts.append(f"No match for {result.pattern}")
        else:
            found = _count(result.total, "match", "matches")
            shown = (
                f", newest {len(result.matches)} shown"
                if len(result.matches) < result.total
                else ""
            )
            parts.append(f"{found} for {result.pattern}{shown}")
    if result.about is not None:
        related = _count(len(result.related), "memory", "memories")
        parts.append(f"{related} related to {result.about}")
    extra = [line for line in result.related if line not in result.matches]
    return " · ".join(parts), "\n".join([*result.matches, *extra])


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
    text = PROMPT.format(limit=limit)
    try:
        off = disabled_tools()
    except ValueError as error:
        return f"{text}\n- {error} Every memo tool fails until the setting is fixed."
    if not off:
        return text
    kept = [
        line
        for line in text.splitlines()
        if not any(line.startswith(f"  {NAMESPACE}.{tool}(") for tool in off)
    ]
    names = ", ".join(f"{NAMESPACE}.{tool}" for tool in sorted(off))
    kept.append(f"- These tools are turned off here: {names}. Do not call them.")
    return "\n".join(kept)


def _ctx(env):
    base = _workspace(env)
    return {
        "memo": {
            "personal": status(base=base),
            "everyone": status(base=base, scope=EVERYONE),
        }
    }


vis.register_extension(
    vis.Extension(
        name="vis-optmem",
        description=(
            "Permanent memory for agents: a personal memory for every session on this "
            "machine, and a memory that everyone shares. Based on OptMem by Victor Taelin."
        ),
        version="0.2.0",
        alias="memo",
        symbols=[vis.Symbol(Memo(base=vis.workspace_root), name="memo")],
        prompt=_prompt,
        ctx=_ctx,
        env=[
            "MEMORY_DIR",
            "MEMORY_STORE",
            "MEMORY_STORE_CONFIG",
            "MEMORY_EVERYONE_DIR",
            "MEMORY_EVERYONE_STORE",
            "MEMORY_EVERYONE_STORE_CONFIG",
            "MEMORY_DISABLED_TOOLS",
        ],
    )
)
