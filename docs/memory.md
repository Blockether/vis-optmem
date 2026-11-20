# Memory guide

## When to use

Use this guide to [choose where the memory lives](#where-the-memory-is),
[set up team memory](#personal-and-team-memory), or [change the size limits](#sizes).
To keep memories in a service or database, see [Custom stores](custom-stores.md).

## Turn tools off

To stop agents from using some memo tools, turn them off in the Vis tool lists. For example, a
company memory that an administrator creates and fills needs no `init` and no `import_memories`:

```yaml
# vis.yml
tools:
  deny:
    - memo.init
    - memo.import_memories
```

Vis removes a tool that is off from the session and refuses each call to it. You can also set the
lists in the project, group or session settings of Vis.

## How the memory works

A leader uses the memory. Its subagents do not read or write it.

- Each memory is one dated line. A memory never changes.
- Each two neighboring memories get a one-line summary. Then each two neighboring summaries get a
  summary, and so on. The agent writes a summary when a result asks for it.
- `memo.wake()` shows the whole memory in a fixed number of lines, 96 by default. New memories
  show in full. Older periods show as summaries, and the older the period, the larger its summary.
- `memo.zoom()` opens a summary when the agent needs its detail. `memo.recall()` searches the
  original memories.
- `memo.note()` refuses a fact that the memory already holds, because a saved memory never
  changes and `memo.forget()` drops only summaries. The store's `duplicate(text)` decides what a
  repeat is. By default, a note is a repeat when it has the same names as one of the closest
  memories from `search` and shares at least 60% of its words. Names are numbers, paths, file
  names, flags, camelCase and ALL-CAPS tokens, environment words and proper names
  (`vis_optmem.names`). So "port 5432" and "port 5433", or staging and production, are different
  facts. The result shows that memory. To save a different fact that looks similar, give
  `force=True`. The store's `note()` checks and saves in one atomic step, so two sessions that note
  one fact at the same time save it once.
- `memo.note()`, `memo.nap()` and `memo.import_memories()` refuse a line that seems to hold a
  secret or has profanity, in every memory and every store. The secret rules find private keys,
  JSON Web Tokens, AWS, GitHub, GitLab, Slack, Google, OpenAI, Anthropic, Stripe, npm and Hugging
  Face tokens, `Bearer` and `Basic` values, tokens and passwords in URLs, and random values after
  words like `password=` or `token:`. The error names the kind of problem and does not repeat the
  value. `force=True` does not skip this check.
- `MEMORY_LANGUAGE` (personal memory) and `MEMORY_TEAM_LANGUAGE` (team memory) give the only
  language that a memory keeps: `en`, `pl`, `de`, `fr`, `es`, `it`, `pt` or `nl`. Then notes,
  summaries and `about` searches in another language are refused. A short check of common words
  decides; capitalized words count as names, so `Kraków` in an English note is allowed.
- A memory or summary that is at most 2 bytes over `ENTRY_CHARS` is cut to the limit, and the
  result shows the saved line. A longer line is refused.
- The agent prompt asks for memories and searches in English and in professional language, with
  the task key, the environment, the repository and the short commit SHA when they apply.

## Where the memory is

The memory folder is `$MEMORY_DIR`, else `~/.optmem/memory`. To use another folder, set
`MEMORY_DIR` before you start Vis.

A relative `MEMORY_DIR` is a folder in the session workspace. To keep the memory in the
project, put it in the shared project `vis.yml`:

```yaml
environment:
  MEMORY_DIR: {literal: "memory"}
```

Each person then gets `<workspace>/memory`. An absolute path or a `~` path does not change.

The files have the layout of the OptMem `memo` command, with wider records: 384 bytes for a memory
and 352 bytes for a summary, instead of 320 and 288. A folder with the OptMem
widths makes its records wide. vis-optmem does it at the first use, so the OptMem `memo` command
cannot read that folder after it.
A lock file keeps the writes of all sessions and processes in order. Do not edit the files by hand.

## Personal and team memory

vis-optmem has two memories:

- **personal**: your own memory. The tools use it when you give no `scope`.
- **team**: one memory that every person on your team shares. It keeps general knowledge that
  stays useful in other projects: best practices, reusable solutions, pitfalls with their fixes and
  team conventions.

The two memories never mix. Each has its own memories and its own summaries, so a summary of the
team memory never contains a personal note. The agent saves to the team memory only facts that
every person may read, and keeps project details and facts about you personal. It writes each
team memory so that it stands alone and a search finds it.

The agent reads only the personal memory at the start of a session. It does not read the team
memory: before it plans a task, it searches both memories with `memo.recall(scope="all")`. So a
large team memory costs no tokens at the start. For the same reason, a team note does not ask
the agent for a summary. Summaries matter only for `memo.wake()` and `memo.zoom()`. If you want
them for the team memory, let a scheduled job write them with `memo.nap(scope="team")`.

The team memory has no default place. To enable it, set one of these before you start Vis:

- `MEMORY_TEAM_DIR`: a folder that every person reaches. A relative path is in the session
  workspace. It must not be the personal memory folder.
- `MEMORY_TEAM_STORE` and `MEMORY_TEAM_STORE_CONFIG`: a custom store, as in
  [Keep the memory in another place](custom-stores.md#keep-the-memory-in-another-place).

Then create it once:

```python
memo.init(scope="team")
memo.note("The team uses uv, not Poetry.", scope="team")
```

In a session, `session["memo"]["team"]` shows the count of team memories, or
`{"store": "unset"}` when no team memory is set. In Python, use
`Memo(team_directory=...)` or `Memo(team_store=...)`.

## Sizes

Change a size with `memo.config({"NAME": value})`. Use `None` as the value to go back to the
default.

| Name          | Default | Meaning                                           |
|---------------|---------|---------------------------------------------------|
| `WAKE_LINES`  | 96      | Lines that `memo.wake()` shows                    |
| `ENTRY_CHARS` | 340     | Longest memory or summary, in bytes (at most 344) |
| `PART_CHARS`  | 20000   | Largest part of one result, in bytes              |
| `PART_LINES`  | 500     | Largest part of one result, in lines              |

In Vis, one part holds at most 8000 bytes, so that it fits into one print.

## Credits

The design and the file format come from [OptMem](https://github.com/VictorTaelin/OptMem) by
[Victor Taelin](https://github.com/VictorTaelin): the dated memory log, the tree of summaries and
the fixed-size view. vis-optmem is an independent Python implementation for Vis. It contains no
OptMem code. See [NOTICE](../NOTICE).

## License

Apache License 2.0. See [LICENSE](../LICENSE).
