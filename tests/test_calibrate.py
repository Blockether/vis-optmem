"""Tests of the threshold calibration, with a fake embed that gives exact cosines."""

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vis_optmem.calibrate import (
    DEFAULT_PAIRS,
    KINDS,
    Pair,
    calibrate,
    check,
    load_pairs,
    main,
)

ROOT = Path(__file__).resolve().parents[1]


def angle_embed(cosines: dict[str, float]):
    """An embed that puts each text on the unit circle: the cosine to "q" is ``cosines[text]``."""

    def embed(texts):
        return [
            (cosines.get(t, 1.0), math.sqrt(max(0.0, 1 - cosines.get(t, 1.0) ** 2)))
            for t in texts
        ]

    return embed


def hash_embed(texts):
    """A bag of words in 64 buckets: texts with common words come close."""
    vectors = []
    for text in texts:
        vector = [0.0] * 64
        for word in text.lower().split():
            vector[hashlib.sha256(word.encode()).digest()[0] % 64] += 1.0
        vectors.append(vector)
    return vectors


# Each pair compares a text with "q", so its cosine is the value in COSINES.
COSINES = {
    "rep high": 0.97,
    "rep low": 0.90,
    "diff high": 0.86,
    "diff low": 0.70,
    "diff port 5433": 0.99,
    "rel low": 0.60,
    "rel high": 0.80,
    "unrel high": 0.40,
    "unrel low": 0.10,
}
PAIRS = [
    Pair("q", "rep high", "repeat"),
    Pair("q", "rep low", "repeat"),
    Pair("q", "diff high", "different"),
    Pair("q", "diff low", "different"),
    # Other names: correct at any cosine, so it does not limit duplicate_min.
    Pair("q port 5432", "diff port 5433", "different"),
    Pair("q", "rel low", "related"),
    Pair("q", "rel high", "related"),
    Pair("q", "unrel high", "unrelated"),
    Pair("q", "unrel low", "unrelated"),
]
EMBED = angle_embed(COSINES | {"q port 5432": 1.0})


def test_calibrate_chooses_the_middle_of_each_gap():
    result = calibrate(EMBED, PAIRS)

    assert result.duplicate_min == pytest.approx(0.88)
    assert result.duplicate_margin == pytest.approx(0.02)
    assert result.search_min == pytest.approx(0.50)
    assert result.search_margin == pytest.approx(0.10)
    assert result.unseparated == ()
    assert not check(EMBED, PAIRS, result.duplicate_min, result.search_min)


def test_calibrate_gives_the_pairs_that_no_threshold_separates():
    overlap = PAIRS + [Pair("q", "diff higher", "different")]
    embed = angle_embed(COSINES | {"q port 5432": 1.0, "diff higher": 0.93})

    result = calibrate(embed, overlap)

    assert result.duplicate_margin == pytest.approx(-0.015)
    assert {(s.pair.b, s.pair.kind) for s in result.unseparated} == {
        ("rep low", "repeat"),
        ("diff higher", "different"),
    }
    assert check(embed, overlap, result.duplicate_min, result.search_min)


def test_a_repeat_with_other_names_is_never_found():
    pairs = PAIRS + [Pair("q port 5432", "rep port 5433", "repeat")]
    embed = angle_embed(COSINES | {"q port 5432": 1.0, "rep port 5433": 0.99})

    result = calibrate(embed, pairs)

    assert [s.pair.b for s in result.unseparated] == ["rep port 5433"]
    assert check(embed, pairs, result.duplicate_min, result.search_min) == [
        "repeat not found: other names: 'q port 5432' | 'rep port 5433'"
    ]


def test_check_names_each_wrong_decision():
    found = check(EMBED, PAIRS, duplicate_min=0.95, search_min=0.7)

    assert found == [
        "repeat not found: cosine 0.9000 < 0.9500: 'q' | 'rep low'",
        "related not found: cosine 0.6000 < 0.7000: 'q' | 'rel low'",
    ]
    assert check(EMBED, PAIRS, duplicate_min=0.85, search_min=0.3) == [
        "different seen as a repeat: cosine 0.8600 >= 0.8500: 'q' | 'diff high'",
        "unrelated found: cosine 0.4000 >= 0.3000: 'q' | 'unrel high'",
    ]


