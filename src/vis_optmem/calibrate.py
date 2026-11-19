"""Calibrate the repeat and search thresholds of an embedding model.

A store that compares embeddings needs two cosine thresholds:

- ``duplicate_min``: ``memo.note`` refuses a repeat at this cosine or more, when
  both lines also have the same names (``vis_optmem.names.same_names``);
- ``search_min``: ``memo.recall(about=...)`` shows a memory at this cosine or more.

Each model puts texts at other distances. When a team changes the model, it must
choose both thresholds again. ``calibrate`` chooses them from labelled pairs, and
``check`` gives the pairs that two thresholds decide wrongly. Assert ``not check(...)``
in a test, so a model or threshold change fails at once::

    python -m vis_optmem.calibrate [PAIRS] --embed module:function \
        [--duplicate-min X --search-min Y]

``embed`` takes a list of texts and returns one vector for each text.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vis_optmem.names import same_names

Embed = Callable[[list[str]], Sequence[Sequence[float]]]
"""Gives one vector for each text, in the same sequence."""

KINDS = ("repeat", "different", "related", "unrelated")
"""``repeat``: the repeat check must find it. ``different``: it must not.
``related``: search for ``a`` must find ``b``. ``unrelated``: it must not."""

DUPLICATE_MIN = 0.85
SEARCH_MIN = 0.5
"""The thresholds of ``cloudflare/`` for ``@cf/baai/bge-m3``."""


@dataclass(frozen=True, slots=True)
class Pair:
    """Two texts and the decision that the thresholds must give for them."""

    a: str
    b: str
    kind: str

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(
                f"kind must be one of {', '.join(KINDS)}, not {self.kind!r}"
            )


@dataclass(frozen=True, slots=True)
class Score:
    """A pair with the cosine of its embeddings and the result of ``same_names``."""

    pair: Pair
    cosine: float
    same_names: bool

    def to_dict(self) -> dict:
        return {
            "a": self.pair.a,
            "b": self.pair.b,
            "kind": self.pair.kind,
            "cosine": round(self.cosine, 6),
            "same_names": self.same_names,
        }


@dataclass(frozen=True, slots=True)
class Calibration:
    """The chosen thresholds, each in the middle of the gap between its two kinds.

    A margin is the distance from a threshold to the nearest pair. It is negative
    when no threshold separates the kinds. ``unseparated`` gives those pairs, and
    each ``repeat`` pair with other names, which no cosine can find.
    """

    scores: tuple[Score, ...]
    duplicate_min: float
    search_min: float
    duplicate_margin: float
    search_margin: float
    unseparated: tuple[Score, ...]

    def to_dict(self) -> dict:
        return {
            "duplicate_min": round(self.duplicate_min, 6),
            "search_min": round(self.search_min, 6),
            "duplicate_margin": round(self.duplicate_margin, 6),
            "search_margin": round(self.search_margin, 6),
            "unseparated": [s.to_dict() for s in self.unseparated],
            "scores": [s.to_dict() for s in self.scores],
        }


def load_pairs(path: str | Path) -> list[Pair]:
    """The pairs in a JSON lines file: one ``{"a": ..., "b": ..., "kind": ...}`` on each line."""
    pairs = []
    for number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            pairs.append(Pair(row["a"], row["b"], row["kind"]))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"{path}:{number}: not a pair: {error}") from error
    return pairs


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / norm if norm else 0.0


def scores(embed: Embed, pairs: Iterable[Pair]) -> list[Score]:
    """The score of each pair. ``embed`` gets each text one time, in one call."""
    pairs = list(pairs)
    texts = list(dict.fromkeys(text for pair in pairs for text in (pair.a, pair.b)))
    vectors = list(embed(texts)) if texts else []
    if len(vectors) != len(texts):
        raise ValueError(f"embed gave {len(vectors)} vectors for {len(texts)} texts")
    by_text = dict(zip(texts, vectors))
    return [
        Score(
            pair, _cosine(by_text[pair.a], by_text[pair.b]), same_names(pair.a, pair.b)
        )
        for pair in pairs
    ]


def _split(
    found: list[Score], missed: list[Score], what: str
) -> tuple[float, float, list[Score]]:
    """The threshold in the middle of the gap, its margin and the pairs in the overlap."""
    if not found or not missed:
        raise ValueError(f"calibration needs {what}")
    low = min(s.cosine for s in found)
    high = max(s.cosine for s in missed)
    overlap = [s for s in found if s.cosine <= high] + [
        s for s in missed if s.cosine >= low
    ]
    return (low + high) / 2, (low - high) / 2, overlap if low <= high else []


def calibrate(embed: Embed, pairs: Iterable[Pair]) -> Calibration:
    """Choose ``duplicate_min`` and ``search_min`` from labelled pairs.

    A ``different`` pair with other names is correct at any cosine, so only a
    ``different`` pair with the same names limits ``duplicate_min``.
    """
    scored = scores(embed, pairs)

    def kind(name: str) -> list[Score]:
        return [s for s in scored if s.pair.kind == name]

    no_names = [s for s in kind("repeat") if not s.same_names]
    duplicate_min, duplicate_margin, duplicate_overlap = _split(
        [s for s in kind("repeat") if s.same_names],
        [s for s in kind("different") if s.same_names],
        "a repeat pair and a different pair, both with the same names",
    )
    search_min, search_margin, search_overlap = _split(
        kind("related"), kind("unrelated"), "a related pair and an unrelated pair"
    )
    return Calibration(
        tuple(scored),
        duplicate_min,
        search_min,
        duplicate_margin,
        search_margin,
        tuple(no_names + duplicate_overlap + search_overlap),
    )


def problems(
    scored: Iterable[Score], duplicate_min: float, search_min: float
) -> list[str]:
    """The pairs that the two thresholds decide wrongly, one line for each."""
    found = []
    for s in scored:
        kind, cosine = s.pair.kind, s.cosine
        if kind == "repeat" and not s.same_names:
            problem = "repeat not found: other names"
        elif kind == "repeat" and cosine < duplicate_min:
            problem = f"repeat not found: cosine {cosine:.4f} < {duplicate_min:.4f}"
        elif kind == "different" and s.same_names and cosine >= duplicate_min:
            problem = f"different seen as a repeat: cosine {cosine:.4f} >= {duplicate_min:.4f}"
        elif kind == "related" and cosine < search_min:
            problem = f"related not found: cosine {cosine:.4f} < {search_min:.4f}"
        elif kind == "unrelated" and cosine >= search_min:
            problem = f"unrelated found: cosine {cosine:.4f} >= {search_min:.4f}"
        else:
            continue
        found.append(f"{problem}: {s.pair.a!r} | {s.pair.b!r}")
    return found


def check(
    embed: Embed, pairs: Iterable[Pair], duplicate_min: float, search_min: float
) -> list[str]:
    """The problems of two thresholds for ``pairs``. Empty when both separate all pairs."""
    return problems(scores(embed, pairs), duplicate_min, search_min)


def _load_embed(spec: str) -> Embed:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise ValueError(f"--embed must be module:function, not {spec!r}")
    return getattr(importlib.import_module(module), name)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the calibration as JSON. Exit 1 when the thresholds have problems."""
    parser = argparse.ArgumentParser(
        prog="python -m vis_optmem.calibrate", description=main.__doc__
    )
    parser.add_argument(
        "pairs", nargs="?", help="JSON lines file; default: DEFAULT_PAIRS"
    )
    parser.add_argument(
        "--embed", required=True, help="module:function that embeds texts"
    )
    parser.add_argument(
        "--duplicate-min", type=float, help="check this repeat threshold"
    )
    parser.add_argument("--search-min", type=float, help="check this search threshold")
    args = parser.parse_args(argv)
    if (args.duplicate_min is None) != (args.search_min is None):
        parser.error("give --duplicate-min and --search-min together")
    try:
        pairs = load_pairs(args.pairs) if args.pairs else list(DEFAULT_PAIRS)
        result = calibrate(_load_embed(args.embed), pairs)
    except (ValueError, OSError, ImportError, AttributeError) as error:
        print(f"calibrate: {error}", file=sys.stderr)
        return 2
    output = result.to_dict()
    if args.duplicate_min is None:
        duplicate_min, search_min = result.duplicate_min, result.search_min
    else:
        duplicate_min, search_min = args.duplicate_min, args.search_min
        output["checked"] = {"duplicate_min": duplicate_min, "search_min": search_min}
    output["problems"] = problems(result.scores, duplicate_min, search_min)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if output["problems"] else 0


