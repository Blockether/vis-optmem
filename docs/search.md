# Memory search reference

## When to use

Use this reference to combine exact matches and related memories in one search, or to
choose thresholds for a store that compares embeddings. To select a store, see
[Custom stores](custom-stores.md).

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
