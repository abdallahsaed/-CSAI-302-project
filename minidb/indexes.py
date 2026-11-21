"""B+ tree and LSM secondary indexes used by MiniDB milestone 2."""
from bisect import bisect_left, bisect_right
from collections import defaultdict


class BPlusTree:
    """B+ tree with duplicate keys, linked leaves, range scans and deletion.

    Deletion rebuilds the compact tree from its remaining leaf entries. This
    guarantees correct occupancy and separator keys, while keeping this
    educational implementation small and deterministic.
    """
    def __init__(self, order=32):
        if order < 4:
            raise ValueError("B+ tree order must be >= 4")
        self.order = order
        self.root = {"leaf": True, "keys": [], "values": [], "children": [], "next": None}

    def insert(self, key, rid):
        split = self._insert(self.root, key, rid)
        if split:
            sep, right = split
            self.root = {"leaf": False, "keys": [sep], "children": [self.root, right], "values": [], "next": None}

    def _insert(self, node, key, rid):
        if node["leaf"]:
            i = bisect_left(node["keys"], key)
            if i < len(node["keys"]) and node["keys"][i] == key:
                if rid not in node["values"][i]:
                    node["values"][i].append(rid)
                return None
            node["keys"].insert(i, key); node["values"].insert(i, [rid])
            if len(node["keys"]) <= self.order: return None
            mid = len(node["keys"]) // 2
            right = {"leaf": True, "keys": node["keys"][mid:], "values": node["values"][mid:], "children": [], "next": node["next"]}
            node["keys"], node["values"] = node["keys"][:mid], node["values"][:mid]
            node["next"] = right
            return right["keys"][0], right
        i = bisect_right(node["keys"], key)
        split = self._insert(node["children"][i], key, rid)
        if split:
            sep, right = split; node["keys"].insert(i, sep); node["children"].insert(i + 1, right)
        if len(node["keys"]) <= self.order: return None
        mid = len(node["keys"]) // 2; sep = node["keys"][mid]
        right = {"leaf": False, "keys": node["keys"][mid + 1:], "children": node["children"][mid + 1:], "values": [], "next": None}
        node["keys"], node["children"] = node["keys"][:mid], node["children"][:mid + 1]
        return sep, right

    def search(self, key):
        leaf = self._find_leaf(key); i = bisect_left(leaf["keys"], key)
        return list(leaf["values"][i]) if i < len(leaf["keys"]) and leaf["keys"][i] == key else []

    def delete(self, key, rid):
        """Remove exactly one (key, RID) entry; return whether it existed."""
        entries = self.range_search()
        if not any(k == key and r == rid for k, r in entries): return False
        self.root = {"leaf": True, "keys": [], "values": [], "children": [], "next": None}
        for k, r in entries:
            if k != key or r != rid: self.insert(k, r)
        return True

    def range_search(self, low=None, high=None, include_low=True, include_high=True):
        leaf = self._leftmost() if low is None else self._find_leaf(low); out = []
        while leaf is not None:
            for key, rids in zip(leaf["keys"], leaf["values"]):
                if low is not None and (key < low or (key == low and not include_low)): continue
                if high is not None and (key > high or (key == high and not include_high)): return out
                out.extend((key, rid) for rid in rids)
            leaf = leaf["next"]
        return out

    def _find_leaf(self, key):
        node = self.root
        while not node["leaf"]: node = node["children"][bisect_right(node["keys"], key)]
        return node

    def _leftmost(self):
        node = self.root
        while not node["leaf"]: node = node["children"][0]
        return node


