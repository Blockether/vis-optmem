# Custom stores

## When to use

Use this guide to keep your memory in a database or an HTTP service. For the default
file store and team memory settings, see the [memory guide](memory.md).

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

[`examples/sqlite_store.py`](../examples/sqlite_store.py) is a complete store for one SQL
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

[`cloudflare/`](../cloudflare/) is a complete memory service for Cloudflare Workers. It serves
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
[Calibrate the thresholds](search.md#calibrate-the-thresholds-for-a-new-embedding-model).

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
MEMORY_STORE_CONFIG='{"path": "/shared/team-memory.db"}'

MEMORY_STORE=examples/dynamodb_store.py:dynamodb_store
MEMORY_STORE_CONFIG='{"table": "team-memory", "memory": "team"}'
```

How it works:

- Each instance keeps all memories in a cache. A memory never changes, so `sync()` reads only the memories after the cache, in one query.
- A write gives the next ids, with the condition that those keys are absent. When another writer used them first, the store syncs, checks `note` for a repeat again, and tries again with new ids and a random wait. After 20 s it raises `WriteConflict`. So ids stay dense, and no write is lost or saved twice.
- `put_summary` moves the level count and writes the summary in one transaction. `drop_summaries` lowers the counts and removes the summaries in one transaction.
- `SharedStore(table, embed=fn)` searches by meaning. `fn` gives one vector for each text. A repeat needs a cosine of at least `duplicate_min` (0.85) and the same names (`names.same_names`). Each memory keeps its vector, so other instances do not embed it again.
- `save_snapshot(path)` writes all memories to a JSON file. `SharedStore(table, snapshot=path)` starts from it and syncs only the newer memories.