def test_calibrate_needs_both_kinds_of_each_threshold():
    with pytest.raises(ValueError, match="related pair and an unrelated pair"):
        calibrate(EMBED, [p for p in PAIRS if p.kind != "unrelated"])
    with pytest.raises(ValueError, match="kind must be one of"):
        Pair("a", "b", "same")


def test_embed_gets_each_text_one_time():
    calls = []

    def embed(texts):
        calls.append(texts)
        return EMBED(texts)

    calibrate(embed, PAIRS)

    assert len(calls) == 1
    assert len(calls[0]) == len(set(calls[0]))


def test_load_pairs_reads_json_lines(tmp_path):
    path = tmp_path / "pairs.jsonl"
    path.write_text(
        '{"a": "x", "b": "y", "kind": "repeat"}\n\n{"a": "x", "b": "z", "kind": "related"}\n',
        encoding="utf-8",
    )
    assert load_pairs(path) == [Pair("x", "y", "repeat"), Pair("x", "z", "related")]

    path.write_text('{"a": "x", "kind": "repeat"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="pairs.jsonl:1"):
        load_pairs(path)


def test_default_pairs_cover_each_kind_in_both_languages():
    assert len(DEFAULT_PAIRS) >= 40
    assert {p.kind for p in DEFAULT_PAIRS} == set(KINDS)
    polish = [p for p in DEFAULT_PAIRS if any(c in p.a + p.b for c in "ąćęłńóśźż")]
    assert {p.kind for p in polish} == set(KINDS)
    result = calibrate(hash_embed, DEFAULT_PAIRS)
    assert len(result.scores) == len(DEFAULT_PAIRS)


def write_embed_module(directory: Path) -> None:
    (directory / "fake_model.py").write_text(
        "import math\n"
        f"COSINES = {COSINES | {'q port 5432': 1.0}!r}\n"
        "def embed(texts):\n"
        "    return [(COSINES.get(t, 1.0), math.sqrt(1 - COSINES.get(t, 1.0) ** 2))"
        " for t in texts]\n",
        encoding="utf-8",
    )


def write_pairs(path: Path) -> None:
    path.write_text(
        "".join(json.dumps({"a": p.a, "b": p.b, "kind": p.kind}) + "\n" for p in PAIRS),
        encoding="utf-8",
    )


def test_cli_prints_the_calibration(tmp_path, monkeypatch, capsys):
    write_embed_module(tmp_path)
    write_pairs(tmp_path / "pairs.jsonl")
    monkeypatch.syspath_prepend(str(tmp_path))

    code = main([str(tmp_path / "pairs.jsonl"), "--embed", "fake_model:embed"])

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["duplicate_min"] == pytest.approx(0.88)
    assert output["search_min"] == pytest.approx(0.5)
    assert output["problems"] == []


def test_cli_exits_1_when_given_thresholds_have_problems(tmp_path):
    write_embed_module(tmp_path)
    write_pairs(tmp_path / "pairs.jsonl")
    env = os.environ | {
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(tmp_path)])
    }
    command = [
        sys.executable,
        "-m",
        "vis_optmem.calibrate",
        str(tmp_path / "pairs.jsonl"),
    ]
    command += [
        "--embed",
        "fake_model:embed",
        "--duplicate-min",
        "0.85",
        "--search-min",
        "0.5",
    ]

    done = subprocess.run(command, capture_output=True, text=True, env=env, check=False)

    output = json.loads(done.stdout)
    assert done.returncode == 1
    assert output["checked"] == {"duplicate_min": 0.85, "search_min": 0.5}
    assert output["problems"] == [
        "different seen as a repeat: cosine 0.8600 >= 0.8500: 'q' | 'diff high'"
    ]


def test_cli_refuses_one_threshold_and_a_bad_embed(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["--embed", "fake_model:embed", "--duplicate-min", "0.9"])
    assert main(["--embed", "no_colon"]) == 2
    assert "module:function" in capsys.readouterr().err
