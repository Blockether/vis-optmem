# vis-optmem

Give [Vis](https://github.com/Blockether/vis) agents permanent personal or team memory. The extension is based on [OptMem](https://github.com/VictorTaelin/OptMem) by Victor Taelin.

## Install

```bash
vis-agent extension install 'blockether/vis-optmem' --global --trust
```

The first tool call creates the memory folder. You do not need to call `memo.init()` for personal memory.

## Use

Ask the agent in chat to remember a decision or find a fact from an earlier session. Its tools return typed records with a `text` field.

| Tool | Result |
|---|---|
| `memo.init` | Create a memory folder or store. |
| `memo.wake` | Read new memories in full and older memories as summaries. |
| `memo.note` | Save one fact in one line, up to 340 bytes by default. |
| `memo.nap` | Save a summary that a result requests. |
| `memo.recall` | Find original memories by regular expression or related words. |
| `memo.zoom` | Open a summary into its two halves. |
| `memo.forget` | Drop an incorrect summary so the agent can write it again. |
| `memo.config` | Show or change size limits. |
| `memo.import_memories` | Add lines in `YYYY-MM-DD text` format. |

Tools use `scope="personal"` by default. Use `scope="team"` for shared facts. `memo.recall` also accepts `scope="all"` to search both memories. See the [memory guide](docs/memory.md) for summary rules or the [search reference](docs/search.md) for examples.

## Configuration

| Setting | Purpose |
|---|---|
| `MEMORY_DIR` | Personal memory folder. The default is `~/.optmem/memory`. |
| `MEMORY_TEAM_DIR` | Shared team memory folder. There is no default team store. |
| `MEMORY_LANGUAGE`, `MEMORY_TEAM_LANGUAGE` | Language allowed in personal or team memory. |
| `MEMORY_STORE`, `MEMORY_STORE_CONFIG` | Personal store and its options. |
| `MEMORY_TEAM_STORE`, `MEMORY_TEAM_STORE_CONFIG` | Team store and its options. |
| `MEMORY_STORE_TOKEN` | Token for a remote store. |

Use [`memo.config`](docs/memory.md#sizes) to set `WAKE_LINES`, `ENTRY_CHARS`, `PART_CHARS`, or `PART_LINES`. See [Custom stores](docs/custom-stores.md) to use a database or an HTTP service instead of a folder.

## Development

```bash
vis-agent python -m pytest tests -q
```
