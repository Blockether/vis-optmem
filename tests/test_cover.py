"""The blocks that wake shows: exact tiling, the line budget and detail near the present."""

import pytest

from vis_optmem.cover import cover, tile


def test_every_memory_shows_in_full_when_all_fit():
    assert cover(0, 96) == []
    assert cover(5, 5) == [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]


@pytest.mark.parametrize("total", [7, 37, 97, 300, 1023, 1024, 1025, 50_000, 1_000_000])
@pytest.mark.parametrize("budget", [16, 33, 96])
def test_blocks_tile_the_memories_with_aligned_power_of_two_blocks(total, budget):
    blocks = cover(total, budget)
    assert blocks[0][0] == 0 and blocks[-1][1] == total
    for (lo, hi), (next_lo, _) in zip(blocks, blocks[1:]):
        assert hi == next_lo
    for lo, hi in blocks:
        size = hi - lo
        assert size & (size - 1) == 0 and lo % size == 0
    assert len(blocks) == min(total, budget)


def test_detail_goes_down_with_age():
    sizes = [hi - lo for lo, hi in cover(1_000_000, 96)]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] == 1


def test_a_larger_alpha_gives_fewer_blocks():
    assert len(tile(1000, 0.05)) > len(tile(1000, 0.5)) > len(tile(1000, 1.0))


def test_known_covers_match_the_memo_tool():
    # Computed with the OptMem `memo` tool: the same files show the same lines.
    assert cover(37, 9) == [
        (0, 16),
        (16, 24),
        (24, 28),
        (28, 30),
        (30, 32),
        (32, 34),
        (34, 35),
        (35, 36),
        (36, 37),
    ]
    blocks = cover(1_000_000, 96)
    assert blocks[:3] == [(0, 131072), (131072, 262144), (262144, 327680)]
    assert blocks[-3:] == [(999997, 999998), (999998, 999999), (999999, 1000000)]
