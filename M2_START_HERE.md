# MiniDB M1 + M2 — Start Here

## Environment

Python 3.10+. Install the optional Fernet dependency for the encryption test:

```powershell
python -m pip install -r requirements.txt
```

## Run from this folder (repository root)

```powershell
python -m unittest discover -s tests -v
python demo.py
python demo_m2_retail.py
```

Expected validation in the development environment: **37 tests** (23 M1 + 14 M2). Re-run on your Windows machine and keep the terminal output for your submission evidence.

## Reproduce M2 read/write/index benchmark

```powershell
python bench/run_m2_benchmarks.py --rows 5000 --queries 50 --runs 5 --seed 302 --output results/m2_benchmark.csv
```

This records B+ Tree vs LSM build time, serialized and allocated-page size, indexed equality lookup vs full scan, indexed insert batches and indexed key updates. It asserts indexed answers against table contents. Use the CSV generated on your own machine as the target-machine evidence.

## Project documents

- `README.md` — setup, architecture and public API.
- `docs/M1_DESIGN.md`, `docs/M1_REPORT.md` — M1 design and measured evidence.
- `docs/M2_DESIGN.md`, `docs/M2_REPORT.md`, `docs/M2_STATUS.md` — M2 architecture, rubric mapping, results and limitations.
- `M1_M2_COMPLETION_CHECKLIST.md` — completion checklist.

## Important notes

- Index snapshots are persisted in DiskManager pages through BufferPool at checkpoint; this is snapshot persistence, not WAL/ARIES crash recovery.
- Fernet encryption is optional and applies to object files only. Install requirements and keep the generated key outside the database directory.
- B+ Tree delete rebuilds the tree (O(n)); LSM compaction is synchronous/manual. These limitations are disclosed in the report.
- This package covers M1 and M2 only. Do not claim M3/M4/M5/M6 requirements are completed.
