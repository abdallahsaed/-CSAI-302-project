#!/usr/bin/env python3
"""End-to-end Milestone-1 demonstration (retail-orders domain).

    python demo.py [--dir demo_db]

Shows: create tables in both layouts -> bulk load -> queries -> buffer-pool statistics
for different replacement policies -> clean shutdown -> restart -> data still there.
"""
import argparse, os, random, shutil

from minidb import Database

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default="demo_db")
args = ap.parse_args()
shutil.rmtree(args.dir, ignore_errors=True)

SCHEMA = [("order_id", "INT"), ("customer_id", "INT"), ("category", "TEXT"), ("quantity", "INT"), ("price", "FLOAT")]
rnd = random.Random(1)
rows = [(i, rnd.randrange(1, 500), rnd.choice(["books", "toys", "grocery", "electronics"]),
         rnd.randrange(1, 6), round(rnd.uniform(2, 300), 2)) for i in range(1, 20001)]


def banner(t):
    print(f"\n=== {t} " + "=" * (66 - len(t)))


banner("1. create + load (same data in a row table and a column table)")
db = Database(args.dir, pool_size=64, policy="lru")
orders_r = db.create_table("orders_row", SCHEMA, layout="row")
orders_c = db.create_table("orders_col", SCHEMA, layout="column")
rid = orders_r.insert_many(rows)
cid = orders_c.insert_many(rows)
for n in ("orders_row", "orders_col"):
    print(" ", n, db.table_info(n))

banner("2. queries (identical answers from both layouts)")
for t in (orders_r, orders_c):
    revenue = sum(q * p for q, p in t.rows(["quantity", "price"]))
    books = sum(1 for _ in (r for r in t.rows(["category"]) if r[0] == "books"))
    print(f"  [{t.layout:6s}] point get -> {t.get(rid[41] if t is orders_r else cid[41])}   "
          f"revenue={revenue:,.2f}   #books={books}")

banner("3. modify: update + delete")
orders_r.update(rid[0], (1, 7, "books", 9, 1.0)); orders_c.update(cid[0], (1, 7, "books", 9, 1.0))
orders_r.delete(rid[1]); orders_c.delete(cid[1])
print("  row count after delete:", orders_r.count(), orders_c.count())

banner("4. buffer pool: same cold scan with each replacement policy (pool=64 frames)")
print(f"  {'policy':6s} {'hit ratio':>10s} {'page reads':>11s}")
for pol in ("lru", "fifo", "clock", "mru"):
    db.configure_buffer_pool(pol, 64)
    db.drop_cache(); db.reset_stats()
    for _ in range(2):                      # two passes: second pass shows policy behaviour
        sum(1 for _ in orders_r.rows())
    s = db.stats()
    print(f"  {pol:6s} {s['hit_ratio']:>9.1%} {s['page_reads']:>11d}")

banner("5. persistence: close, reopen, verify")
before = (list(orders_r.rows()), list(orders_c.rows()))
db.close()
db2 = Database(args.dir, pool_size=32, policy="fifo")
after = (list(db2.table("orders_row").rows()), list(db2.table("orders_col").rows()))
print("  tables after restart:", sorted(db2.tables), " data identical:", before == after)
print("  file size: %.2f MiB" % (db2.disk.file_size() / 2**20))
db2.close()
shutil.rmtree(args.dir, ignore_errors=True)
