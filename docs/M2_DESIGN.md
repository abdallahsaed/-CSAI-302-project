# MiniDB — Milestone 2: Indexing and Object Storage

## Scope and compatibility

M2 extends the M1 engine without replacing its DiskManager, BufferPool,
page format, RowTable, ColumnTable, or replacement policies. The selected
application workload is retail products, orders, and inventory.

## Architecture

- `Database` remains the public facade; `IndexedTable` maintains indexes on CRUD.
- `IndexManager` owns secondary indexes and validates returned RIDs against live rows.
- `BPlusTree` supports duplicate keys, equality/range scans, linked leaves, and entry deletion.
- `LSMTree` supports a MemTable, immutable sorted runs, flush, tombstones, and explicit compaction.
- `ObjectStore` stores binary objects under `<database path>/objects` using atomic replacement.
- Index snapshots are serialized into ordinary pages allocated by `DiskManager` and accessed through `BufferPool`. The catalog records each index's page IDs and byte length.

## Persistent index-page format

Each index snapshot is serialized with Python pickle and split across pages.
Every page begins with an 8-byte header: 4-byte magic `IDX2` and a 32-bit
payload length, followed by the chunk. `catalog.json` stores the ordered page
IDs and total serialized size for each table/column index. On checkpoint:

1. Serialize each current index and allocate/write its pages through BufferPool.
2. Flush pages and sync the database file.
3. Atomically replace the catalog with the new page references.
4. Reclaim pages from the previous catalog generation.

On reopen, the catalog directs MiniDB to read and deserialize the saved index
pages. Older catalogs without `index_storage` remain compatible and rebuild
indexes from table rows. Pickle is intended only for trusted database files;
never open a database file supplied by an untrusted party.

This is page-backed persistence, but it is a snapshot-based index format,
not a node-per-page B+ Tree or leveled on-disk LSM implementation. It also
is not a substitute for WAL/transactional crash recovery (M4).

## B+ Tree deletion

`delete(key, rid)` removes exactly one entry and rebuilds a compact tree from
the remaining leaf entries. This restores valid separator keys and occupancy
without implementing in-place sibling redistribution. It is correct for this
teaching implementation but has O(n) deletion cost and should not be described
as production-grade incremental B+ Tree deletion.

## LSM compaction

`compact()` flushes the MemTable, merges runs from oldest to newest, retains
the newest operation for each `(key, RID)`, removes tombstoned entries, and
writes one sorted run. Compaction is explicit/synchronous; there is no
background scheduler, Bloom filter, leveled policy, or concurrent writer model.

## Object encryption

The default `IdentityCodec` stores plaintext and does not encrypt. For
confidentiality, callers can inject `FernetCodec` from `minidb`. Fernet uses
authenticated encryption provided by `cryptography`; generate and protect a
key with `FernetCodec.generate_key()`, and pass the same key on later opens.
The key is deliberately not stored in the database catalog. Losing it makes
encrypted objects unrecoverable. Install the optional dependency with
`pip install -r requirements.txt`.

## Public API

```python
from minidb import Database, FernetCodec

db = Database("retail_db")
products = db.create_table("products", [("product_id", "INT"),
    ("name", "TEXT"), ("category", "TEXT"), ("price", "FLOAT")], layout="row")
products.insert((101, "Keyboard", "Accessories", 35.0))
db.create_index("products", "product_id", kind="btree")
db.create_index("products", "category", kind="lsm")
db.index_lookup("products", "product_id", 101)
db.index_range("products", "product_id", 100, 200)
db.compact_index("products", "category")
db.checkpoint()
db.close()

# Optional encryption (keep this key outside the database directory)
key = FernetCodec.generate_key()
secure_db = Database("secure_retail", object_codec=FernetCodec(key))
secure_db.put_object("product-images/101", b"image bytes")
secure_db.close()
```

## Workload selection

`kind="auto"` selects B+ Tree for read-oriented/default workloads and LSM
for `workload="write"`. This is a deterministic declared-workload rule, not
an adaptive optimizer. Benchmark results should guide workload selection.

## Limitations

- Index snapshots are page-backed, but not incrementally updated node/run pages.
- Snapshot serialization currently uses pickle and assumes trusted local files.
- B+ Tree deletion rebuilds the tree and is O(n).
- LSM compaction is synchronous and explicit; no Bloom filters or background jobs.
- Object blobs remain separate files, not M1 data pages.
- Fernet keys must be managed externally and supplied on every database open.
- `select_where` supports only `=` and `between`, not full SQL.
- No M4 WAL, transactions, isolation, or crash recovery is implemented.

## M1/M2 integration verification

- The M1 `DiskManager` remains the sole allocator and page-I/O owner; M2 does not introduce a second database file for index pages.
- `BufferPool.fetch_page/new_page/unpin_page/flush_all` is used for index snapshot page I/O.
- `catalog.json` is atomically replaced only after page flush and disk sync; old snapshot pages are freed after the new catalog is durable. This is checkpoint ordering, not WAL-based crash atomicity.
- Restart integration tests cover B+ Tree and LSM equality/range lookup, CRUD after reopening, another close/reopen, and old-index-page reclamation.
- Object bytes are separate files under `objects/`; `FernetCodec` encrypts object payloads only. Keys are external and must be supplied again on reopen.
