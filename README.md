# MiniDB — CSAI 302 Course Project · Milestone 1: Storage Engine Foundation

A custom storage engine written from scratch (no SQLite/PostgreSQL/… underneath):
paged file storage, a buffer pool with **configurable replacement policies**, and **row-oriented and
column-oriented table layouts**, plus tests and a reproducible benchmark suite.

| M1 requirement (brief)                         | Where                                   | Evidence |
|-----------------------------------------------|------------------------------------------|----------|
| Custom persistent storage, survives restart   | `minidb/disk.py`, `database.py`          | `tests/test_storage.py::test_restart_persistence`, benchmark restart check |
| Buffer pool, configurable replacement policy  | `minidb/buffer_pool.py`, `replacer.py`   | LRU, FIFO (required) + CLOCK, MRU; `configure_buffer_pool(policy, size)` |
| Row-oriented **and** columnar layouts         | `row_table.py` + `page.py`, `column_table.py` | same `Table` interface, differential tests |
| Correctness tests + fair benchmarks           | `tests/`, `bench/`                       | `results/summary.md` + 4 charts |

## 1. Declared decisions (the brief asks teams to document these)

| Topic | Decision |
|---|---|
| Language / runtime | Python ≥ 3.10 (developed on 3.12). **Standard library only** for the engine; `matplotlib` only for benchmark charts. *If the instructor mandates another language, ask before porting — the design (page format, pool, replacer interface) is language-neutral.* |
| Storage format | One data file `data.db`, fixed **4096-byte pages** (configurable 128 B–32 KiB). Page 0 = header (magic, page size, page count, free-list head). Freed pages form a free list. Table metadata lives in `catalog.json`. CSV is never used by the engine. |
| Persistence model | `checkpoint()` = flush dirty pages → `fsync` → atomically replace catalog. `close()` checkpoints. Crash-atomicity is out of scope until M4 (WAL/ARIES). |
| Row layout | Slotted pages (header, slot directory growing forward, records growing backward), pages chained in a heap file, record id = `(page_id, slot)`. |
| Column layout | One page-set per column; fixed-width columns packed densely, TEXT as length-prefixed values; per-column page directory + binary search; record id = row ordinal; deletes are tombstones. |
| Types | `INT` (int64), `FLOAT` (float64), `TEXT` (UTF-8, ≤ 65 535 B). No NULLs yet. |
| Measurement plan | See §4 and `results/summary.md` (datasets, baselines, metrics, 5 measured runs after warm-up, seeds, commands). |

## 2. Architecture

```
            Database  (catalog.json, create/drop table, checkpoint, stats)
                │
     ┌──────────┴───────────┐
 RowTable               ColumnTable            <- same Table interface:
 (page.py: slotted      (per-column pages +       insert / insert_many / get / update /
  pages, heap chain)     page directory)          delete / scan / rows / column_values
     └──────────┬───────────┘
          BufferPool  ── Replacer (LRU | FIFO | CLOCK | MRU)
      fetch / new / unpin(dirty) / flush / reconfigure / drop_cache
                │   hit / miss / eviction counters
          DiskManager  (page 0 header, free list, read_page / write_page, I/O counters)
                │
             data.db   (+ catalog.json)
```
Milestone hooks: M2's B+Tree/LSM pages and object storage sit on `BufferPool`; the encryption hook goes
between `BufferPool` and `DiskManager` (`read_page`/`write_page` are the single choke point).

## 3. Quick start

```bash
python -m unittest discover -s tests -v      # 23 correctness tests (~0.4 s)
python demo.py                               # end-to-end demo (create → load → query → restart)
python bench/run_benchmarks.py --quick       # ~20 s smoke benchmark
python bench/run_benchmarks.py               # full benchmark -> results/ (≈4-5 min on 1 core)
python -m bench.report results               # regenerate tables/charts from raw_results.json
```

```python
from minidb import Database
db = Database("mydb", pool_size=256, policy="lru")       # policy: lru | fifo | clock | mru
t = db.create_table("orders", [("id","INT"),("price","FLOAT"),("cat","TEXT")], layout="column")
ids = t.insert_many([(1, 9.5, "books"), (2, 3.0, "toys")])
t.get(ids[0]); list(t.rows(["price"])); sum(t.column_values("price"))
t.update(ids[1], (2, 4.0, "toys")); t.delete(ids[0])
db.configure_buffer_pool("fifo", 64); print(db.stats())   # hits, misses, hit_ratio, page_reads, ...
db.close()
```

## 4. Tests and measurements

* **Tests** (`tests/test_storage.py`): disk (round-trip, restart, free list, bad ids), each policy's eviction
  decision, pinned pages never evicted, dirty write-back, slotted-page insert/delete/compaction/update,
  CRUD + unicode + validation on **both layouts**, restart persistence, drop-table page reuse, results identical for
  every policy × pool size, and a **randomized differential test** (row table vs column table vs Python model,
  1 500 random ops, 3-frame pool, 256-byte pages, restarts in the middle, all 4 policies, zero pin leaks).
* **Benchmarks** (`bench/`): E1 policy comparison (4 access patterns), E2 hit-ratio vs pool size, E3 row vs column
  (full scan, 1-col and 2-col aggregates, point lookups, insert throughput, space). Cache hit/miss ratio, page I/O,
  wall-clock ms, throughput, p50/p95 latency; 1 warm-up + 5 measured runs, mean ± std; paired workloads; seeds,
  environment and commands recorded in `results/summary.md` / `raw_results.json`.
* Honest caveat: wall-clock numbers include the OS page cache, so **hit ratio and page I/O** are the primary metrics
  (`--sim-read-latency-us N` injects a per-miss device latency if you want time to reflect I/O cost).

#
