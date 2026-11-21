# Milestone 1 - Benchmark summary

All numbers are mean ± standard deviation over 5 measured runs (after 1 warm-up run(s)); every run starts with a cold buffer pool. Raw data: `raw_results.json`, `raw_runs.csv`.

## Reproducibility declaration

| Item | Value |
|---|---|
| OS / platform | Linux-6.18.44-fc-v726-x86_64-with-glibc2.39 |
| Python | 3.12.3 |
| CPU | x86_64 x1 |
| RAM (GB) | 3.9 |
| Timer | time.perf_counter |
| Concurrency | 1 thread (single client) |
| Dataset | synthetic orders table, 200,000 rows x 7 columns (seed 42; run r uses seed 42*1000+r) |
| Page size | 4096 B |
| E1 pool | 457 frames (10% of row-table pages) |
| E3 pool | 64 frames (LRU) for both layouts |
| Lookups per E1/E2 run | 100,000 |
| Simulated read latency | 0.0 us |
| Command | `python bench/run_benchmarks.py` |

> Wall-clock times include the OS file cache (reads that miss the buffer pool are often served from RAM by the OS), so **hit ratio and page I/O counts are the primary, hardware-independent metrics**; times are secondary.

## E1 - Replacement-policy comparison

Pool = 457 frames. Same workload replayed for every policy (paired).

| workload | policy | hit ratio | page reads | time (ms) | throughput (ops/s) | p50 (µs) | p95 (µs) |
|---|---|---|---|---|---|---|---|
| uniform | lru | 9.95 ± 0.06 % | 90,049 ± 62 | 549 ± 19 | 182,365 ± 6,138 | 5.0 ± 0.2 | 6.6 ± 0.5 |
| uniform | fifo | 9.95 ± 0.08 % | 90,047 ± 81 | 512 ± 6 | 195,397 ± 2,380 | 4.8 ± 0.1 | 5.5 ± 0.1 |
| uniform | clock | 9.95 ± 0.08 % | 90,052 ± 83 | 545 ± 15 | 183,490 ± 4,922 | 5.1 ± 0.1 | 6.3 ± 0.5 |
| uniform | mru ★ | 10.03 ± 0.07 % | 89,967 ± 73 | 516 ± 50 | 195,199 ± 16,900 | 4.8 ± 0.5 | 6.0 ± 0.8 |
| zipf | lru ★ | 62.08 ± 0.17 % | 37,924 ± 173 | 370 ± 13 | 270,745 ± 9,543 | 2.5 ± 0.0 | 5.6 ± 0.2 |
| zipf | fifo | 57.88 ± 0.13 % | 42,124 ± 129 | 360 ± 18 | 278,011 ± 13,326 | 2.5 ± 0.1 | 5.3 ± 0.3 |
| zipf | clock | 61.09 ± 0.17 % | 38,913 ± 166 | 371 ± 19 | 270,098 ± 13,321 | 2.6 ± 0.3 | 5.9 ± 0.8 |
| zipf | mru | 13.45 ± 0.23 % | 86,546 ± 229 | 466 ± 7 | 214,401 ± 3,092 | 4.2 ± 0.1 | 6.0 ± 0.5 |
| mixed | lru ★ | 54.45 ± 0.15 % | 51,799 ± 171 | 1,129 ± 14 | 88,557 ± 1,098 | 2.5 ± 0.0 | 5.9 ± 0.2 |
| mixed | fifo | 50.85 ± 0.11 % | 55,893 ± 126 | 1,173 ± 60 | 85,407 ± 4,218 | 2.6 ± 0.1 | 5.7 ± 0.2 |
| mixed | clock | 53.60 ± 0.15 % | 52,761 ± 171 | 1,184 ± 57 | 84,644 ± 4,099 | 2.7 ± 0.3 | 6.5 ± 0.8 |
| mixed | mru | 13.07 ± 0.16 % | 98,858 ± 184 | 1,216 ± 42 | 82,334 ± 2,786 | 4.2 ± 0.1 | 5.7 ± 0.4 |
| scan_loop | lru | 0.00 ± 0.00 % | 13,716 ± 0 | 738 ± 24 | 814,164 ± 25,002 | - | - |
| scan_loop | fifo | 0.00 ± 0.00 % | 13,716 ± 0 | 748 ± 24 | 803,037 ± 25,851 | - | - |
| scan_loop | clock | 0.00 ± 0.00 % | 13,716 ± 0 | 777 ± 42 | 774,011 ± 42,495 | - | - |
| scan_loop | mru ★ | 6.66 ± 0.00 % | 12,802 ± 0 | 737 ± 26 | 815,141 ± 27,792 | - | - |

