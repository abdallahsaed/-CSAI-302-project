"""Transparent CRUD wrapper that keeps secondary indexes in sync."""
class IndexedTable:
    def __init__(self, table, index_manager):
        self._table = table
        self._indexes = index_manager

    def __getattr__(self, name):
        return getattr(self._table, name)

    def insert(self, row):
        validated = self.schema.validate(row)
        rid = self._table.insert(validated)
        self._indexes.on_insert(rid, validated)
        return rid

    def insert_many(self, rows):
        return [self.insert(row) for row in rows]

    def update(self, rid, row):
        old = self._table.get(rid)
        if old is None:
            raise KeyError(f"no live record at {rid}")
        validated = self.schema.validate(row)
        new_rid = self._table.update(rid, validated)
        self._indexes.on_update(rid, new_rid, old, validated)
        return new_rid

    def delete(self, rid):
        old = self._table.get(rid)
        if old is None:
            return False
        deleted = self._table.delete(rid)
        if deleted:
            self._indexes.on_delete(rid, old)
        return deleted

    def scan(self, columns=None):
        return self._table.scan(columns)

    def rows(self, columns=None):
        return self._table.rows(columns)

    def column_values(self, name):
        return self._table.column_values(name)

    def get(self, rid, columns=None):
        return self._table.get(rid, columns)

    def count(self):
        return self._table.count()

    def page_ids(self):
        return self._table.page_ids()

    def meta(self):
        return self._table.meta()
