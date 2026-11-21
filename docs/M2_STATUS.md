# M2 Implementation Status — M1/M2 Final Candidate

## Implemented against the official M2 rubric

- B+ Tree: duplicate-key equality lookup, range scan, linked leaves, and exact key/RID deletion; integrated with RowTable and ColumnTable via IndexManager/IndexedTable.
- LSM Tree: MemTable, immutable sorted runs, flush, tombstones, equality/range lookup, and explicit compaction.
- Workload selection: documented deterministic rule (`read`/default -> B+ Tree; `write` -> LSM).
- Object storage: atomic file replacement under the database's `objects/` directory, with codec interface and optional Fernet authenticated encryption.
- Index persistence: checkpoint serializes each index into one or more DiskManager pages accessed through BufferPool; catalog records format, page IDs, page count and payload size; restart loads snapshots from those pages.
- Index lifecycle: checkpoint replaces snapshots and reclaims old snapshot pages after catalog replacement; dropping an index removes its catalog reference and frees old pages on checkpoint.
- Tests: equality/range after restart for both index types, CRUD after loading persisted indexes, another restart, catalog metadata, page reclamation, encryption-at-rest, and all M1 tests.
- Benchmarks: five independent trials for read-heavy and write-heavy workloads; build time, serialized size, allocated page size, indexed lookup vs repeated full scan, insert batch and indexed key updates; correctness assertions accompany timings.

## Combined verification in the development environment

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python demo.py
python demo_m2_retail.py
python bench/run_m2_benchmarks.py --rows 5000 --queries 50 --runs 5 --seed 302 --output results/m2_benchmark.csv
```

Latest validation: **37 tests passed** (23 M1 + 14 M2). Both demos completed. The benchmark CSV is included. Re-run all commands on the target Windows machine before submission; timings vary by hardware and Python version.

## Important implementation boundaries

1. Index persistence is checkpointed serialized snapshots split across BufferPool-managed DiskManager pages. It is not incremental node-per-page B+ Tree or run-per-page LSM persistence.
2. Checkpoint ordering uses flush, fsync and atomic catalog replacement, but it is not WAL/ARIES crash recovery; that is M4.
3. B+ Tree deletion rebuilds the tree (O(n)); it is correct but makes key updates slower.
4. LSM compaction is synchronous and explicit, without Bloom filters or background leveled compaction.
5. Auto-selection is based on declared workload, not an adaptive cost-based optimizer.
6. Object blobs are separate files, not table pages. Fernet encrypts object payloads only; key management is external and the key is not stored in the DB.
7. Pickle snapshots must only be loaded from trusted database files.
8. M3 query engine, M4 transactions/WAL/ARIES, M5 distributed/Raft/vector baseline, and M6 full integration are not claimed as complete by this M1/M2 package.

See `docs/M2_REPORT.md` for rubric mapping, measured results and interpretation; see `M1_M2_COMPLETION_CHECKLIST.md` for a concise milestone checklist.
