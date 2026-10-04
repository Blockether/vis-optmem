"""Permanent memory for Vis agents, based on OptMem by Victor Taelin.

Nothing here needs Vis: ``Memo`` is ordinary Python that you can call from a
script. ``extension.py`` is the only file that registers it.
"""

from vis_optmem.memo import Memo, status

__all__ = ["Memo", "status"]
