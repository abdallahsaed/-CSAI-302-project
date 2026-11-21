"""Database facade: M1 storage plus M2 indexing and object storage."""
import json
import os
import pickle
import struct

from .buffer_pool import BufferPool
from .column_table import ColumnTable
from .config import PAGE_SIZE
from .disk import DiskManager
from .errors import MiniDBError
from .indexed_table import IndexedTable
from .indexes import IndexManager
from .object_store import ObjectStore
from .row_table import RowTable
from .schema import Schema

_LAYOUTS = {"row": RowTable, "column": ColumnTable}


class Database:
    def __init__(self, path, pool_size=256, policy="lru", page_size=PAGE_SIZE,
                 read_latency_us=0.0, object_codec=None):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.data_path = os.path.join(path, "data.db")
        self.catalog_path = os.path.join(path, "catalog.json")
        self.disk = DiskManager(self.data_path, page_size, read_latency_us)
        self.pool = BufferPool(self.disk, pool_size, policy)
        self.tables = {}
        self.index_managers = {}
        self._wrappers = {}
        self._index_storage = {}
        self.objects = ObjectStore(path, object_codec)
        self._load_catalog()

    def _load_catalog(self):
        if not os.path.exists(self.catalog_path):
            return
        with open(self.catalog_path, "r", encoding="utf-8") as f:
            cat = json.load(f)
        self._index_storage = cat.get("index_storage", {})
        if cat["page_size"] != self.disk.page_size:
            raise MiniDBError("catalog page size does not match the data file")
        for name, t in cat["tables"].items():
            raw = _LAYOUTS[t["layout"]](name, Schema.from_dict(t["schema"]), self.pool, t["meta"])
            self.tables[name] = raw
            manager = IndexManager(raw)
            self.index_managers[name] = manager
            definitions = cat.get("indexes", {}).get(name, {})
            stored = cat.get("index_storage", {}).get(name, {})
            if stored:
                for col, meta in stored.items():
                    payload = self._read_index_blob(meta)
                    manager.indexes[col] = pickle.loads(payload)
                    manager.definitions[col] = definitions[col]
            else:
                manager.rebuild(definitions)

    def _save_catalog(self):
        cat = {
            "version": 2,
            "page_size": self.disk.page_size,
            "tables": {n: {"layout": t.layout, "schema": t.schema.to_dict(), "meta": t.meta()}
                       for n, t in self.tables.items()},
            "indexes": {n: m.definitions for n, m in self.index_managers.items() if m.definitions},
            "index_storage": self._index_storage,
        }
        tmp = self.catalog_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cat, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.catalog_path)

    def _read_index_blob(self, meta):
        chunks = []
        for pid in meta["page_ids"]:
            page = self.pool.fetch_page(pid)
            magic, size = struct.unpack_from("<4sI", page, 0)
            if magic != b"IDX2":
                self.pool.unpin_page(pid)
                raise MiniDBError(f"corrupt persistent index page {pid}")
            if size > self.disk.page_size - 8:
                self.pool.unpin_page(pid)
                raise MiniDBError(f"invalid payload length in persistent index page {pid}")
            chunks.append(bytes(page[8:8 + size]))
            self.pool.unpin_page(pid)
        return b"".join(chunks)[:meta["size"]]

    def _write_index_blob(self, payload):
        capacity = self.disk.page_size - 8
        page_ids = []
        for offset in range(0, len(payload), capacity):
            pid, page = self.pool.new_page()
            chunk = payload[offset:offset + capacity]
            struct.pack_into("<4sI", page, 0, b"IDX2", len(chunk))
            page[8:8 + len(chunk)] = chunk
            self.pool.unpin_page(pid, dirty=True)
            page_ids.append(pid)
        return {"page_ids": page_ids, "size": len(payload),
                "page_count": len(page_ids), "format": "pickle-pages-v1"}

    def checkpoint(self):
        # Persist each index as a sequence of ordinary DiskManager pages. The
        # catalog is replaced only after all index pages are flushed and synced.
        old_storage = getattr(self, "_index_storage", {})
        new_storage = {}
        for table_name, manager in self.index_managers.items():
            table_meta = {}
            for column, index in manager.indexes.items():
                blob = pickle.dumps(index, protocol=pickle.HIGHEST_PROTOCOL)
                table_meta[column] = self._write_index_blob(blob)
            if table_meta: new_storage[table_name] = table_meta
        self._index_storage = new_storage
        self.pool.flush_all()
        self.disk.sync()
        self._save_catalog()
        # Old pages are reclaimed only after the new catalog is durable.
        for table_meta in old_storage.values():
            for meta in table_meta.values():
                for pid in meta.get("page_ids", []):
                    if not any(pid in m.get("page_ids", []) for tm in new_storage.values() for m in tm.values()):
                        self.pool.delete_page(pid)
        self.disk.sync()

    def close(self):
        self.checkpoint()
        self.disk.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def create_table(self, name, columns, layout="row"):
        if not name or name in self.tables:
            raise MiniDBError(f"invalid or duplicate table name {name!r}")
        if layout not in _LAYOUTS:
            raise MiniDBError(f"layout must be one of {sorted(_LAYOUTS)}")
        raw = _LAYOUTS[layout](name, Schema(columns), self.pool)
        self.tables[name] = raw
        self.index_managers[name] = IndexManager(raw)
        wrapped = IndexedTable(raw, self.index_managers[name])
        self._wrappers[name] = wrapped
        return wrapped

    def table(self, name):
        try:
            if name not in self._wrappers:
                self._wrappers[name] = IndexedTable(self.tables[name], self.index_managers[name])
            return self._wrappers[name]
        except KeyError:
            raise MiniDBError(f"no such table {name!r}") from None

    def create_index(self, table_name, column, kind="auto", workload=None):
        if table_name not in self.tables:
            raise MiniDBError(f"no such table {table_name!r}")
        return self.index_managers[table_name].create(column, kind, workload)

    def index_lookup(self, table_name, column, key):
        return self.index_managers[table_name].lookup(column, key)

    def index_range(self, table_name, column, low=None, high=None):
        return self.index_managers[table_name].range_lookup(column, low, high)

    def select_where(self, table_name, column, operator, value):
        """Execute a small SQL-like predicate using an index when available."""
        table = self.table(table_name)
        if operator == "=":
            if column in self.index_managers[table_name].indexes:
                return [row for _, row in self.index_lookup(table_name, column, value)]
            ci = table.schema.col_index(column)
            return [row for _, row in table.scan() if row[ci] == value]
        if operator.lower() in ("between", "range"):
            if not isinstance(value, (tuple, list)) or len(value) != 2:
                raise ValueError("BETWEEN expects a (low, high) pair")
            if column in self.index_managers[table_name].indexes:
                return [row for _, row in self.index_range(table_name, column, value[0], value[1])]
            ci = table.schema.col_index(column)
            low, high = value
            return [row for _, row in table.scan() if low <= row[ci] <= high]
        raise ValueError("supported operators are '=' and 'between'")

    def compact_index(self, table_name, column):
        from .indexes import LSMTree
        idx = self.index_managers[table_name].indexes[column]
        if not isinstance(idx, LSMTree):
            raise ValueError("compaction is available only for LSM indexes")
        return idx.compact()

    def index_stats(self, table_name=None):
        if table_name is not None:
            return self.index_managers[table_name].stats()
        return {name: manager.stats() for name, manager in self.index_managers.items()}

    def drop_index(self, table_name, column):
        manager = self.index_managers[table_name]
        manager.indexes.pop(column, None)
        manager.definitions.pop(column, None)
        self._save_catalog()

    def drop_table(self, name):
        t = self.table(name)
        for pid in list(t.page_ids()):
            self.pool.delete_page(pid)
        del self.tables[name]
        self.index_managers.pop(name, None)
        self._wrappers.pop(name, None)

    def put_object(self, key, data):
        return self.objects.put(key, data)

    def get_object(self, key):
        return self.objects.get(key)

    def delete_object(self, key):
        return self.objects.delete(key)

    def configure_buffer_pool(self, policy=None, pool_size=None):
        self.pool.reconfigure(policy, pool_size)

    def drop_cache(self):
        self.pool.drop_cache()

    def stats(self):
        return self.pool.stats()

    def reset_stats(self):
        self.pool.reset_stats()

    def table_info(self, name):
        t = self.table(name)
        return {"layout": t.layout, "rows": t.count(), "pages": t.num_pages,
                "bytes": t.num_pages * self.disk.page_size}
