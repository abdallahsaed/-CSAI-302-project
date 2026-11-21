"""Table schemas and row (de)serialisation.

Types: INT (int64), FLOAT (float64), TEXT (UTF-8, <= 65535 bytes).
Row record format (row layout):  [fixed-width fields packed] [u16 len + bytes]*  per TEXT column.
"""
import struct

from .errors import SchemaError

INT, FLOAT, TEXT = "INT", "FLOAT", "TEXT"
FIXED = {INT: ("q", 8), FLOAT: ("d", 8)}
_U16 = struct.Struct("<H")
_I64_MIN, _I64_MAX = -(1 << 63), (1 << 63) - 1


class Schema:
    def __init__(self, columns):
        cols = []
        for c in columns:
            name, typ = (c["name"], c["type"]) if isinstance(c, dict) else c
            typ = typ.upper()
            if typ not in (INT, FLOAT, TEXT):
                raise SchemaError(f"unsupported type {typ!r} for column {name!r}")
            cols.append((name, typ))
        if not cols:
            raise SchemaError("a table needs at least one column")
        names = [n for n, _ in cols]
        if len(set(names)) != len(names):
            raise SchemaError("duplicate column names")
        self.columns = cols
        self.names = names
        self.types = [t for _, t in cols]
        self.index = {n: i for i, n in enumerate(names)}
        self.fixed_idx = [i for i, t in enumerate(self.types) if t in FIXED]
        self.text_idx = [i for i, t in enumerate(self.types) if t == TEXT]
        self._fixed = struct.Struct("<" + "".join(FIXED[self.types[i]][0] for i in self.fixed_idx))
        self.ncols = len(cols)

    # ------------------------------------------------------------------ helpers
    def col_index(self, name):
        try:
            return self.index[name]
        except KeyError:
            raise SchemaError(f"unknown column {name!r}") from None

    def to_dict(self):
        return [{"name": n, "type": t} for n, t in self.columns]

    @classmethod
    def from_dict(cls, d):
        return cls(d)

    # ------------------------------------------------------------------ validation
    def validate(self, row):
        if len(row) != self.ncols:
            raise SchemaError(f"expected {self.ncols} values, got {len(row)}")
        out = []
        for v, t, n in zip(row, self.types, self.names):
            if t == INT:
                if type(v) is not int or not (_I64_MIN <= v <= _I64_MAX):
                    raise SchemaError(f"column {n!r} expects a 64-bit INT, got {v!r}")
            elif t == FLOAT:
                if type(v) is int:
                    v = float(v)
                elif type(v) is not float:
                    raise SchemaError(f"column {n!r} expects FLOAT, got {v!r}")
            else:
                if type(v) is not str:
                    raise SchemaError(f"column {n!r} expects TEXT, got {v!r}")
                if len(v.encode("utf-8")) > 65535:
                    raise SchemaError(f"TEXT value for {n!r} exceeds 65535 bytes")
            out.append(v)
        return tuple(out)

    # ------------------------------------------------------------------ row codec
    def encode(self, row):
        parts = [self._fixed.pack(*[row[i] for i in self.fixed_idx])]
        for i in self.text_idx:
            b = row[i].encode("utf-8")
            parts.append(_U16.pack(len(b)))
            parts.append(b)
        return b"".join(parts)

    def decode(self, buf, off=0):
        vals = [None] * self.ncols
        fv = self._fixed.unpack_from(buf, off)
        off += self._fixed.size
        for k, i in enumerate(self.fixed_idx):
            vals[i] = fv[k]
        for i in self.text_idx:
            ln = _U16.unpack_from(buf, off)[0]
            off += 2
            vals[i] = bytes(buf[off:off + ln]).decode("utf-8")
            off += ln
        return tuple(vals)
