#!/usr/bin/env python3
"""Reproducible Milestone-1 benchmark suite.

E1  buffer replacement policies   (LRU / FIFO / CLOCK / MRU) x 4 access patterns
E2  buffer-size sweep             hit ratio vs pool size (Zipf workload)
E3  row vs column layout          scans, aggregates, point lookups, insert, space

Protocol: 1 warm-up run (discarded) + N measured runs (default 5); every run uses a cold
buffer pool; the workload for run r is generated once from seed (base_seed + r) and replayed
identically for every policy/layout, so comparisons are paired.  Reported: mean +- std over
measured runs; wall-clock in ms, throughput in ops/s, p50/p95 per-lookup latency in us.

    python bench/run_benchmarks.py                 # full run (defaults)
    python bench/run_benchmarks.py --quick         # small smoke run
"""
import argparse, csv, itertools, json, math, os, platform, random, shutil, statistics, sys, tempfile, time
from operator import itemgetter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from minidb import Database, POLICIES                       # noqa: E402

SCHEMA = [("id", "INT"), ("customer_id", "INT"), ("product_id", "INT"), ("quantity", "INT"),
          ("price", "FLOAT"), ("category", "TEXT"), ("description", "TEXT")]
CATEGORIES = ["electronics", "books", "toys", "grocery", "clothing", "garden", "sports", "beauty"]
WORDS = ("fast durable compact premium classic eco smart portable light heavy modern vintage "
         "wireless silent rapid ergonomic ultra basic deluxe sturdy").split()
POLICY_ORDER = ["lru", "fifo", "clock", "mru"]
perf = time.perf_counter


# ----------------------------------------------------------------------------- helpers
def gen_rows(n, seed):
    r = random.Random(seed)
    return [(i + 1, r.randrange(1, 20001), r.randrange(1, 5001), r.randrange(1, 11),
             round(r.uniform(1, 500), 2), r.choice(CATEGORIES),
             " ".join(r.choice(WORDS) for _ in range(r.randint(3, 8)))) for i in range(n)]


def mean_std(v):
    return (statistics.fmean(v), statistics.stdev(v) if len(v) > 1 else 0.0)


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))] if s else 0.0


def log(*a):
    print(*a, flush=True)


def env_info():
    mem = None
    try:
        with open("/proc/meminfo") as f:
            mem = round(int(f.readline().split()[1]) / 1024 / 1024, 2)
    except Exception:
        pass
    return {"platform": platform.platform(), "python": sys.version.split()[0],
            "processor": platform.processor() or platform.machine(), "cpu_count": os.cpu_count(),
            "ram_gb": mem, "timer": "time.perf_counter", "concurrency": "1 thread (single client)"}


def snap(db, t0, t1, extra=None):
    s = db.stats()
    d = {"time_ms": (t1 - t0) * 1000, "hit_ratio": s["hit_ratio"], "hits": s["hits"], "misses": s["misses"],
         "page_reads": s["page_reads"], "page_writes": s["page_writes"], "evictions": s["evictions"]}
    d.update(extra or {})
    return d


