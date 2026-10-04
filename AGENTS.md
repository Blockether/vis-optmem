# vis-optmem

Permanent agent memory in the OptMem format, as typed Python functions for Vis.

- `src/vis_optmem/` is ordinary Python. Only `extension.py` mentions Vis.
- Keep the files byte-compatible with the OptMem `memo` command: the log and summary records, the `config` file and the `.lock` file. Test a format change against that command.
- Write memory files only through `Store`, under its lock.
- `cover.py` must choose the same blocks as OptMem for every memory count and line budget. Keep its tests.
- Every result has a `text` field that tells the agent the next step. Keep each `text` within one Vis print.
- Give every exported method an explicit Activity presentation with a capitalized English label. Test the success, failure and empty states.
- Do not copy code or text from OptMem: it has no license. Credit Victor Taelin in the README, the NOTICE and the extension description.
- Format and lint with ruff. Run tests with `vis-agent python -m pytest tests -q`.
