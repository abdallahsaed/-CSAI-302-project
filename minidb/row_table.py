"""Row-oriented table: a heap file of slotted pages linked into a chain.

Inserts go to the last page (a new page is chained when it is full).  Deleted
space is reused only inside the page it belongs to (no free-space map yet).
"""
from operator import itemgetter

from . import page
from .page import HEADER_SIZE, SLOT_SIZE, _SLOT
from .table import Table


class RowTable(Table):
    layout = "row"

    def __init__(self, name, schema, pool, meta=None):
        m = meta or {}
        self.name, self.schema, self.pool = name, schema, pool
        self.first_page = m.get("first_page", 0)
        self.last_page = m.get("last_page", 0)
        self.num_rows = m.get("num_rows", 0)
        self.num_pages = m.get("num_pages", 0)
        self.max_record = pool.page_size - HEADER_SIZE - SLOT_SIZE

    def meta(self):
        return {"first_page": self.first_page, "last_page": self.last_page,
                "num_rows": self.num_rows, "num_pages": self.num_pages}

    # ------------------------------------------------------------------ writes
    def _encode(self, row):
        row = self.schema.validate(row)
        rec = self.schema.encode(row)
        if len(rec) > self.max_record:
            raise ValueError(f"record of {len(rec)} bytes exceeds the page capacity ({self.max_record})")
        return rec

    def _insert_rec(self, rec):
        pool = self.pool
        if self.last_page:
            buf = pool.fetch_page(self.last_page)
            try:
                slot = page.insert(buf, rec)
            finally:
                pool.unpin_page(self.last_page, True)
            if slot is not None:
                self.num_rows += 1
                return (self.last_page, slot)
        pid, buf = pool.new_page()
        try:
            page.init_page(buf)
            slot = page.insert(buf, rec)
        finally:
            pool.unpin_page(pid, True)
        if self.last_page:
            prev = pool.fetch_page(self.last_page)
            page.set_next_page(prev, pid)
            pool.unpin_page(self.last_page, True)
        else:
            self.first_page = pid
        self.last_page = pid
        self.num_pages += 1
        self.num_rows += 1
        return (pid, slot)

    def insert(self, row):
        return self._insert_rec(self._encode(row))

    def insert_many(self, rows):
        return [self.insert(r) for r in rows]

    def delete(self, rid):
        pid, slot = rid
        buf = self.pool.fetch_page(pid)
        try:
            ok = page.delete(buf, slot)
        finally:
            self.pool.unpin_page(pid, True)
        if ok:
            self.num_rows -= 1
        return ok

    def update(self, rid, row):
        """Update in place if it fits; otherwise relocate. Returns the (possibly new) rid."""
        rec = self._encode(row)
        pid, slot = rid
        buf = self.pool.fetch_page(pid)
        try:
            res = page.update(buf, slot, rec)
        finally:
            self.pool.unpin_page(pid, True)
        if res is None:
            raise KeyError(f"no live record at {rid}")
        if res:
            return rid
        self.delete(rid)
        return self._insert_rec(rec)

    # ------------------------------------------------------------------ reads
    def get(self, rid, columns=None):
        pid, slot = rid
        pool = self.pool
        buf = pool.fetch_page(pid)
        try:
            span = page.slot_span(buf, slot)
            row = self.schema.decode(buf, span[0]) if span else None
        finally:
            pool.unpin_page(pid, False)
        if row is None or columns is None:
            return row
        return tuple(row[i] for i in self._proj(columns))

    def _page_rows(self, pid):
        """Materialise one page (pin released before returning); -> (rows, next_pid)."""
        pool = self.pool
        buf = pool.fetch_page(pid)
        try:
            decode, unpack = self.schema.decode, _SLOT.unpack_from
            out = []
            for s in range(page.num_slots(buf)):
                off = unpack(buf, HEADER_SIZE + s * SLOT_SIZE)[0]
                if off:
                    out.append((s, decode(buf, off)))
            return out, page.next_page(buf)
        finally:
            pool.unpin_page(pid, False)

    def scan(self, columns=None):
        idx = self._proj(columns)
        pick = None if idx is None else itemgetter(*idx) if len(idx) > 1 else (lambda r, i=idx[0]: (r[i],))
        pid = self.first_page
        while pid:
            recs, pid_next = self._page_rows(pid)
            for slot, row in recs:
                yield (pid, slot), (row if pick is None else pick(row))
            pid = pid_next

    def rows(self, columns=None):
        for _, row in self.scan(columns):
            yield row

    def column_values(self, name):
        i = self.schema.col_index(name)
        for _, row in self.scan():
            yield row[i]

    def count(self):
        return self.num_rows

    def page_ids(self):
        pid = self.first_page
        while pid:
            buf = self.pool.fetch_page(pid)
            nxt = page.next_page(buf)
            self.pool.unpin_page(pid, False)
            yield pid
            pid = nxt
