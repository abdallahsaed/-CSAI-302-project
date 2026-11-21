"""Page-replacement policies for the buffer pool.

A replacer only decides WHICH unpinned frame to evict; the buffer pool owns
pinning and dirty write-back.  Interface (all policies):

    record_load(f)        a page was just loaded into frame f
    record_access(f)      frame f was hit again
    set_evictable(f, b)   f became unpinned (True) / pinned (False)
    victim() -> f | None  choose, remove and return an evictable frame
    remove(f)             forget frame f

Policies:  lru | fifo | clock | mru
"""
from collections import OrderedDict


class Replacer:
    name = "base"

    def __init__(self, capacity):
        self.capacity = capacity
        self._evictable = set()

    def set_evictable(self, f, flag):
        if flag:
            self._evictable.add(f)
        else:
            self._evictable.discard(f)

    def record_load(self, f): raise NotImplementedError
    def record_access(self, f): raise NotImplementedError
    def victim(self): raise NotImplementedError
    def remove(self, f): raise NotImplementedError


class LRUReplacer(Replacer):
    """Evict the least recently used frame."""
    name = "lru"

    def __init__(self, capacity):
        super().__init__(capacity)
        self._order = OrderedDict()      # oldest access first

    def record_load(self, f):
        self._order[f] = None
        self._order.move_to_end(f)

    def record_access(self, f):
        if f in self._order:
            self._order.move_to_end(f)

    def _candidates(self):
        return iter(self._order)

    def victim(self):
        chosen = None
        for f in self._candidates():
            if f in self._evictable:
                chosen = f
                break
        if chosen is not None:
            self.remove(chosen)
        return chosen

    def remove(self, f):
        self._order.pop(f, None)
        self._evictable.discard(f)


class FIFOReplacer(LRUReplacer):
    """Evict the frame that has been in the pool longest (hits do not reorder)."""
    name = "fifo"

    def record_access(self, f):
        pass


class MRUReplacer(LRUReplacer):
    """Evict the most recently used frame (good for looping sequential scans)."""
    name = "mru"

    def _candidates(self):
        return reversed(self._order)


class ClockReplacer(Replacer):
    """Second-chance / CLOCK approximation of LRU."""
    name = "clock"

    def __init__(self, capacity):
        super().__init__(capacity)
        self._ref = [False] * capacity
        self._present = [False] * capacity
        self._hand = 0

    def record_load(self, f):
        self._present[f] = True
        self._ref[f] = True

    def record_access(self, f):
        self._ref[f] = True

    def victim(self):
        n = self.capacity
        for _ in range(2 * n):
            f = self._hand
            self._hand = (self._hand + 1) % n
            if self._present[f] and f in self._evictable:
                if self._ref[f]:
                    self._ref[f] = False
                else:
                    self.remove(f)
                    return f
        return None

    def remove(self, f):
        self._present[f] = False
        self._ref[f] = False
        self._evictable.discard(f)


POLICIES = {c.name: c for c in (LRUReplacer, FIFOReplacer, ClockReplacer, MRUReplacer)}


def make_replacer(name, capacity):
    try:
        return POLICIES[name.lower()](capacity)
    except KeyError:
        raise ValueError(f"unknown replacement policy {name!r}; choose from {sorted(POLICIES)}") from None
