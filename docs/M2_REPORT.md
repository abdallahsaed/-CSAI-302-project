# Milestone 2 — Advanced Indexing System: Results and Rubric Mapping

## Scope from the CSAI 302 project brief

M2 requires (1) refactoring the storage engine to support object storage, (2) encryption hooks,
(3) B+ Tree and LSM Tree indexes, and (4) workload-based index auto-selection. Its deliverable
is simple SQL-like indexed queries, an index correctness test, and read/write performance
comparisons. The detailed rubric allocates 15 marks: B+ Tree 5, LSM Tree 4, workload choice 2,
object storage/encryption hook 1, correctness/performance evidence 3.

## Implementation and evidence

| Rubric criterion | Implementation | Evidence / qualification |
|---|---|---|
| B+ Tree (5) | `minidb/indexes.py::BPlusTree` supports equality, range, duplicates, leaf links and entry deletion; `IndexManager` connects keys to table RIDs. | `test_duplicate_and_range_after_splits`, `test_delete_entry_after_splits`, and integrated restart tests. Deletion currently rebuilds the tree (O(n)); this is a known performance limitation. |
| LSM Tree (4) | MemTable, sorted immutable runs, flush threshold, tombstones, lookup/range, explicit compaction. | LSM unit tests and table integration/restart tests. Compaction is synchronous/manual, without Bloom filters or background levels. |
| Workload choice (2) | `kind="auto"` selects B+ Tree for read/default and LSM Tree for `workload="write"`. | `test_auto_selection`; deterministic declared-workload rule, not an adaptive runtime optimizer. |
| Object storage + encryption hook (1) | Atomic object files, `IdentityCodec`, optional `FernetCodec`. | Codec and encryption-at-rest tests. Fernet requires `pip install -r requirements.txt`; keys are managed outside the DB. Encryption covers objects, not table/index pages. |
| Correctness/performance (3) | Indexed equality/range APIs and benchmark for scan vs indexes, build cost, size, insert and key-update workloads. | `tests/test_m2.py`; `bench/run_m2_benchmarks.py`; `results/m2_benchmark.csv`. Five independent trials and correctness assertions. |

## Reproducible benchmark

Command:

```bash
python bench/run_m2_benchmarks.py --rows 5000 --queries 50 --runs 5 --seed 302 --output results/m2_benchmark.csv
```

Workload: 5,000 synthetic retail-like rows, 50 deterministic equality keys, five independent
trials per index type, fixed seed 302. Read test compares 50 equality lookups against 50 repeated
full scans. Write test inserts 500 new records and updates 10 existing indexed keys. Index build
and serialized/page-allocated footprint are recorded. Each index result is checked against the
authoritative table. Timings below are from the development container (Linux x86_64, Python
3.13.5); they are not a promise of the same timings on student Windows hardware.

| Metric | B+ Tree mean ± stdev | LSM Tree mean ± stdev |
|---|---:|---:|
| Index build (ms) | 20.361 ± 27.025 | 8.634 ± 0.678 |
| Serialized index size (bytes) | 70,602 ± 0 | 65,088 ± 0 |
| Allocated index snapshot pages (bytes) | 73,728 ± 0 | 65,536 ± 0 |
| 50 equality lookups (ms) | 0.170 ± 0.026 | 6.740 ± 0.239 |
| 500 indexed inserts (ms) | 2.673 ± 0.448 | 2.407 ± 0.396 |
| 10 indexed key updates (ms) | 67.328 ± 26.904 | 0.948 ± 0.045 |
| Full-scan baseline for 50 queries (ms) | 113.345 ± 4.791 | same shared baseline |

Interpretation: B+ Tree is substantially faster for read-heavy equality lookups in this workload.
LSM insertion and key-update batches are faster in this test. B+ Tree update cost is dominated by
its current O(n) delete/rebuild implementation; this is explicitly disclosed rather than hidden.
LSM equality lookup is slower here because the educational implementation searches runs linearly.
The page-footprint measure includes full allocated snapshot pages and their headers; serialized
size is the pickle payload length.

## M1 continuity

M2 is layered on the M1 engine without replacing its DiskManager, BufferPool, RowTable,
ColumnTable or replacement policies. M1's existing 23 tests remain in the combined suite.
The M1 report and raw results remain under `docs/M1_REPORT.md` and `results/`. M1's documented
benchmark protocol covers replacement-policy comparisons, hit/miss behavior, row/column layouts,
page I/O and repeated trials.

## Combined verification

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python demo.py
python demo_m2_retail.py
```

At packaging time, the combined suite contains 37 tests: 23 M1 tests and 14 M2 tests.
The demo exercises the retail indexed-query path and object-storage round trip.

## Known limitations / scope boundary

- Index persistence is checkpointed serialized snapshots in DiskManager pages; it is not an incremental page-per-node B+ Tree or page-per-run LSM design. Checkpoint ordering is not WAL/ARIES crash recovery.
- B+ Tree delete rebuilds the full tree.
- LSM compaction is explicit and synchronous; no Bloom filters, leveled compaction policy or background workers.
- Auto selection uses a documented caller-declared workload (`read` vs `write`), not runtime statistics or adaptive cost modeling.
- Object files are separate from table pages. Fernet is optional, object-only encryption; key rotation and key management are outside this milestone.
- SQL-like `select_where` currently supports equality and BETWEEN/range predicates, not full SQL.
- M3 query processing, M4 transactions/WAL/ARIES, and M5 distributed/Raft/vector-search requirements are later milestones and are not claimed as completed by this M1/M2 package.
