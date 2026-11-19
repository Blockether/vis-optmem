# vis-optmem

Permanent memory for [Vis](https://github.com/Blockether/vis) agents. Every session on your
computer reads and writes the same personal memory. So an agent keeps your decisions, the results
of its work and what you teach it from one session to the next. A second memory can keep the facts
that your whole team shares.

vis-optmem brings [OptMem](https://github.com/VictorTaelin/OptMem) by
[Victor Taelin](https://github.com/VictorTaelin) to Vis as typed Python functions. It uses the same
design and the same file layout, with wider records so that a memory holds 340 bytes. See
[Credits](#credits).

## Install

```bash
vis-agent extension install 'blockether/vis-optmem' --global --trust
```

The first tool call creates the memory folder, so you do not need `memo.init()`. After that, each
session reads its memory before other work and saves new facts as it works.

## Tools

```python
memo.init()                          # create the memory folder
memo.wake()                          # read the memory: new memories in full, old ones as summaries
memo.note("The user prefers uv.")    # save one memory: one line of at most 340 bytes
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
   | `matches(pattern, newest)`, optional         | Find memories for `memo.recall(pattern)`        |

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

### Keep the memory in a service

`vis_optmem.remote.RemoteStore` keeps a memory in an HTTP service, for example an AWS Lambda
function. Each store call is one JSON request, so the service does the search and the repeat
check where the memories are. This team memory uses such a service:

```yaml
environment:
  MEMORY_TEAM_STORE: {literal: "vis_optmem.remote:RemoteStore"}
  MEMORY_TEAM_STORE_CONFIG: {literal: '{"url": "https://memory.example.com/", "memory": "team"}'}
  MEMORY_STORE_TOKEN: {env: MEMORY_STORE_TOKEN}
```

To build the service, implement a `MemoryStore` in it and serve it with
`vis_optmem.remote.lambda_response(event, stores, token)`. The module docstring of
`vis_optmem.remote` gives the protocol. `tests/test_remote.py` runs every memo tool through it.

The service refuses what `memo` refuses, also from a client that does not use `memo`: a line
that is too long, a secret, profanity, and with `language="en"` in `answer` or `lambda_response`,
another language. The `note` operation checks for a repeat and saves in one step.

### Keep the memory in Cloudflare

[`cloudflare/`](cloudflare/) is a complete memory service for Cloudflare Workers. It serves
the protocol of `vis_optmem.remote`, so the client side is `RemoteStore` with no other code.

- Each memory is a Durable Object with its own SQLite database. A Durable Object runs one
  write at a time, so each write is atomic without retries.
- Workers AI embeds each memory with `@cf/baai/bge-m3`, a multilingual model. So
  `memo.recall(about=...)` finds memories by meaning, also in other languages.
- `memo.note()` refuses a repeat when two conditions are true: the embeddings are close
  (cosine of at least 0.85), and both lines have the same names. Names are numbers, paths,
  flags, CamelCase and ALL-CAPS words, environment words such as `staging` and `production`,
  and capitalized names. So "port 5432" and "port 5433" stay two memories.
- A password protects the service. The Worker keeps it in the secret `MEMORY_STORE_TOKEN`.
  Each person keeps the same value in the environment variable `MEMORY_STORE_TOKEN`, never in
  `vis.yml`.

To deploy it, you need a Cloudflare account and Node.js. Run these commands in `cloudflare/`:

```bash
npx wrangler login
export MEMORY_STORE_TOKEN="$(openssl rand -base64 32)"   # keep it in your password manager
printf %s "$MEMORY_STORE_TOKEN" | npx wrangler secret put MEMORY_STORE_TOKEN
npx wrangler deploy
```

Wrangler shows the URL of the service, for example `https://vis-optmem.example.workers.dev`.
Give the URL and the password to your team. Each person sets `MEMORY_STORE_TOKEN` in the
shell profile before Vis starts, and the project `vis.yml` selects the service:

```yaml
environment:
  MEMORY_TEAM_STORE: {literal: "vis_optmem.remote:RemoteStore"}
  MEMORY_TEAM_STORE_CONFIG: {literal: '{"url": "https://vis-optmem.example.workers.dev/", "memory": "team"}'}
  MEMORY_STORE_TOKEN: {env: MEMORY_STORE_TOKEN}
```

Then one person creates the memory with `memo.init(scope="team")`.

To change the password, put a new secret with `npx wrangler secret put MEMORY_STORE_TOKEN`
and give the new value to your team. The old value stops at once.

Change these settings in `vars` of `cloudflare/wrangler.jsonc`:

| Name              | Default           | Meaning                                                 |
|-------------------|-------------------|---------------------------------------------------------|
| `MEMORIES`        | `team`            | Memory names that the service keeps, or `*` for all     |
| `EMBEDDING_MODEL` | `@cf/baai/bge-m3` | Workers AI model; a change embeds every memory again    |
| `SEARCH_MIN`      | `0.5`             | Lowest cosine that `memo.recall(about=...)` shows       |
| `DUPLICATE_MIN`   | `0.85`            | Lowest cosine of a repeat that `memo.note()` refuses    |
| `MEMORY_LANGUAGE` | (none)            | Only language of notes, summaries and searches, like `en` |

The Worker refuses the same lines as `memo`: too long, secrets, profanity and other languages.

The thresholds fit `bge-m3`. If you change the model, check them again: see
[Calibrate the thresholds](#calibrate-the-thresholds-for-a-new-embedding-model).

Costs and limits:

- Each note, search and repeat check makes one Workers AI call. Workers AI has a free daily
  allocation, and Cloudflare bills the use above it.
- A Durable Object keeps the vectors of its memory in memory: 4 KB for each memory. So one
  memory holds about 20,000 memories.

To test a change, run `npm test` in `cloudflare/`. To run every memo tool against the Worker,
start `npx wrangler dev` and follow the docstring of `tests/test_cloudflare.py`.

The store runs in the Python environment of vis-optmem. That environment has the standard
library and the packages that vis-optmem declares, not the shared Vis packages. So write the
store with the standard library: `sqlite3` for a local file, or `urllib.request` for an HTTP
service, for example an API in front of your database.

In Python, give the store directly: `Memo(store=MyStore(...))`.

### A memory for many writers on many hosts

`vis_optmem.shared.SharedStore` is a reference `MemoryStore` for a team memory that many sessions on many hosts write at the same time. Copy it or subclass it.

It keeps the memory in a `Table`: sorted string keys, items as dicts, and one call `transact(puts, deletes, conditions)` that writes all or nothing. The conditions are `Absent(key)` and `Equals(key, field, value)`. Two tables come with the package:

- `SqliteTable(path)`: one SQLite file that many processes share (`BEGIN IMMEDIATE`, busy timeout).
- `MemoryTable()`: one process, for tests.

`examples/dynamodb_store.py` gives a `Table` for Amazon DynamoDB (boto3, `TransactWriteItems`).

```bash
MEMORY_STORE=vis_optmem.shared:sqlite_store
MEMORY_STORE_CONFIG={"path": "/shared/team-memory.db"}

MEMORY_STORE=examples/dynamodb_store.py:dynamodb_store
MEMORY_STORE_CONFIG={"table": "team-memory", "memory": "team"}
```

How it works:

- Each instance keeps all memories in a cache. A memory never changes, so `sync()` reads only the memories after the cache, in one query.
- A write gives the next ids, with the condition that those keys are absent. When another writer used them first, the store syncs, checks `note` for a repeat again, and tries again with new ids and a random wait. After 20 s it raises `WriteConflict`. So ids stay dense, and no write is lost or saved twice.
- `put_summary` moves the level count and writes the summary in one transaction. `drop_summaries` lowers the counts and removes the summaries in one transaction.
- `SharedStore(table, embed=fn)` searches by meaning. `fn` gives one vector for each text. A repeat needs a cosine of at least `duplicate_min` (0.85) and the same names (`names.same_names`). Each memory keeps its vector, so other instances do not embed it again.
- `save_snapshot(path)` writes all memories to a JSON file. `SharedStore(table, snapshot=path)` starts from it and syncs only the newer memories.

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

### Calibrate the thresholds for a new embedding model

A store that compares embeddings uses two cosine thresholds:

- `DUPLICATE_MIN` (0.85 for `@cf/baai/bge-m3`): `memo.note()` refuses a repeat at this cosine or more, when both lines also have the same names.
- `SEARCH_MIN` (0.5 for `@cf/baai/bge-m3`): `memo.recall(about=...)` shows a memory at this cosine or more.

Each model gives other cosines. When you change the model, choose both thresholds again. `vis_optmem.calibrate` chooses them from labelled pairs of texts. Each pair has a kind:

| Kind        | The thresholds must                                  |
|-------------|------------------------------------------------------|
| `repeat`    | find the repeat (same fact, other words or language) |
| `different` | not find a repeat (near fact: other port, person)    |
| `related`   | let a search for `a` find `b`                        |
| `unrelated` | not let a search for `a` find `b`                    |

The package has 48 default pairs in English and Polish. Put pairs from your own memory in a JSON lines file, one `{"a": ..., "b": ..., "kind": ...}` on each line. `embed` takes a list of texts and returns one vector for each text.

```bash
# Choose the thresholds. Each one is in the middle of the gap between its two kinds.
python -m vis_optmem.calibrate --embed my_service.embeddings:embed
# Check your thresholds against your pairs. The exit code is 1 when a pair is wrong.
python -m vis_optmem.calibrate pairs.jsonl --embed my_service.embeddings:embed \
    --duplicate-min 0.85 --search-min 0.5
```

The JSON output gives the thresholds, the margins and the pairs that no threshold separates. A negative margin means that the kinds overlap. Add a test, so that a model or threshold change fails at once:

```python
from vis_optmem.calibrate import DEFAULT_PAIRS, check

def test_thresholds_still_separate_the_pairs():
    assert not check(embed, DEFAULT_PAIRS, duplicate_min=0.85, search_min=0.5)
```

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
