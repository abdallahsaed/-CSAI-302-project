"""Buffer pool manager with pluggable replacement policy.

* fetch_page(pid)  -> pinned page buffer (bytearray, mutate in place)
* new_page()       -> (pid, pinned zeroed buffer)
* unpin_page(pid, dirty)
* flush_page / flush_all, delete_page
* reconfigure(policy, pool_size)  swap policy / size at runtime (flushes first)
* drop_cache()                    cold-start the pool (used by benchmarks)

Pinned pages are never evicted; dirty victims are written back before reuse.
"""
import threading

from .disk import DiskManager
from .errors import BufferPoolFullError, StorageError
from .replacer import make_replacer


class Frame:
    __slots__ = ("page_id", "data", "pin", "dirty")

    def __init__(self):
        self.page_id = None
        self.data = None
        self.pin = 0
        self.dirty = False


class BufferPool:
    def __init__(self, disk: DiskManager, pool_size=256, policy="lru"):
        self.disk = disk
        self.page_size = disk.page_size
        self._lock = threading.RLock()
        self._build(pool_size, policy)
        self.reset_stats()

    # ------------------------------------------------------------------ setup
    def _build(self, pool_size, policy):
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        self.pool_size = pool_size
        self.policy = policy.lower()
        self.replacer = make_replacer(self.policy, pool_size)
        self.frames = [Frame() for _ in range(pool_size)]
        self.page_table = {}
        self.free = list(range(pool_size - 1, -1, -1))

    def reset_stats(self):
        self.hits = self.misses = self.evictions = self.dirty_evictions = self.new_pages = 0
        self.disk.reset_counters()

    def stats(self):
        total = self.hits + self.misses
        return {
            "policy": self.policy, "pool_size": self.pool_size,
            "hits": self.hits, "misses": self.misses,
            "hit_ratio": (self.hits / total) if total else 0.0,
            "evictions": self.evictions, "dirty_evictions": self.dirty_evictions,
            "new_pages": self.new_pages,
            "page_reads": self.disk.reads, "page_writes": self.disk.writes,
        }

    def pinned_count(self):
        return sum(1 for f in self.frames if f.pin > 0)

    # ------------------------------------------------------------------ core API
    def _acquire_frame(self):
        if self.free:
            return self.free.pop()
        fid = self.replacer.victim()
        if fid is None:
            raise BufferPoolFullError(f"all {self.pool_size} frames are pinned")
        fr = self.frames[fid]
        if fr.dirty:
            self.disk.write_page(fr.page_id, fr.data)
            self.dirty_evictions += 1
        del self.page_table[fr.page_id]
        self.evictions += 1
        fr.page_id, fr.data, fr.pin, fr.dirty = None, None, 0, False
        return fid

    def fetch_page(self, pid):
        with self._lock:
            fid = self.page_table.get(pid)
            if fid is not None:                                   # HIT
                fr = self.frames[fid]
                self.hits += 1
                fr.pin += 1
                if fr.pin == 1:
                    self.replacer.set_evictable(fid, False)
                self.replacer.record_access(fid)
                return fr.data
            self.misses += 1                                       # MISS
            fid = self._acquire_frame()
            fr = self.frames[fid]
            try:
                fr.data = self.disk.read_page(pid)
            except Exception:
                self.free.append(fid)
                raise
            fr.page_id, fr.pin, fr.dirty = pid, 1, False
            self.page_table[pid] = fid
            self.replacer.record_load(fid)
            return fr.data

    def new_page(self):
        with self._lock:
            fid = self._acquire_frame()
            try:
                pid = self.disk.allocate_page()
            except Exception:
                self.free.append(fid)
                raise
            fr = self.frames[fid]
            fr.page_id, fr.data, fr.pin, fr.dirty = pid, bytearray(self.page_size), 1, True
            self.page_table[pid] = fid
            self.replacer.record_load(fid)
            self.new_pages += 1
            return pid, fr.data

    def unpin_page(self, pid, dirty=False):
        with self._lock:
            fid = self.page_table.get(pid)
            if fid is None:
                raise StorageError(f"unpin of page {pid} which is not in the pool")
            fr = self.frames[fid]
            if fr.pin <= 0:
                raise StorageError(f"unpin of page {pid} which is not pinned")
            fr.pin -= 1
            if dirty:
                fr.dirty = True
            if fr.pin == 0:
                self.replacer.set_evictable(fid, True)

    def flush_page(self, pid):
        with self._lock:
            fid = self.page_table.get(pid)
            if fid is not None and self.frames[fid].dirty:
                fr = self.frames[fid]
                self.disk.write_page(pid, fr.data)
                fr.dirty = False

    def flush_all(self):
        with self._lock:
            for fr in self.frames:
                if fr.page_id is not None and fr.dirty:
                    self.disk.write_page(fr.page_id, fr.data)
                    fr.dirty = False

    def delete_page(self, pid):
        """Drop a page from the pool (without write-back) and free it on disk."""
        with self._lock:
            fid = self.page_table.get(pid)
            if fid is not None:
                fr = self.frames[fid]
                if fr.pin > 0:
                    raise StorageError(f"cannot delete pinned page {pid}")
                self.replacer.remove(fid)
                del self.page_table[pid]
                fr.page_id, fr.data, fr.dirty = None, None, False
                self.free.append(fid)
            self.disk.deallocate_page(pid)

    # ------------------------------------------------------------------ configuration
    def reconfigure(self, policy=None, pool_size=None):
        """Flush everything, then rebuild the pool with a new policy and/or size."""
        with self._lock:
            if self.pinned_count():
                raise StorageError("cannot reconfigure while pages are pinned")
            self.flush_all()
            self._build(pool_size or self.pool_size, policy or self.policy)

    def drop_cache(self):
        """Flush and empty the pool (cold start) keeping the same configuration."""
        self.reconfigure()
