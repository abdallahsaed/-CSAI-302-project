class MiniDBError(Exception):
    """Base class for all engine errors."""

class StorageError(MiniDBError):
    """Invalid page id, corrupt file, bad unpin, ..."""

class BufferPoolFullError(MiniDBError):
    """Every frame is pinned, so no victim can be evicted."""

class SchemaError(MiniDBError):
    """Invalid schema or a row that does not match its schema."""
