# vis-optmem

Permanent memory for [Vis](https://github.com/Blockether/vis) agents. Every session on your
computer reads and writes the same memory. So an agent keeps your decisions, the results of its
work and what you teach it from one session to the next.

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
memo.zoom("0-15")                    # open a summary into its two halves
memo.forget("0-15")                  # drop a wrong summary; memo asks for it again
memo.config({"WAKE_LINES": 128})     # show or change the sizes
memo.import_memories("~/old.txt")    # add dated lines, each "YYYY-MM-DD text"
```

Each result is a typed record with a `text` field. The agent prints the text and does what it says.
A subagent does not use the memory. Its leader reads and writes it.

## How the memory works

- Each memory is one dated line. A memory never changes.
- Each two neighboring memories get a one-line summary. Then each two neighboring summaries get a
  summary, and so on. The agent writes a summary when a result asks for it.
- `memo.wake()` shows the whole memory in a fixed number of lines, 96 by default. New memories
  show in full. Older periods show as summaries, and the older the period, the larger its summary.
- `memo.zoom()` opens a summary when the agent needs its detail. `memo.recall()` searches the
  original memories.

## Where the memory is

The memory folder is `$MEMORY_DIR`, else `~/.optmem/memory`. To use another folder, set
`MEMORY_DIR` before you start Vis.

The files are the same as those of the OptMem `memo` command, so both tools can use one memory.
A lock file keeps the writes of all sessions and processes in order. Do not edit the files by hand.

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
