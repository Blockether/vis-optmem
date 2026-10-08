# vis-optmem

Permanent memory for [Vis](https://github.com/Blockether/vis) agents. Every session on your
computer reads and writes the same personal memory. So an agent keeps your decisions, the results
of its work and what you teach it from one session to the next. A second memory can keep the facts
that your whole team shares.

vis-optmem brings [OptMem](https://github.com/VictorTaelin/OptMem) by
[Victor Taelin](https://github.com/VictorTaelin) to Vis as typed Python functions. It uses the same
design and the same files. See [Credits](#credits).

## Install

```bash
vis-agent extension install 'blockether/vis-optmem' --global --trust
```

Then ask an agent to create the memory, or call `memo.init()` once yourself. After that, each
session reads its memory before other work and saves new facts as it works.

## Tools

```python
memo.init()                          # create the memory folder
memo.wake()                          # read the memory: new memories in full, old ones as summaries
memo.note("The user prefers uv.")    # save one memory: one line of at most 280 bytes
memo.nap("0-1", "<one line>")        # save a summary that a result asks for
memo.recall(r"uv|poetry")            # search every memory with a regular expression
memo.recall(about="Python packaging") # find related memories by meaning
memo.recall(r"uv", scope="all")      # search both memories in one call
memo.zoom("0-15")                    # open a summary into its two halves
memo.forget("0-15")                  # drop a wrong summary; memo asks for it again
memo.config({"WAKE_LINES": 128})     # show or change the sizes
memo.import_memories("~/old.txt")    # add dated lines, each "YYYY-MM-DD text"
```

Each tool takes `scope="personal"`, the default, or `scope="team"`. `memo.recall()` also
takes `scope="all"`, which searches both memories. See
[Personal and team memory](#personal-and-team-memory).

Each result is a typed record with a `text` field. The agent prints the text and does what it says.
A subagent does not use the memory. Its leader reads and writes it.

### Turn tools off

To stop agents from using some tools, list them in `MEMORY_DISABLED_TOOLS`, separated by commas.
For example, a company memory that an administrator creates and fills needs no `init` and no
`import_memories`:

```yaml
environment:
  MEMORY_DISABLED_TOOLS: {literal: "init,import_memories"}
```

A tool that is turned off refuses each call, and the agent instructions leave it out. The other
tools work as before. A name that is not a memo tool stops every tool, so a typo cannot leave a
tool on. In Python, `Memo(disabled=["init"])` replaces the setting.

## How the memory works

- Each memory is one dated line. A memory never changes.
- Each two neighboring memories get a one-line summary. Then each two neighboring summaries get a
  summary, and so on. The agent writes a summary when a result asks for it.
- `memo.wake()` shows the whole memory in a fixed number of lines, 96 by default. New memories
  show in full. Older periods show as summaries, and the older the period, the larger its summary.
- `memo.zoom()` opens a summary when the agent needs its detail. `memo.recall()` searches the
  original memories.
- `memo.note()` refuses a fact that the memory already holds, because a saved memory never
  changes and `memo.forget()` drops only summaries. The store's `duplicate(text)` decides what a
  repeat is. By default, a note is a repeat when it has the same numbers as one of the closest
  memories from `search` and shares at least 60% of its words. The result shows that memory. To
  save a different fact that looks similar, give `force=True`.

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

The files are the same as those of the OptMem `memo` command, so both tools can use one memory.
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
  [Keep the memory in another place](#keep-the-memory-in-another-place).

Then create it once:

```python
memo.init(scope="team")
memo.note("The team uses uv, not Poetry.", scope="team")
```

In a session, `session["memo"]["team"]` shows the count of team memories, or
`{"store": "unset"}` when no team memory is set. In Python, use
`Memo(team_directory=...)` or `Memo(team_store=...)`.

## Keep the memory in another place

To share one memory between computers or people, keep it in a database or another service
instead of a folder. Write a store for that place, then select it with two settings.

1. Subclass `vis_optmem.MemoryStore` and implement its methods. The memory, its summaries and
   its sizes all go through them:

   | Method                                       | What it does                                    |
   |----------------------------------------------|-------------------------------------------------|
   | `location`                                   | Name the place for people, for example a table  |
   | `exists()`, `create()`                       | Check for the memory, or create it              |
   | `count()`, `entries(lo, hi)`                 | Read the memories                               |
   | `append(items)`                              | Add memories and give their ids                 |
   | `level_count(size)`, `summary(lo, hi)`       | Read the summaries                              |
   | `put_summary(lo, hi, text)`                  | Save the next summary of a level                |
   | `drop_summaries(lo, hi)`                     | Remove a summary and the summaries after it     |
   | `overrides()`, `write_overrides(overrides)`  | Read and write the sizes                        |
   | `search(query, limit)`, optional             | Find related memories for `memo.recall(about=)` |
   | `duplicate(text)`, optional                  | Find the memory that a new note repeats         |

   Make each write atomic, because many sessions write at the same time. The docstrings of
   `MemoryStore` give the exact contract.
2. Set `MEMORY_STORE` to the store, and `MEMORY_STORE_CONFIG` to a JSON object. vis-optmem
   calls the store with the keys of that object as keyword arguments.

[`examples/sqlite_store.py`](examples/sqlite_store.py) is a complete store for one SQL
database. For a database on Amazon RDS, keep its tables and transactions and change the
connection. To use the example as it is, put it in your project and add this to `vis.yml`:

```yaml
environment:
  MEMORY_STORE: {literal: "tools/sqlite_store.py:SqliteStore"}
  MEMORY_STORE_CONFIG: {literal: '{"path": "~/team-memory.db"}'}
```

`MEMORY_STORE` takes one of these forms:

- `path/to/store.py:Name`: a Python file. A relative path is in the session workspace.
- `module:Name`: a module that Python can import.
- `name`: a store that a package installs in the `vis_optmem.stores` entry point group.

`Name` is a `MemoryStore` subclass or a function that returns a store. vis-optmem runs this
code in your Vis session, so select only code that you trust. If the store cannot load or
connect, the session shows the error in `session["memo"]`, and the memo tools explain it.

The store runs in the Python environment of vis-optmem. That environment has the standard
library and the packages that vis-optmem declares, not the shared Vis packages. So write the
store with the standard library: `sqlite3` for a local file, or `urllib.request` for an HTTP
service, for example an API in front of your database.

In Python, give the store directly: `Memo(store=MyStore(...))`.

## Search by meaning

`memo.recall()` searches in two ways, alone or together in one call:

- `pattern`: a regular expression that matches the words of a memory exactly.
- `about`: a query in plain words. The result lists up to 10 related memories, best first.

```python
memo.recall(r"postgres|migration", about="change a database schema safely", scope="all")
```

With `scope="all"`, each line starts with its memory, for example `[team] #3 2025-01-07 ...`,
because both memories number their memories from 0. The related memories take the best of each
memory in turn. If no team memory is set, the search uses only the personal memory and
says so.

The store decides what "related" means. The default `search` ranks memories by the words that
they share with the query, and rare words count more. Words match when their first 6 letters are
the same, so "migrate" finds "migrations". It does not know synonyms or other languages.

For a real search by meaning, override `search(query, limit)` in your store. For example, an
HTTP service on AWS can keep an embedding of each memory and return the nearest ones. The agent
calls the same `memo.recall(about=...)` in both cases.

The default repeat check of `memo.note()` compares words, so it misses the same fact in other
words. To catch those repeats, also override `duplicate(text)`. Return the memory that already
says the fact, or `None` to save the note:

```python
class ServiceStore(MemoryStore):
    def duplicate(self, text: str) -> Entry | None:
        nearest = self.client.nearest(text, limit=1)  # your embedding service
        if nearest and nearest[0].score >= 0.92:
            return nearest[0].entry
        return None
```

## Sizes

Change a size with `memo.config({"NAME": value})`. Use `None` as the value to go back to the
default.

| Name          | Default | Meaning                                           |
|---------------|---------|---------------------------------------------------|
| `WAKE_LINES`  | 96      | Lines that `memo.wake()` shows                    |
| `ENTRY_CHARS` | 280     | Longest memory or summary, in bytes (at most 280) |
| `PART_CHARS`  | 20000   | Largest part of one result, in bytes              |
| `PART_LINES`  | 500     | Largest part of one result, in lines              |

In Vis, one part holds at most 8000 bytes, so that it fits into one print.

## Development

```bash
vis-agent python -m pytest tests -q
```

## Credits

The design and the file format come from [OptMem](https://github.com/VictorTaelin/OptMem) by
[Victor Taelin](https://github.com/VictorTaelin): the dated memory log, the tree of summaries and
the fixed-size view. vis-optmem is an independent Python implementation for Vis. It contains no
OptMem code. See [NOTICE](NOTICE).

## License

Apache License 2.0. See [LICENSE](LICENSE).
