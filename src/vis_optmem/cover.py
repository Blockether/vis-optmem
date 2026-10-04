"""Choose the blocks that wake shows: recent memories in full, older ones summarized."""

from __future__ import annotations

from collections.abc import Iterator

Block = tuple[int, int]
"""Memories ``lo`` to ``hi - 1``: a half-open range."""


def _root(total: int) -> int:
    """The smallest power of two that holds ``total`` memories."""
    size = 1
    while size < total:
        size *= 2
    return size


def _visit(lo: int, hi: int, total: int, alpha: float) -> Iterator[Block]:
    if lo >= total:
        return
    width = hi - lo
    if width == 1 or (hi <= total and width <= alpha * (total - lo)):
        yield lo, hi
        return
    middle = lo + width // 2
    yield from _visit(lo, middle, total, alpha)
    yield from _visit(middle, hi, total, alpha)


def tile(total: int, alpha: float) -> list[Block]:
    """Cover memories ``0`` to ``total - 1`` with aligned power-of-two blocks, oldest first.

    A block stays whole when it is complete and its size is at most ``alpha`` times
    its age: the number of memories from its start to ``total``. A larger ``alpha``
    gives fewer and larger blocks.
    """
    return list(_visit(0, _root(total), total, alpha))


def cover(total: int, budget: int) -> list[Block]:
    """The blocks that wake shows for ``total`` memories in at most ``budget`` lines.

    The detail goes down with age. If all memories fit, every memory is its own
    block. Block sizes are powers of two, so the best ``alpha`` can leave lines
    unused. Those lines go to the newest blocks, where detail is most useful.
    """
    if total <= 0:
        return []
    if total <= budget:
        return [(index, index + 1) for index in range(total)]
    low, high = 0.0, 1.0
    for _ in range(60):
        alpha = (low + high) / 2
        if len(tile(total, alpha)) > budget:
            low = alpha
        else:
            high = alpha
    blocks = tile(total, high)
    while len(blocks) < budget:
        newest = next(
            (
                index
                for index in range(len(blocks) - 1, -1, -1)
                if blocks[index][1] - blocks[index][0] > 1
            ),
            None,
        )
        if newest is None:
            break
        lo, hi = blocks[newest]
        middle = (lo + hi) // 2
        blocks[newest : newest + 1] = [(lo, middle), (middle, hi)]
    return blocks
