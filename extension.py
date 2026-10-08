"""Vis entry point. The memory operations live in vis_optmem."""

import blockether.vis.extension as vis

from vis_optmem.memo import NAMESPACE, TEAM, Memo, disabled_tools, status
from vis_optmem.store import SIZES

CONTENT_LIMIT = 4000
DEFAULT_LIMIT = next(size.default for size in SIZES if size.name == "ENTRY_CHARS")

PROMPT = """memo surface active: permanent memory that lasts across sessions, in two memories.
- "personal", the default: your own memory. Facts about the user, preferences and project details.
- "team": one memory that the whole team shares, for search only. General knowledge that stays useful in other projects: best practices, reusable solutions, pitfalls with their fixes, team conventions.
Each tool takes scope="personal" (the default) or scope="team". memo.recall also takes scope="all": it searches both memories, and each line starts with its memory, like [team].
  memo.wake(part=1, at=None)              read a memory
  memo.note(memory)                       save one fact: one line of at most {limit} bytes
  memo.nap(block, summary)                save a summary that a result asks for
  memo.recall(pattern=None, about=None)   search: pattern is a regex, about is plain words matched by meaning; give one or both
  memo.zoom(block)                        open a summary into its two halves
  memo.forget(block)                      drop a wrong summary; memo asks for it again
Each result has a `text` field. Print it and do what it says. apropos(r"^memo\\.") lists the other tools.
- At the start of each session, before other work, read your memory: print((await memo.wake()).text). Run each "Next:" call that a result gives until a result says "You are awake." Do not read the team memory with memo.wake: search it.
- Before you plan a task or choose a tool, search both memories in one call, for example memo.recall(r"postgres|migration", about="change a database schema safely", scope="all").
- Save each new fact of lasting value when you learn it: decisions, results of real work, what the user teaches you, facts about the user's life, events with lasting effect.
- Save to scope="team" only general knowledge that stays useful in other projects and that every person may read. Write it so that it stands alone and a search finds it: name the technology, the problem and the solution in plain words, without local paths or session details.
- Save each fact once, in one memory. Memories never change and memo.forget drops only summaries, so memo.note refuses a fact that a memory already says. Then save only what is new. Give force=True only for a different fact.
- Never save passwords, keys or other secrets in either memory.
- When a result asks for a summary, save it with the call in that result, before your next action. session["memo"]["personal"]["summaries_due"] counts the summaries that are due.
- If session["memo"]["team"]["store"] is "unset", there is no team memory: use only the personal memory.
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
            if getattr(result, "scope", None) == TEAM:
                summary += " · team"
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
            "team": status(base=base, scope=TEAM),
        }
    }


vis.register_extension(
    vis.Extension(
        name="vis-optmem",
        description=(
            "Permanent memory for agents: a personal memory for every session on this "
            "machine, and a team memory that agents search. Based on OptMem by Victor Taelin."
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
            "MEMORY_TEAM_DIR",
            "MEMORY_TEAM_STORE",
            "MEMORY_TEAM_STORE_CONFIG",
            "MEMORY_DISABLED_TOOLS",
            "MEMORY_STORE_TOKEN",
        ],
    )
)
