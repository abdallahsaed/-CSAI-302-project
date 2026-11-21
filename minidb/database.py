"""Database facade: catalog + disk manager + buffer pool + tables.

Persistence model (Milestone 1)
-------------------------------
* data.db      : all pages of all tables (single file, DiskManager)
* catalog.json : table names, schemas, layouts and per-table metadata
* checkpoint() = flush dirty pages -> fsync data file -> atomically replace catalog.
  Data survive a clean close()/restart.  Crash atomicity (WAL/ARIES) is Milestone 4.
"""
import json
import os

from .buffer_pool import BufferPool
from .column_table import ColumnTable
from .config import PAGE_SIZE
from .disk import DiskManager
from .errors import MiniDBError
from .row_table import RowTable
from .schema import Schema

_LAYOUTS = {"row": RowTable, "column": ColumnTable}


class Database:
    def __init__(self, path, pool_size=256, policy="lru", page_size=PAGE_SIZE, read_latency_us=0.0):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.data_path = os.path.join(path, "data.db")
        self.catalog_path = os.path.join(path, "catalog.json")
        self.disk = DiskManager(self.data_path, page_size, read_latency_us)
        self.pool = BufferPool(self.disk, pool_size, policy)
        self.tables = {}
        self._load_catalog()

    # ------------------------------------------------------------------ catalog
    def _load_catalog(self):
        if not os.path.exists(self.catalog_path):
            return
        with open(self.catalog_path, "r", encoding="utf-8") as f:
            cat = json.load(f)
        if cat["page_size"] != self.disk.page_size:
            raise MiniDBError("catalog page size does not match the data file")
        for name, t in cat["tables"].items():
            self.tables[name] = _LAYOUTS[t["layout"]](name, Schema.from_dict(t["schema"]), self.pool, t["meta"])

    def _save_catalog(self):
        cat = {"version": 1, "page_size": self.disk.page_size,
               "tables": {n: {"layout": t.layout, "schema": t.schema.to_dict(), "meta": t.meta()}
                          for n, t in self.tables.items()}}
        tmp = self.catalog_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cat, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.catalog_path)

    def checkpoint(self):
        self.pool.flush_all()
        self.disk.sync()
        self._save_catalog()

    def close(self):
        self.checkpoint()
        self.disk.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ DDL
    def create_table(self, name, columns, layout="row"):
        if not name or name in self.tables:
            raise MiniDBError(f"invalid or duplicate table name {name!r}")
        if layout not in _LAYOUTS:
            raise MiniDBError(f"layout must be one of {sorted(_LAYOUTS)}")
        t = _LAYOUTS[layout](name, Schema(columns), self.pool)
        self.tables[name] = t
        return t

    def table(self, name):
        try:
            return self.tables[name]
        except KeyError:
            raise MiniDBError(f"no such table {name!r}") from None

    def drop_table(self, name):
        t = self.table(name)
        for pid in list(t.page_ids()):
            self.pool.delete_page(pid)
        del self.tables[name]

    # ------------------------------------------------------------------ buffer pool control / stats
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