class LSMTree:
    """LSM index with memtable, immutable sorted runs, tombstones and compaction."""
    def __init__(self, flush_threshold=128):
        if flush_threshold < 1: raise ValueError("flush_threshold must be >= 1")
        self.flush_threshold = flush_threshold; self.memtable = defaultdict(list); self.runs = []; self._pending = 0

    def insert(self, key, rid):
        self.memtable[key].append(("put", rid)); self._pending += 1; self._maybe_flush()

    def delete(self, key, rid):
        self.memtable[key].append(("del", rid)); self._pending += 1; self._maybe_flush()

    def _maybe_flush(self):
        if self._pending >= self.flush_threshold: self.flush()

    def flush(self):
        if not self.memtable: return
        latest = {}
        for key, ops in self.memtable.items():
            for op, rid in ops: latest[(key, rid)] = op
        run = [(key, op, rid) for (key, rid), op in latest.items()]
        run.sort(key=lambda x: (x[0], repr(x[2]))); self.runs.insert(0, run)
        self.memtable = defaultdict(list); self._pending = 0

    def search(self, key):
        latest = {}
        for op, rid in reversed(self.memtable.get(key, [])): latest.setdefault(rid, op)
        for run in self.runs:
            for k, op, rid in run:
                if k == key: latest.setdefault(rid, op)
        return [rid for rid, op in latest.items() if op == "put"]

    def range_search(self, low=None, high=None):
        keys = set(self.memtable)
        for run in self.runs: keys.update(k for k, _, _ in run)
        out = []
        for key in sorted(keys):
            if low is not None and key < low: continue
            if high is not None and key > high: break
            out.extend((key, rid) for rid in self.search(key))
        return out

    def compact(self):
        """Merge all levels into one sorted run, discarding obsolete ops."""
        self.flush(); runs_before = len(self.runs); latest = {}
        # Older runs first; newer runs overwrite older operations.
        for run in reversed(self.runs):
            for key, op, rid in run: latest[(key, rid)] = op
        compacted = [(key, "put", rid) for (key, rid), op in latest.items() if op == "put"]
        compacted.sort(key=lambda x: (x[0], repr(x[2])))
        self.runs = [compacted] if compacted else []
        return {"runs_before": runs_before, "runs_after": len(self.runs), "entries_after": len(compacted)}

class IndexManager:
    """Owns per-column indexes and filters stale record identifiers."""
    def __init__(self, table):
        self.table = table; self.indexes = {}; self.definitions = {}; self.reads = 0; self.writes = 0

    def create(self, column, kind="auto", workload=None):
        self.table.schema.col_index(column)
        if kind not in ("auto", "btree", "lsm"): raise ValueError("kind must be auto, btree, or lsm")
        selected = ("lsm" if workload == "write" else "btree") if kind == "auto" else kind
        idx = BPlusTree() if selected == "btree" else LSMTree()
        self.indexes[column] = idx
        self.definitions[column] = {"kind": selected, "requested": kind, "workload": workload or "read"}
        ci = self.table.schema.col_index(column)
        for rid, row in self.table.scan(): idx.insert(row[ci], rid)
        return selected

    def rebuild(self, definitions):
        for col, definition in definitions.items():
            self.create(col, definition.get("requested", definition.get("kind", "auto")), definition.get("workload"))

    def on_insert(self, rid, row):
        for col, idx in self.indexes.items(): idx.insert(row[self.table.schema.col_index(col)], rid)
        self.writes += 1

    def on_delete(self, rid, old_row):
        for col, idx in self.indexes.items():
            key = old_row[self.table.schema.col_index(col)]
            if isinstance(idx, LSMTree): idx.delete(key, rid)
            else: idx.delete(key, rid)
        self.writes += 1

    def on_update(self, old_rid, new_rid, old_row, new_row):
        for col, idx in self.indexes.items():
            ci = self.table.schema.col_index(col); old_key, new_key = old_row[ci], new_row[ci]
            if old_key == new_key and old_rid == new_rid: continue
            if isinstance(idx, LSMTree): idx.delete(old_key, old_rid)
            else: idx.delete(old_key, old_rid)
            idx.insert(new_key, new_rid)
        self.writes += 1

    def _visible(self, column, key, rids):
        ci = self.table.schema.col_index(column); out = []; seen = set()
        for rid in rids:
            if rid in seen: continue
            seen.add(rid); row = self.table.get(rid)
            if row is not None and row[ci] == key: out.append((rid, row))
        return out

    def lookup(self, column, key):
        if column not in self.indexes: raise KeyError(f"no index on {column!r}")
        self.reads += 1
        return self._visible(column, key, self.indexes[column].search(key))

    def range_lookup(self, column, low=None, high=None):
        if column not in self.indexes: raise KeyError(f"no index on {column!r}")
        self.reads += 1; pairs = self.indexes[column].range_search(low, high); out = []; seen = set(); ci = self.table.schema.col_index(column)
        for key, rid in pairs:
            if rid in seen: continue
            seen.add(rid); row = self.table.get(rid)
            if row is not None and row[ci] == key: out.append((rid, row))
        return out

    def stats(self):
        return {"indexed_columns": len(self.indexes), "reads": self.reads, "writes": self.writes,
                "indexes": {c: d["kind"] for c, d in self.definitions.items()}}
