# MiniDB — Project Structure & Architecture

This document describes the repository organization and the responsibility of each module in MiniDB, covering Milestone 1 (Storage Engine Foundation) and Milestone 2 (Advanced Indexing and Object Storage).

## 1. Repository Structure

```text
CSAI-302-project/
│
├── minidb/                         # Core database engine
│   ├── __init__.py                 # Public package exports
│   ├── config.py                   # Engine configuration and page-size defaults
│   ├── errors.py                   # Custom MiniDB exceptions
│   ├── schema.py                   # Schema definition, validation, row encoding
│   ├── table.py                    # Common table interface
│   │
│   ├── disk.py                     # Persistent page-level disk I/O
│   ├── page.py                     # Slotted-page implementation
│   ├── buffer_pool.py              # In-memory page cache and page management
│   ├── replacer.py                 # LRU, FIFO, CLOCK and MRU policies
│   │
│   ├── row_table.py                # Row-oriented table storage
│   ├── column_table.py             # Column-oriented table storage
│   │
│   ├── database.py                 # Database facade and catalog management
│   ├── indexed_table.py            # Table wrapper integrating index maintenance
│   ├── indexes.py                  # IndexManager, B+ Tree and LSM Tree
│   └── object_store.py              # Binary object storage and codec support
│
├── tests/                          # Correctness and integration tests
│   ├── __init__.py
│   ├── test_storage.py              # M1 storage engine tests
│   └── test_m2.py                   # M2 indexing and object storage tests
│
├── bench/                          # Benchmark execution and reporting
│   ├── __init__.py
│   ├── run_benchmarks.py            # M1 benchmark workloads
│   ├── run_m2_benchmarks.py         # M2 index benchmarks
│   └── report.py                    # M1 benchmark report generation
│
├── docs/                           # Technical documentation and reports
│   ├── M1_DESIGN.md
│   ├── M1_REPORT.md
│   ├── M2_DESIGN.md
│   ├── M2_REPORT.md
│   ├── M2_STATUS.md
│   └── PROJECT_STRUCTURE.md
│
├── results/                        # Experimental outputs and evidence
│   ├── e1_policy_comparison.png
│   ├── e2_hit_ratio_vs_pool_size.png
│   ├── e3_insert_and_space.png
│   ├── e3_row_vs_column.png
│   ├── raw_results.json
│   ├── raw_runs.csv
│   ├── summary.md
│   ├── m2_benchmark.csv
│   └── m2_smoke.csv
│
├── demo.py                         # M1 end-to-end demonstration
├── demo_m2_retail.py               # M2 retail indexing demonstration
├── requirements.txt                # Optional dependencies
├── M1_M2_COMPLETION_CHECKLIST.md   # Milestone completion checklist
├── M2_START_HERE.md                # M2 implementation guide
├── .gitignore
└── README.md
```

## 2. Core Engine Modules

### Database Management
| File | Responsibility |
|---|---|
| `database.py` | Main public API, table creation/deletion, catalog loading/saving, checkpointing, index operations and database statistics |
| `config.py` | Default page size and engine configuration constants |
| `errors.py` | Database, storage, schema and buffer-pool exceptions |
| `schema.py` | Column definitions, type validation and row serialization/deserialization |
| `table.py` | Common interface shared by row and column table implementations |

### Storage Engine — M1
| File | Responsibility |
|---|---|
| `disk.py` | Reads and writes fixed-size pages, manages page allocation and the free-page list |
| `page.py` | Implements slotted pages for storing variable-length records |
| `buffer_pool.py` | Caches pages in memory, manages pinning, dirty pages, eviction and I/O statistics |
| `replacer.py` | Implements LRU, FIFO, CLOCK and MRU page-replacement policies |
| `row_table.py` | Stores complete records using row-oriented pages |
| `column_table.py` | Stores values in separate column page sets |

### Indexing and Object Storage — M2
| File | Responsibility |
|---|---|
| `indexed_table.py` | Wraps a table and maintains its secondary indexes during supported data operations |
| `indexes.py` | Contains `IndexManager`, `BPlusTree` and `LSMTree` implementations |
| `object_store.py` | Stores binary objects separately and provides `IdentityCodec` and optional `FernetCodec` |

## 3. Architecture and Data Flow

```text
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

## 4. Persistence and Runtime Files

A database created by MiniDB uses a runtime directory similar to:

```text
my_database/
│
├── data.db
├── catalog.json
└── objects/
    ├── <object-hash>.obj
    └── ...
```

- `data.db`: fixed-size pages managed by `DiskManager`.
- `catalog.json`: table metadata, schemas, layouts and index definitions/page references.
- `objects/`: binary objects stored separately by `ObjectStore`.

Index snapshots are serialized into ordinary database pages and restored using catalog metadata during reopening. Object encryption, when enabled, applies to object payloads only; it does not encrypt the complete database file or index pages.

## 5. Testing and Benchmark Organization

| Area | Test / Script |
|---|---|
| M1 storage correctness | `tests/test_storage.py` |
| M2 indexing and object storage | `tests/test_m2.py` |
| M1 performance experiments | `bench/run_benchmarks.py` |
| M2 index performance | `bench/run_m2_benchmarks.py` |
| M1 benchmark reports | `bench/report.py` |
| M1 demo | `demo.py` |
| M2 demo | `demo_m2_retail.py` |

Run the complete test suite:

```bash
python -m unittest discover -s tests -v
```

## 6. Design Boundaries

MiniDB currently implements M1 and M2 functionality. The following are not claimed as completed:

- Full SQL query processing.
- Transactions, WAL and ARIES recovery.
- Multi-version concurrency control or full isolation.
- Distributed storage and consensus.
- Adaptive runtime query or index optimization.

For implementation details and milestone-specific decisions, see `M1_DESIGN.md` and `M2_DESIGN.md` in the `docs/` directory.
