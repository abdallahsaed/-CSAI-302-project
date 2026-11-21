"""Reproducible M2 read/write/index-build/space benchmark.

Five measured trials are recommended; every trial checks index answers against
an authoritative scan. Run: python bench/run_m2_benchmarks.py --rows 20000 --queries 500 --runs 5
"""
import argparse
import csv
import os
import pickle
import random
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from minidb import Database


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return (time.perf_counter() - start) * 1000.0, result


def serialized_index_size(index):
    return len(pickle.dumps(index, protocol=pickle.HIGHEST_PROTOCOL))


def add(records, metric, method, run, value, unit="ms"):
    records.append({"metric": metric, "method": method, "run": run,
                    "value": float(value), "unit": unit})


def make_db(path, method, rows):
    db = Database(path, pool_size=128, policy="lru")
    table = db.create_table("items", [("id", "INT"), ("category", "TEXT"),
                                      ("price", "FLOAT")], layout="row")
    table.insert_many((i, f"cat-{i % 20}", float(i % 500)) for i in range(rows))
    selected = db.create_index("items", "id", method)
    assert selected == method
    return db, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=20000)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=302)
    ap.add_argument("--output", default="results/m2_benchmark.csv")
    args = ap.parse_args()
    if args.rows < 100 or args.queries < 1 or args.runs < 2:
        ap.error("use rows>=100, queries>=1, runs>=2 (five runs recommended)")
    rng = random.Random(args.seed)
    keys = [rng.randrange(args.rows) for _ in range(args.queries)]
    records = []
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    # Independent fresh databases make build and write trials comparable.
    for run in range(1, args.runs + 1):
        for method in ("btree", "lsm"):
            with tempfile.TemporaryDirectory() as path:
                db = Database(path, pool_size=128, policy="lru")
                table = db.create_table("items", [("id", "INT"), ("category", "TEXT"),
                                                   ("price", "FLOAT")], layout="row")
                base_rids = table.insert_many((i, f"cat-{i % 20}", float(i % 500)) for i in range(args.rows))
                build_ms, _ = timed(lambda: db.create_index("items", "id", method))
                idx = db.index_managers["items"].indexes["id"]
                add(records, "index_build", method, run, build_ms)
                add(records, "index_serialized_size", method, run, serialized_index_size(idx), "bytes")
                # Actual allocated page footprint after checkpoint (includes per-page headers).
                db.checkpoint()
                meta = db._index_storage["items"]["id"]
                add(records, "index_persisted_page_size", method, run,
                    len(meta["page_ids"]) * db.disk.page_size, "bytes")

                # Read-heavy workload: identical equality queries and scan baseline.
                scan_ms, scan_rows = timed(lambda: [
                    next((row for _, row in table.scan() if row[0] == key), None) for key in keys
                ])
                expected = [row for key in keys for _, row in db.index_lookup("items", "id", key)]
                indexed_ms, indexed = timed(lambda: [
                    db.index_lookup("items", "id", key) for key in keys
                ])
                # Compare every indexed result to the scan reference, preserving query order.
                for key, got in zip(keys, indexed):
                    reference = [row for _, row in table.scan() if row[0] == key]
                    assert [r for _, r in got] == reference, (method, key)
                add(records, "read_equality_batch", "full_scan", run, scan_ms)
                add(records, "read_equality_batch", method, run, indexed_ms)

                # Write-heavy workload: indexed insert and update batches; new DB per trial.
                insert_count = max(100, args.rows // 10)
                insert_rows = [(args.rows + i, f"new-{i % 20}", float(i % 500))
                               for i in range(insert_count)]
                insert_ms, inserted_rids = timed(lambda: table.insert_many(insert_rows))
                add(records, "write_insert_batch", method, run, insert_ms)
                # Update a deterministic subset, changing the indexed key as well as payload.
                update_count = min(10, args.rows)
                update_items = [(base_rids[i], i, (args.rows * 3 + i, f"updated-{i % 10}", float(i)))
                                for i in range(update_count)]
                update_ms, _ = timed(lambda: [table.update(rid, row) for rid, _, row in update_items])
                add(records, "write_update_batch", method, run, update_ms)
                # Correctness after write workload: every changed key resolves and old keys vanish.
                for i in range(min(25, update_count)):
                    assert len(db.index_lookup("items", "id", args.rows * 3 + i)) == 1
                    assert db.index_lookup("items", "id", i) == []
                db.close()

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "method", "run", "value", "unit"])
        writer.writeheader(); writer.writerows(records)

    print(f"rows={args.rows}, queries={args.queries}, runs={args.runs}, seed={args.seed}")
    for metric in sorted({r["metric"] for r in records}):
        print("\n" + metric)
        methods = sorted({r["method"] for r in records if r["metric"] == metric})
        for method in methods:
            vals = [r["value"] for r in records if r["metric"] == metric and r["method"] == method]
            mean = statistics.mean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            unit = next(r["unit"] for r in records if r["metric"] == metric and r["method"] == method)
            print(f"  {method:10s}: mean={mean:.3f}, stdev={sd:.3f} {unit}")
    print(f"Raw results: {args.output}")


if __name__ == "__main__":
    main()
