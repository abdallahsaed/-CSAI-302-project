# Milestone 1 — Design notes

## 1. Page file (`disk.py`)
* One file, fixed-size pages, page 0 = header `(magic, page_size, num_pages, free_head)`; page ids ≥ 1 are data pages, so `0` is the null page id.
* `allocate_page()` pops the free list or extends the file lazily (no I/O until the page is first written; a never-written page reads back as zeros). `deallocate_page()` pushes the page on the free list.
* `read_page` / `write_page` are the only physical I/O calls and increment `reads` / `writes` — the **page I/O** metric. They are also the single seam where M2's encryption hook plugs in.
* `sync()` writes the header and `fsync`s. Optional `read_latency_us` simulates device latency for benchmarks.

## 2. Buffer pool (`buffer_pool.py`, `replacer.py`)
* Fixed array of frames, `page_table: page_id → frame`, free-frame list, `pin` count and `dirty` flag per frame.
* `fetch_page` → hit: pin++ and notify replacer; miss: acquire frame (free list, else `replacer.victim()`), write back if dirty, read page. If every frame is pinned → `BufferPoolFullError`.
* The replacer only sees *evictable* (pin = 0) frames, so a pinned page can never be chosen. Interface: `record_load / record_access / set_evictable / victim / remove`.
* Policies, all O(1) amortised: **LRU** (ordered dict, evict oldest access), **FIFO** (same structure, hits don't reorder), **CLOCK** (second-chance ring with reference bits), **MRU** (evict newest — wins on looping scans).
* `reconfigure(policy, pool_size)` flushes and rebuilds the pool in place, so the *same on-disk data* can be benchmarked under every policy (fair comparison). `drop_cache()` gives a cold start.
* Counters: hits, misses, evictions, dirty evictions, new pages (+ the disk counters).
* Scans materialise **one page at a time and release the pin before yielding**, so an abandoned iterator can never leak a pin; tests assert `pinned_count() == 0` after every operation.

## 3. Row layout (`page.py`, `row_table.py`)
Slotted page: `header(10B) | slot directory → … free … ← records`. Slot = `(offset, length)`; offset 0 = dead slot, so record ids `(page, slot)` are stable. Delete marks the slot dead; insert reuses dead slots and compacts the page only when contiguous space is insufficient but total free space is enough. Update is in-place when it fits (shrinks leave a hole, growth compacts); otherwise the record is relocated and the caller receives the new id. Pages are chained (`next_page`) from the table's first page; inserts append to the last page.

## 4. Column layout (`column_table.py`)
Each column owns its pages. `INT/FLOAT` are stored as dense arrays (`capacity = (page_size − 8) / width`), so value *k* of a page is at a computed offset; `TEXT` is `u16 length + bytes`, packed. A per-column directory `[page_id, first_row, count]` locates the page of a row id by binary search. Scans decode one page per requested column and zip aligned batches — a query that touches 1 of 7 columns reads ≈ 1/7 of the pages. `get(id, columns)` fetches only the requested columns. Delete = tombstone set; update of fixed-width columns is in place; an update that changes a TEXT value is delete + insert (new row id).

## 5. Persistence and catalog (`database.py`)
`catalog.json` holds table names, layouts, schemas and per-table metadata (heap-chain head/tail; column directories; tombstones). `checkpoint()` = flush pages → `fsync` → write catalog to a temp file → `os.replace` (atomic). A clean `close()` always checkpoints.

## 6. Design decisions & alternatives considered
| Decision | Alternative | Why |
|---|---|---|
| Slotted pages with stable slot ids | Fixed-length records | Variable TEXT is needed; slot ids keep record ids stable for the M2 indexes |
| Column directory in the catalog | Linked page chain per column | O(log n) row → page, no extra I/O; cost: directory must be checkpointed (crash safety comes with M4's WAL) |
| Pins released before a scan yields | Hold the pin across `yield` | Prevents pin leaks / pool exhaustion with abandoned generators |
| Python, stdlib only | C++/Java | Fast iteration, easy for graders to run; absolute speed is not graded, measured *relative* behaviour is |
| `reconfigure()` on one pool | New file per policy | Identical page layout → paired, fair policy comparison |
