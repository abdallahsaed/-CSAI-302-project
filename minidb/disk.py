"""DiskManager: fixed-size page I/O on a single database file.

File layout
-----------
page 0      : file header  (magic, page_size, num_pages, free_list_head)
page 1..N-1 : data pages (owned by tables through the buffer pool)

Freed pages form a singly linked free list (first 4 bytes of a free page hold
the next free page id), so dropped tables give their space back.

Every physical page read/write is counted: these counters are the "page I/O"
metric used by the benchmarks.
"""
import os
import struct
import threading
import time

from .config import PAGE_SIZE, NO_PAGE
from .errors import StorageError

_MAGIC = b"MINIDB01"
_HDR = struct.Struct("<8sIII")  # magic, page_size, num_pages, free_head


class DiskManager:
    def __init__(self, path, page_size=PAGE_SIZE, read_latency_us=0.0):
        if page_size > 32768 or page_size < 128:
            raise StorageError("page_size must be between 128 and 32768")
        self.path = path
        self.page_size = page_size
        self.read_latency = read_latency_us / 1e6   # optional simulated device latency
        self.reads = 0
        self.writes = 0
        self._lock = threading.Lock()
        existed = os.path.exists(path) and os.path.getsize(path) >= _HDR.size
        self._f = open(path, "r+b" if existed else "w+b", buffering=0)
        if existed:
            magic, ps, n, free_head = _HDR.unpack(self._raw_read(0, _HDR.size))
            if magic != _MAGIC:
                raise StorageError("not a MiniDB file (bad magic)")
            if ps != page_size:
                raise StorageError(f"file page size is {ps}, requested {page_size}")
            self.num_pages, self.free_head = n, free_head
        else:
            self.num_pages, self.free_head = 1, NO_PAGE
            self._write_header()

    # ------------------------------------------------------------------ internals
    def _raw_read(self, offset, n):
        self._f.seek(offset)
        return self._f.read(n)

    def _write_header(self):
        buf = bytearray(self.page_size)
        _HDR.pack_into(buf, 0, _MAGIC, self.page_size, self.num_pages, self.free_head)
        self._f.seek(0)
        self._f.write(buf)

    def _check(self, pid):
        if not (0 < pid < self.num_pages):
            raise StorageError(f"invalid page id {pid} (file has {self.num_pages} pages)")

    # ------------------------------------------------------------------ page I/O
    def read_page(self, pid):
        self._check(pid)
        with self._lock:
            data = self._raw_read(pid * self.page_size, self.page_size)
            self.reads += 1
        if len(data) < self.page_size:            # allocated but never written => zeros
            data = data + bytes(self.page_size - len(data))
        if self.read_latency:
            end = time.perf_counter() + self.read_latency
            while time.perf_counter() < end:
                pass
        return bytearray(data)

    def write_page(self, pid, data):
        self._check(pid)
        if len(data) != self.page_size:
            raise StorageError("write_page needs exactly one page of data")
        with self._lock:
            self._f.seek(pid * self.page_size)
            self._f.write(data)
            self.writes += 1

    # ------------------------------------------------------------------ allocation
    def allocate_page(self):
        with self._lock:
            if self.free_head != NO_PAGE:
                pid = self.free_head
                raw = self._raw_read(pid * self.page_size, 4)
                self.free_head = struct.unpack("<I", raw.ljust(4, b"\0"))[0]
                self.reads += 1
                return pid
            pid = self.num_pages
            self.num_pages += 1
            return pid

    def deallocate_page(self, pid):
        self._check(pid)
        buf = bytearray(self.page_size)
        with self._lock:
            struct.pack_into("<I", buf, 0, self.free_head)
            self._f.seek(pid * self.page_size)
            self._f.write(buf)
            self.writes += 1
            self.free_head = pid

    # ------------------------------------------------------------------ durability
    def sync(self):
        with self._lock:
            self._write_header()
            self._f.flush()
            os.fsync(self._f.fileno())

    def close(self):
        if not self._f.closed:
            self.sync()
            self._f.close()

    def __del__(self):
        try:
            if not self._f.closed:
                self._f.close()
        except Exception:
            pass

    def reset_counters(self):
        self.reads = 0
        self.writes = 0

    def file_size(self):
        return os.path.getsize(self.path)
