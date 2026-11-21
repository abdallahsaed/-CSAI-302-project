"""Column-oriented table: every column lives in its own set of pages.

Page layout (per column page):  header 8B = type(1) col(1) count(2) used(2) pad(2)
  * INT/FLOAT : densely packed fixed-width values  -> O(1) positional access
  * TEXT      : u16 length + UTF-8 bytes, packed   -> sequential decode inside a page
A per-column directory  [page_id, first_row, count]  (kept in the catalog) maps a row
ordinal to its page by binary search.  Row ordinal == record id.  Deletes are
tombstones (a set of row ids); fixed-width columns are updated in place, an update
touching a TEXT column is performed as delete + re-insert (new row id).
"""
import struct
from bisect import bisect_right

from .errors import SchemaError
from .schema import FIXED, TEXT
from .table import Table

HDR = 8
PAGE_TYPE_COL = 2
_U16 = struct.Struct("<H")
_HDR = struct.Struct("<BBHHH")


class ColumnTable(Table):
    layout = "column"

    def __init__(self, name, schema, pool, meta=None):
        m = meta or {}
        self.name, self.schema, self.pool = name, schema, pool
        self.num_rows = m.get("num_rows", 0)
        self.deleted = set(m.get("deleted", []))
        self.dirs = m.get("dirs") or [[] for _ in range(schema.ncols)]
        self.firsts = [[e[1] for e in d] for d in self.dirs]
        self.num_pages = m.get("num_pages", 0)
        self.codes = [FIXED[t][0] if t in FIXED else None for t in schema.types]
        self.widths = [FIXED[t][1] if t in FIXED else None for t in schema.types]
        self.caps = [(pool.page_size - HDR) // w if w else None for w in self.widths]

    def meta(self):
        return {"num_rows": self.num_rows, "deleted": sorted(self.deleted),
                "dirs": self.dirs, "num_pages": self.num_pages}

    # ------------------------------------------------------------------ page helpers
    def _new_col_page(self, ci, first_row):
        pid, buf = self.pool.new_page()
        _HDR.pack_into(buf, 0, 2, ci, 0, HDR, 0)
        self.pool.unpin_page(pid, True)
        self.dirs[ci].append([pid, first_row, 0])
        self.firsts[ci].append(first_row)
        self.num_pages += 1

    def _append_fixed(self, ci, vals, base):
        pool, d, cap, w, code = self.pool, self.dirs[ci], self.caps[ci], self.widths[ci], self.codes[ci]
        i, n = 0, len(vals)
        while i < n:
            if not d or d[-1][2] >= cap:
                self._new_col_page(ci, base + i)
            ent = d[-1]
            take = min(cap - ent[2], n - i)
            buf = pool.fetch_page(ent[0])
            try:
                struct.pack_into(f"<{take}{code}", buf, HDR + ent[2] * w, *vals[i:i + take])
                _U16.pack_into(buf, 2, ent[2] + take)
            finally:
                pool.unpin_page(ent[0], True)
            ent[2] += take
            i += take

    def _append_text(self, ci, vals, base):
        pool, d, ps = self.pool, self.dirs[ci], self.pool.page_size
        enc = [v.encode("utf-8") for v in vals]
        limit = ps - HDR - 2
        if any(len(b) > limit for b in enc):
            raise ValueError(f"TEXT value too large for a {ps}-byte page")
        i, n = 0, len(enc)
        while i < n:
            if not d:
                self._new_col_page(ci, base + i)
            ent = d[-1]
            buf = pool.fetch_page(ent[0])
            added = 0
            try:
                pos = _U16.unpack_from(buf, 4)[0]
                while i < n:
                    b = enc[i]
                    need = len(b) + 2
                    if pos + need > ps:
                        break
                    _U16.pack_into(buf, pos, len(b))
                    buf[pos + 2:pos + need] = b
                    pos += need
                    i += 1
                    added += 1
                if added:
                    _U16.pack_into(buf, 2, ent[2] + added)
                    _U16.pack_into(buf, 4, pos)
            finally:
                pool.unpin_page(ent[0], added > 0)
            ent[2] += added
            if i < n:
                self._new_col_page(ci, base + i)

    def _decode_page(self, ci, buf):
        cnt = _U16.unpack_from(buf, 2)[0]
        if self.codes[ci]:
            return struct.unpack_from(f"<{cnt}{self.codes[ci]}", buf, HDR)
        out, pos, unpack = [], HDR, _U16.unpack_from
        for _ in range(cnt):
            ln = unpack(buf, pos)[0]
            pos += 2
            out.append(bytes(buf[pos:pos + ln]).decode("utf-8"))
            pos += ln
        return out

    def _locate(self, ci, row_id):
        k = bisect_right(self.firsts[ci], row_id) - 1
        return self.dirs[ci][k]

    def _read_value(self, ci, row_id):
        pid, first, _ = self._locate(ci, row_id)
        buf = self.pool.fetch_page(pid)
        try:
            k = row_id - first
            if self.codes[ci]:
                return struct.unpack_from("<" + self.codes[ci], buf, HDR + k * self.widths[ci])[0]
            pos = HDR
            for _ in range(k):
                pos += 2 + _U16.unpack_from(buf, pos)[0]
            ln = _U16.unpack_from(buf, pos)[0]
            return bytes(buf[pos + 2:pos + 2 + ln]).decode("utf-8")
        finally:
            self.pool.unpin_page(pid, False)

    # ------------------------------------------------------------------ writes
    def insert_many(self, rows):
        rows = [self.schema.validate(r) for r in rows]
        if not rows:
            return []
        base = self.num_rows
        for ci, col in enumerate(zip(*rows)):
            if self.codes[ci]:
                self._append_fixed(ci, col, base)
            else:
                self._append_text(ci, col, base)
        self.num_rows += len(rows)
        return list(range(base, base + len(rows)))

    def insert(self, row):
        return self.insert_many([row])[0]

    def _live(self, rid):
        return isinstance(rid, int) and 0 <= rid < self.num_rows and rid not in self.deleted

    def delete(self, rid):
        if not self._live(rid):
            return False
        self.deleted.add(rid)
        return True

    def update(self, rid, row):
        if not self._live(rid):
            raise KeyError(f"no live record {rid}")
        row = self.schema.validate(row)
        old = self.get(rid)
        if any(old[i] != row[i] for i in self.schema.text_idx):
            self.delete(rid)
            return self.insert(row)
        for ci in self.schema.fixed_idx:
            if old[ci] != row[ci]:
                pid, first, _ = self._locate(ci, rid)
                buf = self.pool.fetch_page(pid)
                try:
                    struct.pack_into("<" + self.codes[ci], buf, HDR + (rid - first) * self.widths[ci], row[ci])
                finally:
                    self.pool.unpin_page(pid, True)
        return rid

    # ------------------------------------------------------------------ reads
    def get(self, rid, columns=None):
        if not self._live(rid):
            return None
        cis = range(self.schema.ncols) if columns is None else self._proj(columns)
        return tuple(self._read_value(ci, rid) for ci in cis)

    def _column_chunks(self, ci):
        for pid, _, _ in list(self.dirs[ci]):
            buf = self.pool.fetch_page(pid)
            try:
                vals = self._decode_page(ci, buf)
            finally:
                self.pool.unpin_page(pid, False)
            if len(vals):
                yield vals

    def _batches(self, cis):
        """Yield (first_row_id, [value-sequence per requested column]) with aligned lengths."""
        iters = [self._column_chunks(ci) for ci in cis]
        bufs = [()] * len(cis)
        pos = 0
        while pos < self.num_rows:
            for k, it in enumerate(iters):
                if not len(bufs[k]):
                    bufs[k] = next(it)
            n = min(len(b) for b in bufs)
            yield pos, [b[:n] for b in bufs]
            bufs = [b[n:] for b in bufs]
            pos += n

    def scan(self, columns=None):
        cis = list(range(self.schema.ncols)) if columns is None else self._proj(columns)
        dead = self.deleted
        for pos, cols in self._batches(cis):
            for k, row in enumerate(zip(*cols)):
                if dead and (pos + k) in dead:
                    continue
                yield pos + k, row

    def rows(self, columns=None):
        cis = list(range(self.schema.ncols)) if columns is None else self._proj(columns)
        dead = self.deleted
        for pos, cols in self._batches(cis):
            if not dead:
                yield from zip(*cols)
            else:
                for k, row in enumerate(zip(*cols)):
                    if (pos + k) not in dead:
                        yield row

    def column_values(self, name):
        ci = self.schema.col_index(name)
        dead = self.deleted
        for pos, (vals,) in self._batches([ci]):
            if not dead:
                yield from vals
            else:
                for k, v in enumerate(vals):
                    if (pos + k) not in dead:
                        yield v

    def count(self):
        return self.num_rows - len(self.deleted)

    def page_ids(self):
        for d in self.dirs:
            for ent in d:
                yield ent[0]
