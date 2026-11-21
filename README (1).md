# MiniDB

A database storage engine built from scratch in Python for **CSAI 302** (Milestones 1 & 2).
No SQLite, PostgreSQL or any other database sits underneath: MiniDB manages its own pages on disk, its own buffer pool, its own table layouts and its own indexes.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Tests](https://img.shields.io/badge/tests-37%20passing-brightgreen)
![Engine deps](https://img.shields.io/badge/engine%20dependencies-stdlib%20only-lightgrey)

---

## Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Getting started](#getting-started)
5. [Usage guide](#usage-guide)
6. [Design decisions](#design-decisions)
7. [Testing](#testing)
8. [Benchmarks](#benchmarks)
9. [Repository structure](#repository-structure)
10. [Limitations](#limitations)
11. [Further documentation](#further-documentation)
12. [Team](#team)

---

## Overview

| Milestone | Scope | Status |
|---|---|---|
| **M1 – Storage Engine Foundation** | Paged persistent storage, buffer pool with configurable replacement policies, row-oriented and column-oriented tables | Complete |
| **M2 – Advanced Indexing & Object Storage** | B+ Tree and LSM Tree secondary indexes, workload-based index selection, object store with optional encryption | Complete |
| M3–M6 | Query engine, transactions/WAL/ARIES, distributed storage, full integration | Not part of this package |

The demo domain is **retail orders**: products, orders and inventory.

---

## Features

### M1 – Storage engine

- **Persistent page file** (`data.db`): fixed-size pages (default 4096 B, configurable from 128 B to 32 KiB), header page, free list for reused pages.
- **Buffer pool** with pin counts, dirty-page write-back, and hit/miss/eviction statistics.
- **Four replacement policies**, switchable at runtime: `lru`, `fifo`, `clock`, `mru`.
- **Row layout**: slotted pages with stable record ids `(page_id, slot)`, heap-file chaining, in-place update when it fits.
- **Column layout**: one page set per column, dense fixed-width arrays, per-column page directory, tombstone deletes.
- **One table interface** for both layouts: `insert`, `insert_many`, `get`, `update`, `delete`, `scan`, `rows`, `column_values`.
- **Types:** `INT` (int64), `FLOAT` (float64), `TEXT` (UTF-8, up to 65,535 bytes).
- **Durable checkpoints:** flush pages → `fsync` → atomic catalog replacement.

### M2 – Indexing and objects

- **B+ Tree**: duplicate keys, equality and range scans, linked leaves, exact `(key, RID)` deletion.
- **LSM Tree**: memtable, immutable sorted runs, tombstones, equality/range lookup, explicit compaction.
- **Auto index selection**: `kind="auto"` picks B+ Tree for read workloads and LSM for `workload="write"`.
- **Automatic index maintenance** on insert, update and delete through `IndexedTable`.
- **Persistent indexes**: each index is serialized into ordinary database pages at checkpoint and restored on reopen.
- **SQL-like helper**: `select_where` with `=` and `between`, using an index when one exists.
- **Object store**: binary blobs stored atomically under `objects/`, with a pluggable codec (`IdentityCodec` by default, optional `FernetCodec` for authenticated encryption).

---

## Architecture

```
                Application
                     │
              Database (facade)
        catalog.json · checkpoint · stats
                     │
      ┌──────────────┼───────────────┐
      │              │               │
  IndexedTable   IndexManager     ObjectStore
      │           │       │            │
      │       B+ Tree   LSM Tree    objects/*.obj
      │           │       │       (IdentityCodec | FernetCodec)
 ┌────┴─────┐     │       │
RowTable  ColumnTable     │   (index snapshots are written as pages)
 │            │           │
 └─────┬──────┴───────────┘
       │
   BufferPool ── Replacer: LRU | FIFO | CLOCK | MRU
       │
  DiskManager  (header page, free list, page I/O counters)
       │
    data.db
```

Every layer sits on the one below it. M2 extends M1 without replacing the disk manager, buffer pool, page format or table layouts.

---

## Getting started

**Requirements:** Python 3.10 or newer. The engine uses only the standard library.

```bash
git clone https://github.com/abdallahsaed/-CSAI-302-project.git
cd -CSAI-302-project

# Optional: needed only for FernetCodec encryption (and its test)
python -m pip install -r requirements.txt
```

`requirements.txt` contains a single optional dependency, `cryptography>=42.0`. Benchmark charts additionally use `matplotlib`.

Verify the installation:

```bash
python -m unittest discover -s tests -v    # 37 tests
python demo.py                             # M1 end-to-end demo
python demo_m2_retail.py                   # M2 retail indexing demo
```

---

## Usage guide

### Tables and queries

```python
from minidb import Database

db = Database("mydb", pool_size=256, policy="lru")   # policy: lru | fifo | clock | mru

orders = db.create_table(
    "orders",
    [("id", "INT"), ("price", "FLOAT"), ("cat", "TEXT")],
    layout="column",                                  # or "row"
)

ids = orders.insert_many([(1, 9.5, "books"), (2, 3.0, "toys")])
orders.get(ids[0])                                    # fetch one record
list(orders.rows(["price"]))                          # read selected columns only
sum(orders.column_values("price"))                    # column aggregate
orders.update(ids[1], (2, 4.0, "toys"))
orders.delete(ids[0])

db.close()                                            # checkpoints, then closes
```

`Database` also works as a context manager (`with Database(path) as db:`).

### Indexes

```python
products = db.create_table("products",
    [("product_id", "INT"), ("name", "TEXT"), ("category", "TEXT"),
     ("price", "FLOAT"), ("stock", "INT")], layout="row")
products.insert_many([
    (101, "Keyboard",  "Accessories", 35.0, 20),
    (102, "Mouse",     "Accessories", 15.0, 50),
    (103, "Monitor",   "Displays",   180.0, 12),
])

db.create_index("products", "product_id", kind="btree")
db.create_index("products", "category",   kind="lsm")
db.create_index("products", "stock",      kind="auto", workload="write")   # -> LSM

db.index_lookup("products", "product_id", 103)         # equality
db.index_range("products", "product_id", 101, 103)     # range
db.select_where("products", "product_id", "=", 101)
db.select_where("products", "product_id", "between", (100, 200))

db.compact_index("products", "category")               # LSM only
db.index_stats("products")
db.drop_index("products", "category")
```

Indexes are kept in sync automatically when you insert, update or delete through the table object.

### Object storage and encryption

```python
from minidb import Database, FernetCodec

db = Database("retail_db")
db.put_object("product-images/101", b"image bytes")
db.get_object("product-images/101")
db.delete_object("product-images/101")

# Optional authenticated encryption (requires `cryptography`)
key = FernetCodec.generate_key()          # store this OUTSIDE the database directory
secure = Database("secure_db", object_codec=FernetCodec(key))
secure.put_object("invoice/7", b"...")
secure.close()                            # pass the same key on every later open
```

Any object with `encrypt(bytes)` and `decrypt(bytes)` methods can be used as a codec.

### Buffer pool tuning and statistics

```python
db.configure_buffer_pool("fifo", 64)      # change policy and size on the same data
db.drop_cache()                           # cold start
db.reset_stats()
print(db.stats())                         # hits, misses, hit_ratio, page_reads, ...
print(db.table_info("orders"))            # layout, rows, pages, bytes
```

### Public API summary

| Method | Purpose |
|---|---|
| `Database(path, pool_size=256, policy="lru", page_size=4096, read_latency_us=0.0, object_codec=None)` | Open or create a database directory |
| `create_table(name, columns, layout="row"\|"column")` | Create a table; returns an index-aware table object |
| `table(name)` / `drop_table(name)` / `table_info(name)` | Table access and metadata |
| `create_index(table, column, kind="auto"\|"btree"\|"lsm", workload=None)` | Build a secondary index |
| `index_lookup` / `index_range` / `select_where` | Indexed reads |
| `compact_index` / `drop_index` / `index_stats` | Index maintenance |
| `put_object` / `get_object` / `delete_object` | Binary object storage |
| `configure_buffer_pool` / `drop_cache` / `stats` / `reset_stats` | Buffer pool control |
| `checkpoint()` / `close()` | Persist state |

---

## Design decisions

| Topic | Decision |
|---|---|
| Storage | One `data.db` file of fixed-size pages. Page 0 is the header (magic, page size, page count, free-list head). Table metadata lives in `catalog.json`. |
| Row pages | Slotted pages: slot directory grows forward, records grow backward. Dead slots are reused; the page is compacted only when needed. |
| Column pages | Dense arrays for `INT`/`FLOAT`, length-prefixed values for `TEXT`, binary-searched page directory. A query touching 1 of 7 columns reads about 1/7 of the pages. |
| Buffer pool | Replacer only sees unpinned frames, so a pinned page can never be evicted. Scans release their pin before yielding, so abandoned iterators cannot leak pins. |
| Fair benchmarking | `configure_buffer_pool()` rebuilds the pool in place, so the same on-disk data is measured under every policy. |
| Index persistence | At checkpoint each index is pickled and split across pages with an 8-byte header (`IDX2` magic + payload length). The catalog stores page ids and size. Old index pages are freed after the new catalog is durable. |
| Index selection | Deterministic rule: `read`/default → B+ Tree, `write` → LSM Tree. |
| Objects | Stored as separate files named by the SHA-256 of the key, written atomically (temp file + `os.replace`). |

Full rationale and alternatives are in [`docs/M1_DESIGN.md`](docs/M1_DESIGN.md) and [`docs/M2_DESIGN.md`](docs/M2_DESIGN.md).

### On-disk layout

```
my_database/
├── data.db          # pages: tables + index snapshots
├── catalog.json     # tables, schemas, layouts, index definitions and page references
└── objects/
    └── <sha256-of-key>.obj
```

---

## Testing

```bash
python -m unittest discover -s tests -v
```

**37 tests** (23 M1 + 14 M2), all passing.

| File | Coverage |
|---|---|
| `tests/test_storage.py` | Disk round-trip, restart, free list; each policy's eviction decision; pinned pages never evicted; dirty write-back; slotted-page insert/delete/compaction/update; CRUD on both layouts; restart persistence; page reuse after `drop_table`; identical results across all policies and pool sizes; a randomized differential test (row vs column vs a Python model, 1,500 random operations, 3-frame pool, restarts mid-run, zero pin leaks) |
| `tests/test_m2.py` | B+ Tree duplicates, ranges and deletion after splits; LSM flush, tombstones and compaction; `auto` selection; index restart for both index types; CRUD after reopening; page reclamation; object storage and encryption at rest |

---

## Benchmarks

### M1 – Replacement policies and row vs column

Setup: synthetic orders table, 200,000 rows × 7 columns, 4096 B pages, one warm-up run plus 5 measured runs (mean ± std), cold buffer pool each run. Hit ratio and page I/O are the primary metrics, because wall-clock times include the OS file cache.

```bash
python bench/run_benchmarks.py --quick    # ~20 s smoke run
python bench/run_benchmarks.py            # full run, writes to results/
python -m bench.report results            # regenerate tables and charts
```

**E1 – Policy comparison** (pool = 10% of table pages)

| Workload | Best policy | Hit ratio |
|---|---|---|
| Zipf lookups | LRU | 62.1% (FIFO 57.9%, CLOCK 61.1%, MRU 13.5%) |
| Mixed lookups + scans | LRU | 54.5% |
| Looping scan | MRU | 6.7% (LRU/FIFO/CLOCK 0%) |
| Uniform lookups | all about equal | about 10% |

**E3 – Row vs column** (cold cache, 64 frames, LRU)

| Operation | Row (ms) | Column (ms) | Winner |
|---|---|---|---|
| Full scan, 7 columns | 239.8 | 150.4 | Column, 1.6× |
| `sum(price)`, 1 column | 240.5 | 8.6 | Column, 28× |
| `sum(qty*price)`, 2 columns | 253.0 | 16.8 | Column, 15× |
| Point lookup, full row | 25.1 | 207.5 | Row, 8× |
| Insert 100,000 rows | 349 | 1,391 | Row, 4× |

Column storage wins analytic scans; row storage wins point lookups and inserts. Charts: `results/e1_policy_comparison.png`, `e2_hit_ratio_vs_pool_size.png`, `e3_row_vs_column.png`, `e3_insert_and_space.png`. Full tables are in [`results/summary.md`](results/summary.md).

### M2 – B+ Tree vs LSM Tree

```bash
python bench/run_m2_benchmarks.py --rows 5000 --queries 50 --runs 5 --seed 302 --output results/m2_benchmark.csv
```

5,000 rows, 50 equality lookups, 500 inserts, 10 key updates, 5 trials, seed 302. Every indexed answer is asserted against the table contents.

| Metric | B+ Tree | LSM Tree |
|---|---:|---:|
| Index build (ms) | 20.4 | 8.6 |
| Serialized size (bytes) | 70,602 | 65,088 |
| 50 equality lookups (ms) | **0.17** | 6.74 |
| 500 indexed inserts (ms) | 2.67 | 2.41 |
| 10 indexed key updates (ms) | 67.3 | **0.95** |
| Full-scan baseline, 50 queries (ms) | 113.3 | 113.3 |

The B+ Tree is much faster for read-heavy lookups (about 670× over a full scan). The LSM Tree is faster for updates, because B+ Tree deletion currently rebuilds the tree. LSM lookups are slower here because runs are searched linearly. Timings come from the development container and will differ on other hardware.

---

## Repository structure

```
.
├── minidb/                  # Engine
│   ├── database.py          #   Facade, catalog, checkpointing, index persistence
│   ├── disk.py              #   Page file, header, free list, I/O counters
│   ├── page.py              #   Slotted page
│   ├── buffer_pool.py       #   Frames, pinning, dirty pages, statistics
│   ├── replacer.py          #   LRU / FIFO / CLOCK / MRU
│   ├── row_table.py         #   Row layout
│   ├── column_table.py      #   Column layout
│   ├── table.py             #   Common table interface
│   ├── schema.py            #   Types, validation, row encoding
│   ├── indexes.py           #   BPlusTree, LSMTree, IndexManager
│   ├── indexed_table.py     #   CRUD wrapper that maintains indexes
│   ├── object_store.py      #   ObjectStore, IdentityCodec, FernetCodec
│   ├── config.py            #   PAGE_SIZE and constants
│   └── errors.py            #   MiniDBError and subclasses
├── tests/                   # test_storage.py, test_m2.py
├── bench/                   # run_benchmarks.py, run_m2_benchmarks.py, report.py
├── docs/                    # Design notes and reports (M1, M2)
├── results/                 # Charts, CSVs, raw JSON, summary.md
├── demo.py                  # M1 demo
├── demo_m2_retail.py        # M2 demo
├── requirements.txt
├── M1_M2_COMPLETION_CHECKLIST.md
└── M2_START_HERE.md
```

---

## Limitations

This package covers M1 and M2 only. Known boundaries:

- **No crash atomicity.** Checkpoints are ordered (flush → fsync → atomic catalog swap), but there is no WAL or recovery; a crash between checkpoints can lose recent changes. Planned for M4.
- **Index persistence is snapshot-based**, not node-per-page or run-per-page.
- **Index snapshots use `pickle`.** Only open database files from trusted sources.
- **B+ Tree deletion is O(n)** (rebuilds the tree).
- **LSM compaction is manual and synchronous**; no Bloom filters or leveled compaction.
- **Index selection is rule-based** on a declared workload, not adaptive.
- **Encryption covers object payloads only**, not table or index pages. Fernet keys are never stored by MiniDB; losing the key makes encrypted objects unrecoverable.
- `select_where` supports only `=` and `between`. No full SQL, no NULLs, no joins.
- Rows larger than one page are rejected; objects are the way to store large blobs.
- Single-threaded; no locking protocol.

---

## Further documentation

| Document | Contents |
|---|---|
| [`docs/M1_DESIGN.md`](docs/M1_DESIGN.md) | Page file, buffer pool, row/column layouts, catalog, alternatives considered |
| [`docs/M1_REPORT.md`](docs/M1_REPORT.md) | M1 methodology and results |
| [`docs/M2_DESIGN.md`](docs/M2_DESIGN.md) | Index architecture, page format, encryption, limitations |
| [`docs/M2_REPORT.md`](docs/M2_REPORT.md) | Rubric mapping and M2 benchmark interpretation |
| [`docs/M2_STATUS.md`](docs/M2_STATUS.md) | Implementation status and boundaries |
| [`results/summary.md`](results/summary.md) | Full M1 benchmark tables |
| [`M1_M2_COMPLETION_CHECKLIST.md`](M1_M2_COMPLETION_CHECKLIST.md) | Milestone checklist |
| [`M2_START_HERE.md`](M2_START_HERE.md) | Quick run instructions |

---

## Team

CSAI 302 course project.

| Name | Contribution |
|---|---|
| _Add name_ | _e.g. storage engine, buffer pool_ |
| _Add name_ | _e.g. indexing, benchmarks_ |

_Instructor / university: add here._
