"""Permanent memory for Vis agents, based on OptMem by Victor Taelin.

Nothing here needs Vis: ``Memo`` is ordinary Python that you can call from a
script. ``extension.py`` is the only file that registers it. Subclass
``MemoryStore`` to keep the memory in another place.
"""

from vis_optmem.guard import UnsafeMemory
from vis_optmem.memo import (
    ALL,
    PERSONAL,
    TEAM,
    DuplicateMemory,
    Memo,
    MemoryNotSet,
    load_store,
    status,
)
from vis_optmem.remote import RemoteStore
from vis_optmem.store import Entry, FileStore, MemoryStore

__all__ = [
    "ALL",
    "PERSONAL",
    "TEAM",
    "DuplicateMemory",
    "Entry",
    "FileStore",
    "Memo",
    "MemoryNotSet",
    "MemoryStore",
    "RemoteStore",
    "UnsafeMemory",
    "load_store",
    "status",
]
