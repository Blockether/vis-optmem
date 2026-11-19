# vis-optmem

Permanent agent memory in the OptMem format, as typed Python functions for Vis.

- `src/vis_optmem/` is ordinary Python. Only `extension.py` mentions Vis.
- Keep the file layout of the OptMem `memo` command (`config`, `.lock`, log and summary records) with one change: the records are wider (`LOG_RECORD` 384, `TREE_RECORD` 352), so a memory holds 340 bytes. `FileStore` reads OptMem widths and makes them wide under the lock. Test a format change in `tests/test_store.py`.
- `tests/fixtures/names.json` and `tests/fixtures/guard.json` keep `names.py` and `guard.py` the same as `cloudflare/src/protocol.js` and `cloudflare/src/guard.js`. Change both sides and the fixture together.
- Write memory files only through `FileStore`, under its lock.
- `MemoryStore` is the public interface for other places. `Memo` uses only its methods. Keep `examples/sqlite_store.py` giving the same results as `FileStore` in `tests/test_custom_store.py`.
- `cloudflare/` serves the protocol of `remote.py` on Cloudflare. Keep its messages the same as `remote.py`, and keep `tests/test_cloudflare.py` passing against `npx wrangler dev`. Test `cloudflare/src/protocol.js` with `npm test` in `cloudflare/`.
- `cover.py` must choose the same blocks as OptMem for every memory count and line budget. Keep its tests.
- Every result has a `text` field that tells the agent the next step. Keep each `text` within one Vis print.
- Give every exported method an explicit Activity presentation with a capitalized English label. Test the success, failure and empty states.
- Do not copy code or text from OptMem: it has no license. Credit Victor Taelin in the README, the NOTICE and the extension description.
- Format and lint with ruff. Run tests with `vis-agent python -m pytest tests -q`.
