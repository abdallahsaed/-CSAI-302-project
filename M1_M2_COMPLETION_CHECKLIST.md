# M1 + M2 Completion Checklist

## M1 — Storage Engine Foundation (15 marks)
- [x] Custom persistent page storage (`DiskManager`, fixed-size pages, free list).
- [x] Buffer pool with configurable LRU, FIFO, CLOCK and MRU replacement.
- [x] Row-oriented and column-oriented table layouts.
- [x] Correctness tests including restart and randomized differential operations.
- [x] Reproducible replacement-policy and row/column benchmarks with raw results and report.

## M2 — Advanced Indexing System (15 marks)
- [x] B+ Tree equality and range lookup integrated with table RIDs.
- [x] LSM Tree MemTable, sorted runs, flush, tombstones, lookup/range and compaction.
- [x] Documented workload-based auto choice: read/default -> B+ Tree; write -> LSM.
- [x] Object storage and pluggable encryption codec; optional Fernet authenticated encryption.
- [x] Index snapshots persisted in DiskManager pages through BufferPool and restored from catalog.
- [x] Restart tests for equality/range lookup and CRUD after index restore.
- [x] Read-heavy and write-heavy benchmarks, index build cost, serialized size and allocated page size.
- [x] M2 report maps implementation and evidence to the official rubric.

## Combined verification
- [x] 37 unit/integration tests pass in the development environment (23 M1 + 14 M2).
- [x] M1 and M2 demos run.
- [x] Benchmark CSV included.

## Disclosed limitations
- Index persistence uses checkpointed snapshots, not incremental node/run page updates.
- B+ Tree deletion rebuilds the tree (O(n)); LSM compaction is explicit and synchronous.
- Auto-selection is a declared-workload rule, not an adaptive optimizer.
- Fernet is optional and encrypts object payloads only; the key is external.
- No M3/M4/M5/M6 functionality is claimed here.
