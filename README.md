# MiniDB

A small educational database storage engine written in Python, built for **CSAI 302**.
MiniDB implements the layers of a real database from the ground up: slotted pages, a disk manager, a buffer pool with pluggable replacement policies, row and column table layouts, secondary indexes (B+ Tree and LSM Tree), and a separate binary object store.

> **Milestones covered:** M1 (Storage Engine Foundation) and M2 (Advanced Indexing and Object Storage).

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Running the Demos](#running-the-demos)
- [Testing](#testing)
- [Benchmarks and Results](#benchmarks-and-results)
- [Repository Structure](#repository-structure)
- [On-Disk Layout](#on-disk-layout)
- [Limitations](#limitations)
- [Documentation](#documentation)
- [Team](#team)

---

## Features

### Milestone 1 — Storage Engine Foundation

- **Slotted pages** for storing variable-length records.
- **Disk manager** for fixed-size page I/O, page allocation and a free-page list.
- **Buffer pool** with page pinning, dirty-page tracking, eviction and I/O statistics.
- **Replacement policies:** LRU, FIFO, CLOCK and MRU.
- **Row-oriented** tables (full records stored together) and **column-oriented** tables (each column in its own set of pages).
- **Schema layer** with column definitions, type validation and row serialization.
- **Catalog** persisted to `catalog.json`, with checkpointing.

### Milestone 2 — Advanced Indexing and Object Storage

- **B+ Tree** index, suited to point lookups and range scans.
- **LSM Tree** index, suited to write-heavy workloads.
- **IndexManager** and **IndexedTable**, which keep secondary indexes in sync during supported data operations.
- **Index persistence:** index snapshots are serialized into ordinary database pages and restored from catalog metadata when the database is reopened.
- **Object store** for binary blobs, kept outside the main page file, with an `IdentityCodec` and an optional `FernetCodec` for encrypting object payloads.

---

## Architecture

```
                     Application / User
                             |
                             v
                       Database API
                       database.py
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
          Table Layer     Index Layer    Object Store
              |              |              |
       +------+------+       |          objects/
       |             |       |
       v             v       v
    RowTable     ColumnTable IndexManager
       |             |       |
       |             |   +---+---+
       |             |   |       |
       |             |   v       v
       |             | B+ Tree  LSM Tree
       |             |   |       |
       +------+------+---+-------+
              |
              v
          BufferPool
              |
       Replacer Policies
       LRU / FIFO / CLOCK / MRU
              |
              v
          DiskManager
              |
              v
            data.db

       Catalog metadata: catalog.json
```

| Layer | Module(s) | Responsibility |
|---|---|---|
| Public API | `database.py` | Table creation/deletion, catalog load/save, checkpointing, index operations, statistics |
| Tables | `row_table.py`, `column_table.py`, `indexed_table.py` | Row/column storage and index maintenance |
| Indexes | `indexes.py` | `IndexManager`, `BPlusTree`, `LSMTree` |
| Objects | `object_store.py` | Binary object storage and codecs |
| Caching | `buffer_pool.py`, `replacer.py` | In-memory page cache and eviction policies |
| Storage | `page.py`, `disk.py` | Slotted pages and page-level disk I/O |
| Support | `schema.py`, `config.py`, `errors.py`, `table.py` | Schemas, configuration, exceptions, table interface |

---

## Installation

```bash
git clone https://github.com/abdallahsaed/-CSAI-302-project.git
cd -CSAI-302-project
pip install -r requirements.txt
```

The core engine uses the Python standard library. `requirements.txt` lists the optional dependencies (for example, those used by encryption support and benchmark plotting).

---

## Quick Start

```python
import tempfile
from minidb import Database

with tempfile.TemporaryDirectory() as path:
    db = Database(path, pool_size=16)

    # Create a row-oriented table
    products = db.create_table(
        "products",
        [("product_id", "INT"), ("name", "TEXT"), ("category", "TEXT"),
         ("price", "FLOAT"), ("stock", "INT")],
        layout="row",            # use "column" for column-oriented storage
    )

    products.insert_many([
        (101, "Keyboard",  "Accessories", 35.0, 20),
        (102, "Mouse",     "Accessories", 15.0, 50),
        (103, "Monitor",   "Displays",   180.0, 12),
        (104, "USB Cable", "Accessories",  5.0, 100),
    ])

    # Secondary indexes
    db.create_index("products", "product_id", "btree")   # B+ Tree
    db.create_index("products", "category",   "lsm")     # LSM Tree

    print(db.index_lookup("products", "product_id", 103))
    print(db.index_range("products", "product_id", 101, 103))
    print(db.index_lookup("products", "category", "Accessories"))

    # Object store
    db.put_object("product-images/101", b"demo-image-bytes")
    print(db.get_object("product-images/101"))

    print(db.index_stats("products"))
    db.close()
```

### Main API at a glance

| Method | Purpose |
|---|---|
| `Database(path, pool_size=...)` | Open or create a database in a directory |
| `db.create_table(name, columns, layout="row" \| "column")` | Create a table |
| `table.insert_many(rows)` | Insert multiple rows |
| `db.create_index(table, column, "btree" \| "lsm")` | Build a secondary index |
| `db.index_lookup(table, column, key)` | Point lookup through an index |
| `db.index_range(table, column, low, high)` | Range scan through a B+ Tree index |
| `db.index_stats(table)` | Index statistics |
| `db.put_object(key, bytes)` / `db.get_object(key)` | Store and retrieve binary objects |
| `db.close()` | Flush and close the database |

---

## Running the Demos

```bash
python demo.py              # M1: storage engine end-to-end demonstration
python demo_m2_retail.py    # M2: retail orders demo (indexes + object store)
```

---

## Testing

Run the full test suite:

```bash
python -m unittest discover -s tests -v
```

| Test file | Covers |
|---|---|
| `tests/test_storage.py` | M1 storage engine correctness |
| `tests/test_m2.py` | M2 indexing and object storage |

---

## Benchmarks and Results

| Script | Purpose |
|---|---|
| `bench/run_benchmarks.py` | M1 workloads (replacement policies, pool size, row vs. column) |
| `bench/run_m2_benchmarks.py` | M2 index benchmarks |
| `bench/report.py` | Generates the M1 benchmark report and plots |

```bash
python bench/run_benchmarks.py
python bench/report.py
python bench/run_m2_benchmarks.py
```

Outputs are written to `results/`:

| Experiment | Output |
|---|---|
| E1 — Replacement policy comparison | `e1_policy_comparison.png` |
| E2 — Hit ratio vs. buffer pool size | `e2_hit_ratio_vs_pool_size.png` |
| E3 — Insert cost and space, row vs. column | `e3_insert_and_space.png`, `e3_row_vs_column.png` |
| Raw data | `raw_results.json`, `raw_runs.csv` |
| M2 index benchmarks | `m2_benchmark.csv`, `m2_smoke.csv` |
| Written summary | `summary.md` |

---

## Repository Structure

```
CSAI-302-project/
├── minidb/                 # Core database engine
│   ├── config.py           # Engine configuration, page-size defaults
│   ├── errors.py           # Custom exceptions
│   ├── schema.py           # Schema, validation, row encoding
│   ├── table.py            # Common table interface
│   ├── disk.py             # Page-level disk I/O
│   ├── page.py             # Slotted-page implementation
│   ├── buffer_pool.py      # Page cache and management
│   ├── replacer.py         # LRU, FIFO, CLOCK, MRU
│   ├── row_table.py        # Row-oriented storage
│   ├── column_table.py     # Column-oriented storage
│   ├── database.py         # Database facade and catalog
│   ├── indexed_table.py    # Table wrapper with index maintenance
│   ├── indexes.py          # IndexManager, B+ Tree, LSM Tree
│   └── object_store.py     # Binary objects and codecs
├── tests/                  # test_storage.py, test_m2.py
├── bench/                  # Benchmarks and reporting
├── docs/                   # Design documents and reports
├── results/                # Experimental outputs
├── demo.py                 # M1 demo
├── demo_m2_retail.py       # M2 demo
├── requirements.txt
├── M1_M2_COMPLETION_CHECKLIST.md
└── M2_START_HERE.md
```

---

## On-Disk Layout

A database directory created by MiniDB looks like this:

```
my_database/
├── data.db          # fixed-size pages managed by DiskManager
├── catalog.json     # table metadata, schemas, layouts, index definitions
└── objects/
    ├── <object-hash>.obj
    └── ...
```

Object encryption (when enabled) applies to object payloads only. It does not encrypt the full database file or the index pages.

---

## Limitations

MiniDB is an educational project. The following are **not** implemented:

- Full SQL query processing
- Transactions, WAL and ARIES recovery
- Multi-version concurrency control or full isolation
- Distributed storage and consensus
- Adaptive runtime query or index optimization

---

## Documentation

| Document | Description |
|---|---|
| [`docs/M1_DESIGN.md`](docs/M1_DESIGN.md) | Milestone 1 design decisions |
| [`docs/M1_REPORT.md`](docs/M1_REPORT.md) | Milestone 1 report |
| [`docs/M2_DESIGN.md`](docs/M2_DESIGN.md) | Milestone 2 design decisions |
| [`docs/M2_REPORT.md`](docs/M2_REPORT.md) | Milestone 2 report |
| [`docs/M2_STATUS.md`](docs/M2_STATUS.md) | Milestone 2 status |
| [`docs/PROJECT_STRUCTURE.md`](docs/PROJECT_STRUCTURE.md) | Repository organization |
| [`M1_M2_COMPLETION_CHECKLIST.md`](M1_M2_COMPLETION_CHECKLIST.md) | Completion checklist |

---

## Team

CSAI 302 course project.

| Name | Role |
|---|---|
| _Your name_ | _e.g. Storage engine / Indexing_ |
| _Teammate_ | _..._ |

_Course instructor / university: add here._
