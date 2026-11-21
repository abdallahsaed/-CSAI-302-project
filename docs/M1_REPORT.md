# Milestone 1 — Results & Discussion

Full tables: `results/summary.md` · raw data: `results/raw_results.json`, `results/raw_runs.csv` · charts: `results/*.png`.
Setup: 200 000-row synthetic orders table (7 columns), 4 KiB pages, 1 core, Python 3.12, 1 warm-up + 5 measured cold-cache runs, mean ± std.

## 1. Replacement policies (E1, E2)

| Workload (pool = 457 frames = 10 % of 4 572 pages) | LRU | FIFO | CLOCK | MRU | Reading |
|---|---|---|---|---|---|
| Uniform random lookups | 9.95 % | 9.95 % | 9.95 % | 10.03 % | No locality → every policy ≈ pool/table = 10 %. (MRU's +0.08 is within noise.) |
| Zipf-skewed lookups | **62.1 %** | 57.9 % | 61.1 % | 13.5 % | Recency matters: FIFO evicts hot pages just because they are old (−4.2 pts); CLOCK tracks LRU within 1 pt. |
| Zipf + periodic full scans | **54.5 %** | 50.9 % | 53.6 % | 13.1 % | Scans pollute the pool (−7.6 pts for LRU) but LRU/CLOCK still lead. |
| Sequential loop, 3 passes | 0 % | 0 % | 0 % | **6.66 %** | Classic sequential flooding: the pool is smaller than the table, so LRU/FIFO/CLOCK evict each page just before its reuse. MRU keeps a stable prefix: 2 × 457 hits / 13 716 accesses = 6.66 % — matches the measurement exactly. |

E2 (Zipf, pool 1 % → 100 %): hit ratio rises monotonically for every policy (LRU 35.7 % → 85.3 % at 50 %), the ranking LRU > CLOCK > FIFO ≫ MRU is stable at every size, and all policies converge to 95.5 % once the pool holds the whole table (only the 4 572 compulsory misses remain). Std-devs are ≤ 0.2 pts, so differences ≥ 1 pt are real; the uniform-workload differences are not.

**Take-away:** no single policy wins everywhere — LRU (or CLOCK, its cheap approximation) for point-lookup/OLTP-style access, MRU-like behaviour only for looping scans. That is the argument for keeping the policy *configurable* (and for scan-resistant policies in M3).

## 2. Row vs column layout (E3; 64-frame pool, cold cache)

| Operation | Row | Column | Page reads (row → column) | Reading |
|---|---|---|---|---|
| `sum(price)` (1 of 7 cols) | 240 ms | **8.6 ms** (28×) | 4 572 → **392** (11.7× fewer) | Column store reads only the needed column. |
| `sum(qty*price)` (2 cols) | 253 ms | **16.8 ms** (15×) | 4 572 → 784 | Cost scales with the number of columns touched. |
| Full scan, all 7 cols | 240 ms | 150 ms (1.6×) | 4 572 → 4 337 | Same I/O (column file is ~5 % smaller: no slot directory). The 1.6× time gap is a **Python artefact** (one bulk `struct.unpack` per page vs. one per row), not an I/O effect. |
| Point lookup, full row ×5 000 | **25 ms** | 208 ms (8× slower) | 4 928 → 34 283 (≈ 7× more) | A column store must touch one page per column to rebuild a row. |
| Point lookup, 1 col ×5 000 | 30 ms | 22 ms | 4 928 → 4 202 | Column store wins even here: its `price` column is small enough that more lookups hit the cache. |
| Insert 100 000 rows, one-at-a-time | **349 ms** (287 k rows/s) | 1 391 ms (72 k rows/s) | pages written 2 285 vs 2 169 | Row insert touches 1 page; column insert touches 7. Batched `insert_many` (used for the initial 200 k-row load) loads the column table in ≈ 0.6 s. |

Matches the textbook expectation: **row = OLTP (whole-row access, inserts); column = analytics (few columns, big scans)**.

## 3. Correctness evidence
* 23 unit/integration tests pass, including a randomized differential test of row vs column vs a Python model (1 500 ops, 3-frame pool, 256-byte pages, restarts, 4 policies).
* Every benchmark run asserts results (0 wrong lookups in 2 M+ lookups; scans/aggregates equal the in-memory reference; restart gives identical data).

## 4. Threats to validity — and how each is handled

| Challenge | Mitigation |
|---|---|
| OS page cache makes "disk" reads cheap, so wall-clock under-states policy differences | Primary metrics are hit ratio and page-read counts (hardware-independent); `--sim-read-latency-us` adds per-miss device latency when time should reflect I/O |
| Python overhead dominates some timings (e.g. full-scan row vs column) | Interpret time together with page I/O; conclusions drawn from I/O counts; design is language-neutral |
| Synthetic data / single machine / 1 core | Seeds, environment and commands recorded; scripts regenerate everything (`python bench/run_benchmarks.py`) |
| Run-to-run noise | 1 warm-up + 5 measured runs, mean ± std, policy/layout order rotated, paired workloads |
| No crash atomicity yet | Documented scope; WAL/ARIES in M4 |

## 5. Hand-off to Milestone 2
`BufferPool` + `DiskManager` are the stable interfaces M2 builds on: B+Tree/LSM nodes are just pages; record ids `(page, slot)` are stable and are what the indexes will store; object storage = overflow pages; the encryption hook goes in `DiskManager.read_page/write_page`.