def _pairs(kind: str, rows: Sequence[tuple[str, str]]) -> tuple[Pair, ...]:
    return tuple(Pair(a, b, kind) for a, b in rows)


_DB = "The staging database listens on port 5432."
_DB_PL = "Baza danych na staging nasłuchuje na porcie 5432."
_TESTS = "Run the tests with vis-agent python -m pytest tests -q."
_OWNER = "The billing service belongs to Anna."
_OWNER_PL = "Za serwis billingowy odpowiada Anna."
_PYTHON = "This project needs Python 3.12."
_BACKUP = "The nightly backup runs at 02:00 UTC."
_LIMIT = "We allow at most 100 API requests per minute."
_SECRETS = "Klucze API trzymamy w Vault, nigdy w repozytorium."
_MEETING = "Zespół spotyka się co poniedziałek o 10:00."
_RUFF = "Ruff formats and lints the Python code."
_DEPLOY = "Production deploys require two reviewer approvals."
_LOGS = "Logi aplikacji trzymamy przez 30 dni."

DEFAULT_PAIRS: tuple[Pair, ...] = (
    *_pairs(
        "repeat",
        [
            (_DB, "Port 5432 is where the staging database listens."),
            (_DB, _DB_PL),
            (_TESTS, "To run the tests, use vis-agent python -m pytest tests -q."),
            (_OWNER, "Anna owns the billing service."),
            (_OWNER_PL, "Anna jest właścicielką serwisu billingowego."),
            (_DEPLOY, "The deploy to production needs approval from two reviewers."),
            (_PYTHON, "Use Python 3.12 for this project."),
            (_SECRETS, "Nigdy nie trzymamy kluczy API w repozytorium, tylko w Vault."),
            (_BACKUP, "Backups run every night at 02:00 UTC."),
            (_RUFF, "The Python code is formatted and linted with Ruff."),
            ("Spotkanie zespołu jest w każdy poniedziałek o 10:00.", _MEETING),
            ("The API rate limit is 100 requests per minute.", _LIMIT),
            (
                "Cache entries expire after 15 minutes.",
                "Wpisy w cache wygasają po 15 minutach.",
            ),
        ],
    ),
    *_pairs(
        "different",
        [
            (_DB, "The staging database listens on port 5433."),
            (_DB, "The production database listens on port 5432."),
            (_DB_PL, "Baza danych na produkcji nasłuchuje na porcie 5432."),
            (_OWNER, "The billing service belongs to Piotr."),
            (_OWNER_PL, "Za serwis billingowy odpowiada Marek."),
            (_PYTHON, "This project needs Python 3.11."),
            (_BACKUP, "The nightly backup runs at 03:00 UTC."),
            (_LIMIT, "We allow at most 1000 API requests per minute."),
            (
                "The config file is /etc/vis/config.yml.",
                "The config file is /etc/vis/local.yml.",
            ),
            (
                "Code review needs one approval before a merge.",
                "Code review comments must get an answer within one day.",
            ),
            (_LOGS, "Kopie zapasowe trzymamy przez 30 dni."),
            (
                "Tests run on every push to the main branch.",
                "Linting runs on every push to the main branch.",
            ),
            (
                "Zespół woli krótkie spotkania rano.",
                "Zespół woli długie spotkania po południu.",
            ),
            (
                "The cache is cleared when the service restarts.",
                "The cache is filled when the service starts.",
            ),
            (
                "The team writes commit messages in the imperative mood.",
                "The team writes long commit messages with a body.",
            ),
        ],
    ),
    *_pairs(
        "related",
        [
            ("which port does the database use", _DB),
            ("jak uruchomić testy", _TESTS),
            ("who owns billing", _OWNER),
            ("backup schedule", _BACKUP),
            ("gdzie trzymać sekrety", _SECRETS),
            ("Python version", _PYTHON),
            ("rate limiting of the API", _LIMIT),
            ("kiedy jest spotkanie zespołu", _MEETING),
            ("how to format code", _RUFF),
            ("production deploy rules", _DEPLOY),
        ],
    ),
    *_pairs(
        "unrelated",
        [
            (
                "which port does the database use",
                "Kamil prefers short commit messages.",
            ),
            ("jak uruchomić testy", _BACKUP),
            ("who owns billing", "Cache entries expire after 15 minutes."),
            ("backup schedule", _RUFF),
            ("gdzie trzymać sekrety", _MEETING),
            ("Python version", _OWNER_PL),
            ("recipe for pancakes", _DB),
            ("pogoda w Krakowie", _LIMIT),
            ("how to format code", _DEPLOY),
            ("favourite football team", _LOGS),
        ],
    ),
)
"""Pairs in English and Polish, with near misses: other ports, environments, people,
versions, times, limits and paths. Add pairs from your own memory to a JSON lines file."""


if __name__ == "__main__":
    sys.exit(main())