# ----------------------------------------------------------------------------- workloads (E1/E2)
class Workloads:
    def __init__(self, n_rows, rids, n_ops, seed, s=1.1):
        self.n, self.rids, self.n_ops, self.seed = n_rows, rids, n_ops, seed
        w = [1.0 / (k + 1) ** s for k in range(n_rows)]
        self.cum = list(itertools.accumulate(w))
        perm = list(range(n_rows))
        random.Random(seed).shuffle(perm)           # hot keys scattered over all pages
        self.perm = perm

    def make(self, kind, run_seed):
        r = random.Random(run_seed)
        if kind == "uniform":
            idx = [r.randrange(self.n) for _ in range(self.n_ops)]
            return [(self.rids[i], i + 1) for i in idx]
        if kind == "zipf":
            ranks = r.choices(range(self.n), cum_weights=self.cum, k=self.n_ops)
            return [(self.rids[self.perm[k]], self.perm[k] + 1) for k in ranks]
        if kind == "mixed":                          # hot lookups + periodic full scans (scan pollution)
            ranks = r.choices(range(self.n), cum_weights=self.cum, k=self.n_ops)
            ops = []
            for j, k in enumerate(ranks):
                if j and j % (self.n_ops // 4) == 0:
                    ops.append("scan")
                ops.append((self.rids[self.perm[k]], self.perm[k] + 1))
            return ops
        if kind == "scan_loop":
            return ["scan"] * 3
        raise ValueError(kind)


def run_access(db, table, ops, n_rows):
    """Execute a workload on a cold pool; returns metrics + correctness error count."""
    db.drop_cache(); db.reset_stats()
    lat, bad, units, scanned = [], 0, 0, 0
    get = table.get
    t0 = perf()
    for op in ops:
        if op == "scan":
            n = 0
            for _ in table.rows():
                n += 1
            bad += (n != n_rows)
            scanned += n
        else:
            a = perf()
            row = get(op[0])
            lat.append(perf() - a)
            bad += (row is None or row[0] != op[1])
            units += 1
    t1 = perf()
    units = units or scanned          # lookups/s when there are lookups, else rows scanned/s
    extra = {"ops": units, "throughput": units / (t1 - t0), "errors": bad}
    if lat:
        extra.update(p50_us=pct(lat, .5) * 1e6, p95_us=pct(lat, .95) * 1e6)
    return snap(db, t0, t1, extra)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--insert-rows", type=int, default=100_000)
    ap.add_argument("--ops", type=int, default=100_000, help="point lookups per E1/E2 run")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool-frac", type=float, default=0.10, help="E1 pool size as a fraction of table pages")
    ap.add_argument("--layout-pool", type=int, default=64, help="E3 pool frames (same for both layouts)")
    ap.add_argument("--page-size", type=int, default=4096)
    ap.add_argument("--sim-read-latency-us", type=float, default=0.0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.quick:
        a.rows, a.insert_rows, a.ops, a.runs = 8_000, 5_000, 3_000, 2
    out = os.path.abspath(a.out); os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="minidb_bench_")
    total_runs = a.warmup + a.runs
    raw, assertions = [], {}
    config = vars(a).copy()

    def add(exp, run, **kw):
        raw.append({"experiment": exp, "run": run, **kw})

    try:
        # ---------------------------------------------------------------- dataset + databases
        log(f"[setup] generating {a.rows} rows (seed={a.seed})")
        rows = gen_rows(a.rows, a.seed)
        exp_sum_price = math.fsum(r[4] for r in rows)
        exp_sum_qp = math.fsum(r[3] * r[4] for r in rows)
        dbs, ids = {}, {}
        for layout in ("row", "column"):
            db = Database(os.path.join(work, layout), pool_size=1024, page_size=a.page_size,
                          read_latency_us=a.sim_read_latency_us)
            t = db.create_table("orders", SCHEMA, layout)
            t0 = perf(); ids[layout] = t.insert_many(rows); db.checkpoint()
            log(f"[setup] {layout:6s} loaded: {t.num_pages} pages in {perf() - t0:.1f}s")
            dbs[layout] = db
        row_db, col_db = dbs["row"], dbs["column"]
        row_t, col_t = row_db.table("orders"), col_db.table("orders")
        table_pages = {"row": row_t.num_pages, "column": col_t.num_pages}

        # ---------------------------------------------------------------- E1 policies
        pool_frames = max(8, int(a.pool_frac * row_t.num_pages))
        config["e1_pool_frames"] = pool_frames
        wl = Workloads(a.rows, ids["row"], a.ops, a.seed)
        kinds = ["uniform", "zipf", "mixed", "scan_loop"]
        log(f"[E1] policy comparison, pool={pool_frames} frames ({a.pool_frac:.0%} of {row_t.num_pages} pages)")
        row_db.configure_buffer_pool("lru", pool_frames)
        for kind in kinds:
            for r in range(total_runs):
                ops = wl.make(kind, a.seed * 1000 + r)
                order = POLICY_ORDER[r % 4:] + POLICY_ORDER[:r % 4]          # rotate policy order
                for pol in order:
                    row_db.configure_buffer_pool(pol, pool_frames)
                    m = run_access(row_db, row_t, ops, a.rows)
                    if r >= a.warmup:
                        add("E1_policy", r - a.warmup, workload=kind, policy=pol, pool_frames=pool_frames, **m)
            log(f"    {kind} done")

        # ---------------------------------------------------------------- E2 size sweep
        fracs = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]
        log("[E2] buffer-size sweep (zipf)")
        for frac in fracs:
            frames = max(4, int(frac * row_t.num_pages)) if frac < 1 else row_t.num_pages + 8
            for r in range(total_runs):
                ops = wl.make("zipf", a.seed * 1000 + r)
                for pol in POLICY_ORDER:
                    row_db.configure_buffer_pool(pol, frames)
                    m = run_access(row_db, row_t, ops, a.rows)
                    if r >= a.warmup:
                        add("E2_size", r - a.warmup, workload="zipf", policy=pol, pool_frames=frames,
                            pool_pct=100 * frames / row_t.num_pages, **m)
        row_db.configure_buffer_pool("lru", 1024)

        # ---------------------------------------------------------------- E3 layouts
        log(f"[E3] row vs column layout (pool={a.layout_pool} frames, LRU, cold cache per run)")
        for d in (row_db, col_db):
            d.configure_buffer_pool("lru", a.layout_pool)
        rng = random.Random(a.seed + 7)
        probes = [rng.randrange(a.rows) for _ in range(5000)]

        def op_full_scan(db, t, layout):
            n = sum(1 for _ in t.rows()); return n == a.rows, n
        def op_agg1(db, t, layout):
            s = math.fsum(t.column_values("price")); return math.isclose(s, exp_sum_price, rel_tol=1e-9), a.rows
        def op_agg2(db, t, layout):
            s = math.fsum(q * p for q, p in t.rows(["quantity", "price"])); return math.isclose(s, exp_sum_qp, rel_tol=1e-9), a.rows
        def op_point_full(db, t, layout):
            ok = True
            for i in probes:
                ok &= (t.get(ids[layout][i])[0] == i + 1)
            return ok, len(probes)
        def op_point_1col(db, t, layout):
            ok = True
            for i in probes:
                ok &= (t.get(ids[layout][i], ["price"])[0] == rows[i][4])
            return ok, len(probes)
        e3_ops = [("full_scan (7 cols)", op_full_scan), ("sum(price) (1 col)", op_agg1),
                  ("sum(qty*price) (2 cols)", op_agg2), ("point lookup, full row", op_point_full),
                  ("point lookup, 1 col", op_point_1col)]
        ok_all = {}
        for name, fn in e3_ops:
            for r in range(total_runs):
                for layout in (("row", "column") if r % 2 == 0 else ("column", "row")):
                    db = dbs[layout]; t = db.table("orders")
                    db.drop_cache(); db.reset_stats()
                    t0 = perf(); ok, units = fn(db, t, layout); t1 = perf()
                    ok_all[(name, layout)] = ok_all.get((name, layout), True) and ok
                    if r >= a.warmup:
                        add("E3_layout", r - a.warmup, op=name, layout=layout,
                            **snap(db, t0, t1, {"ops": units, "throughput": units / (t1 - t0), "correct": bool(ok)}))
            log(f"    {name} done")
        assertions["E3_results_correct"] = all(ok_all.values())

        # insert throughput (fresh DB each run, row-at-a-time insert())
        log("[E3] insert throughput")
        sub = rows[:a.insert_rows]
        for r in range(total_runs):
            for layout in (("row", "column") if r % 2 == 0 else ("column", "row")):
                path = os.path.join(work, f"ins_{layout}_{r}")
                db = Database(path, pool_size=256, page_size=a.page_size)
                t = db.create_table("orders", SCHEMA, layout)
                t0 = perf()
                for rw in sub:
                    t.insert(rw)
                t1 = perf(); db.checkpoint(); t2 = perf()
                if r >= a.warmup:
                    s = db.stats()
                    add("E3_insert", r - a.warmup, layout=layout, rows=len(sub), time_ms=(t1 - t0) * 1000,
                        throughput=len(sub) / (t1 - t0), checkpoint_ms=(t2 - t1) * 1000,
                        page_writes=s["page_writes"], pages=t.num_pages, file_bytes=db.disk.file_size())
                assert list(itertools.islice(t.rows(), 3)) == [tuple(x) for x in sub[:3]]
                db.close(); shutil.rmtree(path, ignore_errors=True)

        # ---------------------------------------------------------------- persistence check
        log("[check] restart persistence")
        res = {}
        for layout, db in dbs.items():
            db.configure_buffer_pool("lru", 1024)
            before = list(db.table("orders").rows())
            db.close()
            t0 = perf(); db2 = Database(os.path.join(work, layout), pool_size=64, page_size=a.page_size)
            reopen_ms = (perf() - t0) * 1000
            after = list(db2.table("orders").rows())
            res[layout] = {"identical_after_restart": before == after and len(after) == a.rows,
                           "reopen_ms": reopen_ms, "file_bytes": db2.disk.file_size()}
            db2.close()
        assertions["restart"] = res
        assertions["E1_E2_lookup_errors"] = sum(x.get("errors", 0) for x in raw)
        assertions["table_pages"] = table_pages

    finally:
        shutil.rmtree(work, ignore_errors=True)

    # ---------------------------------------------------------------- write outputs
    with open(os.path.join(out, "raw_results.json"), "w") as f:
        json.dump({"environment": env_info(), "config": config, "assertions": assertions, "runs": raw}, f, indent=1)
    keys = sorted({k for r in raw for k in r})
    with open(os.path.join(out, "raw_runs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(raw)
    log(f"[done] raw results -> {out}")
    from bench.report import make_report
    make_report(out)


if __name__ == "__main__":
    main()
