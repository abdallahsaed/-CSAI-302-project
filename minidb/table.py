"""Common interface implemented by both storage layouts.

ids:  RowTable    -> record id  = (page_id, slot)
      ColumnTable -> record id  = row ordinal (int)
Both layouts expose the same operations so tests/benchmarks treat them uniformly.
"""


class Table:
    layout = None

    def insert(self, row): raise NotImplementedError          # -> id
    def insert_many(self, rows): raise NotImplementedError    # -> [ids]
    def get(self, rid, columns=None): raise NotImplementedError
    def delete(self, rid): raise NotImplementedError          # -> bool
    def update(self, rid, row): raise NotImplementedError     # -> (possibly new) id
    def scan(self, columns=None): raise NotImplementedError   # -> iter of (id, tuple)
    def rows(self, columns=None): raise NotImplementedError   # -> iter of tuples
    def column_values(self, name): raise NotImplementedError  # -> iter of values
    def count(self): raise NotImplementedError
    def page_ids(self): raise NotImplementedError
    def meta(self): raise NotImplementedError

    def _proj(self, columns):
        """column names -> list of indices (None = all, in schema order)."""
        if columns is None:
            return None
        return [self.schema.col_index(c) for c in columns]
