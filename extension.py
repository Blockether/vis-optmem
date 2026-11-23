"""Vis entry point. The memory operations live in vis_optmem."""

import blockether.vis.extension as vis

from vis_optmem.memo import TEAM, Memo, MemoryNotSet, status
from vis_optmem.store import SIZES

CONTENT_LIMIT = 4000
DEFAULT_LIMIT = next(size.default for size in SIZES if size.name == "ENTRY_CHARS")

PROMPT = """Memo decisions. Never edit memory files. Print each result.text and follow it.
START: If session["agent"]["role"] == "subagent", stop. Do not use memo. Otherwise run print((await memo.wake()).text) before work. Follow Next until awake. Never wake team memory.
PLAN or choose tools: search first. Example: memo.recall(r"postgres|migration", about="change a database schema safely", scope="all").
NEW lasting fact: Check `callable(getattr(memo, "note", None))`. If false, do not save new facts or bypass the limit. Otherwise choose one memory:
- Personal by default: memo.note(memory) for user facts, preferences, project decisions and results.
{team}
WRITE: One fact once, one line of at most {limit} bytes. For work, include the task key, the environment, the repository and the short commit SHA (7 characters) when applicable. On a duplicate, save only new information. Use force=True only for a different fact.
READ-ONLY: If the store refuses a write as read-only, read or search only. Do not retry or redirect the write.
SUMMARY due: Run the requested memo.nap call before other work.
ALWAYS: Write in English and in professional language: translate memory, summary, pattern and about. Never save secrets. memo.note and memo.nap refuse a line that looks like a secret or has profanity. Omit the value.
Other tools: apropos(r"^memo\\.")."""

TEAM_SET = '- Team: memo.note(memory, scope="team") only for general knowledge that stays useful in other projects and is safe for everyone. Name the technology, problem and fix. Omit local paths and session details.'
TEAM_UNSET = '- "team": no team memory is set. Use personal only.'


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


def _team_is_set(base):
    """Whether a folder or a store is set for the team memory. Opens no connection."""
    try:
        Memo(base=base)._store(TEAM)
    except MemoryNotSet:
        return False
    except Exception:
        pass  # A broken setting is still set; the session context shows its error.
    return True


def _prompt(env):
    base = _workspace(env)
    limit = status(base=base).get("max_bytes", DEFAULT_LIMIT)
    team = TEAM_SET if _team_is_set(base) else TEAM_UNSET
    return PROMPT.format(limit=limit, team=team)


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
            "machine, and a team memory that agents search and add to. Based on OptMem by Victor Taelin."
        ),
        version="0.7.3",
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
            "MEMORY_STORE_TOKEN",
        ],
    )
)