★ = highest hit ratio for that workload. Throughput unit = lookups/s (for `mixed` this is lookups per second of total time, scans included); rows scanned/s for the pure scan loop.

## E2 - Hit ratio vs buffer-pool size (Zipf lookups)

| pool (% of table pages) | LRU | FIFO | CLOCK | MRU |
|---|---|---|---|---|
| 1.0 | 35.7 ± 0.1 % | 30.8 ± 0.1 % | 34.3 ± 0.1 % | 3.7 ± 0.1 % |
| 2.0 | 43.6 ± 0.1 % | 38.7 ± 0.1 % | 42.3 ± 0.1 % | 4.8 ± 0.1 % |
| 5.0 | 53.9 ± 0.2 % | 49.3 ± 0.1 % | 52.8 ± 0.1 % | 8.1 ± 0.1 % |
| 10.0 | 62.1 ± 0.2 % | 57.9 ± 0.1 % | 61.1 ± 0.2 % | 13.5 ± 0.2 % |
| 20.0 | 71.1 ± 0.1 % | 67.4 ± 0.1 % | 70.2 ± 0.1 % | 24.4 ± 0.2 % |
| 50.0 | 85.3 ± 0.1 % | 82.8 ± 0.1 % | 84.8 ± 0.1 % | 55.4 ± 0.2 % |
| 100.2 | 95.5 ± 0.0 % | 95.5 ± 0.0 % | 95.5 ± 0.0 % | 95.5 ± 0.0 % |

## E3 - Row vs column layout

Cold cache every run, 64 frames, LRU. Row table = 4,572 pages, column table = 4,337 pages.

| operation | row time (ms) | column time (ms) | row/col time ratio | row page reads | column page reads |
|---|---|---|---|---|---|
| full_scan (7 cols) | 239.8 ± 1.2 | 150.4 ± 1.1 | 1.59x (column faster) | 4,572 ± 0 | 4,337 ± 0 |
| sum(price) (1 col) | 240.5 ± 1.1 | 8.6 ± 0.1 | 27.96x (column faster) | 4,572 ± 0 | 392 ± 0 |
| sum(qty*price) (2 cols) | 253.0 ± 0.9 | 16.8 ± 0.1 | 15.06x (column faster) | 4,572 ± 0 | 784 ± 0 |
| point lookup, full row | 25.1 ± 0.7 | 207.5 ± 1.7 | 0.12x (row faster) | 4,928 ± 0 | 34,283 ± 0 |
| point lookup, 1 col | 30.2 ± 0.4 | 22.3 ± 0.4 | 1.36x (column faster) | 4,928 ± 0 | 4,202 ± 0 |

### Insert throughput and space

| layout | insert 100,000 rows: time (ms) | throughput (rows/s) | page writes | pages | file size (MiB) | checkpoint (ms) |
|---|---|---|---|---|---|---|
| row | 349 ± 12 | 286,863 ± 9,887 | 2,285 ± 0 | 2,285 ± 0 | 8.93 | 15 ± 1 |
| column | 1,391 ± 67 | 72,038 ± 3,290 | 2,169 ± 0 | 2,169 ± 0 | 8.48 | 17 ± 1 |

## Correctness assertions recorded alongside the timings

* E1/E2 point lookups returning a wrong/missing row: **0** (expected 0)
* E3 scans / aggregates / lookups match the in-memory reference: **True**
* Restart persistence (close -> reopen -> full scan identical): row: **True** (reopen 0.3 ms), column: **True** (reopen 1.4 ms)
