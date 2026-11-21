# MiniDB — CSAI 302 Course Project (Milestone 1: Storage Engine Foundation)

**Application Domain:** Retail Orders (Customers, Products, Orders, and Inventory)  
**Course:** CSAI 302 — Advanced Database Systems (Fall 2026), Zewail City

A custom, disk-based database storage engine written from scratch in Python (using only the standard library for the core engine). It implements fixed-size paged disk storage, a buffer pool manager with configurable page-replacement policies, dual storage layouts (row-oriented and column-oriented), automated correctness tests, and a reproducible benchmarking suite.

---

## 📋 Declared Design Decisions

| Decision Area | Implementation Choice |
| :--- | :--- |
| **Language & Runtime** | Python ≥ 3.10 (tested on Python 3.12.3). Core engine uses **Python Standard Library only** (`struct`, `json`, `os`, `collections`); `matplotlib` is used strictly for benchmark chart generation. |
| **Disk & Page Format** | Single binary data file (`data.db`) partitioned into fixed **4096-byte pages** (configurable from 128 B to 32 KiB). Page `0` is reserved as the file header (magic bytes, page size, total page count, and free-list head). Deallocated pages are tracked and recycled via a linked free-list. Table schemas and catalog metadata are persisted in `catalog.json`. |
| **Persistence Model** | Explicit `checkpoint()` flushes all dirty frames to disk, calls `fsync`, and atomically updates `catalog.json`. `close()` automatically triggers a checkpoint. (Crash-atomicity via WAL/ARIES is planned for Milestone 4). |
| **Row-Oriented Layout** | **Slotted Pages** (`page.py`, `row_table.py`): 10-byte page header, forward-growing slot directory `(offset, length)`, and backward-growing record payloads chained in a heap file (`next_page`). Record IDs are `(page_id, slot)` with automatic in-page compaction and dead-slot reuse. |
| **Column-Oriented Layout** | **Per-Column Page Sets** (`column_table.py`): Fixed-width types (`INT`, `FLOAT`) are densely packed; `TEXT` is length-prefixed (`u16` + UTF-8 bytes). Locates rows via a per-column page directory and binary search; deletes use tombstones. |
| **Supported Data Types** | `INT` (signed 64-bit integer), `FLOAT` (64-bit IEEE float), and `TEXT` (UTF-8 string up to 65,535 bytes). |

---

## 🏗️ System Architecture

```text
+-------------------------------------------------------------------+
|                   1. Database API & Catalog                       |
|            Database (database.py)  +  Schema (schema.py)          |
|   - Table lifecycle (create/drop) & persistence (checkpoint)      |
|   - Metadata & schemas (INT, FLOAT, TEXT) stored in catalog.json  |
+---------------------------------+---------------------------------+
                                  |
                 +----------------+----------------+
                 v                                 v
+---------------------------------+ +-------------------------------+
|    2A. Row-Oriented Layout      | |  2B. Column-Oriented Layout   |
|  RowTable (row_table.py)        | | ColumnTable (column_table.py) |
|  - Slotted Pages (page.py)      | | - Independent pages per col   |
|  - Chained heap file            | | - Page directory + Tombstones |
+----------------+----------------+ +---------------+---------------+
                 |                                  |
                 +----------------+-----------------+
                                  |  (Unified Table Interface)
                                  v
+-------------------------------------------------------------------+
|                   3. Buffer Pool Manager                          |
|                     BufferPool (buffer_pool.py)                   |
|   - Frame management, Pin/Unpin tracking, Dirty page write-back   |
|   - Runtime policy reconfiguration & performance counters         |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                 4. Page Replacement Policies                      |
|                       Replacer (replacer.py)                      |
|       [ LRU ]   |   [ FIFO ]   |   [ CLOCK ]   |   [ MRU ]        |
+---------------------------------+---------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                   5. Disk Storage Manager                         |
|                     DiskManager (disk.py)                         |
|   - Single file (data.db) split into fixed 4096-byte pages        |
|   - Page 0 Header + Free-list management for recycled pages       |
+-------------------------------------------------------------------+
