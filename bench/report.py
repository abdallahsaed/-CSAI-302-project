"""Turn results/raw_results.json into summary.md (tables) and labeled PNG charts.
Usage:  python -m bench.report [results_dir]"""
import json, os, statistics, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

POLS = ["lru", "fifo", "clock", "mru"]
COL = {"lru": "#1f77b4", "fifo": "#ff7f0e", "clock": "#2ca02c", "mru": "#d62728", "row": "#4c72b0", "column": "#dd8452"}
WL_ORDER = ["uniform", "zipf", "mixed", "scan_loop"]
WL_TITLE = {"uniform": "Uniform random\nlookups", "zipf": "Zipf (skewed)\nlookups",
            "mixed": "Zipf lookups +\nperiodic full scans", "scan_loop": "Sequential scan\nloop (3 passes)"}


def ms(v):
    return (statistics.fmean(v), statistics.stdev(v) if len(v) > 1 else 0.0)


def group(runs, exp, keys, metrics):
    g = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r["experiment"] == exp:
            k = tuple(r[x] for x in keys)
            for m in metrics:
                if m in r:
                    g[k][m].append(r[m])
    return {k: {m: ms(v) for m, v in d.items()} for k, d in g.items()}


def fmt(t, d=2, pctf=False):
    m, s = t
    return f"{m * 100:.{d}f} ± {s * 100:.{d}f} %" if pctf else f"{m:,.{d}f} ± {s:,.{d}f}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def make_report(out):
    with open(os.path.join(out, "raw_results.json")) as f:
        R = json.load(f)
    runs, env, cfg, asr = R["runs"], R["environment"], R["config"], R["assertions"]
    n_runs = cfg["runs"]
    md = ["# Milestone 1 - Benchmark summary", "",
          "All numbers are mean ± standard deviation over %d measured runs (after %d warm-up run(s)); every run starts "
          "with a cold buffer pool. Raw data: `raw_results.json`, `raw_runs.csv`." % (n_runs, cfg["warmup"]), "",
          "## Reproducibility declaration", "",
          table(["Item", "Value"], [
              ["OS / platform", env["platform"]], ["Python", env["python"]], ["CPU", f'{env["processor"]} x{env["cpu_count"]}'],
              ["RAM (GB)", env["ram_gb"]], ["Timer", env["timer"]], ["Concurrency", env["concurrency"]],
              ["Dataset", f'synthetic orders table, {cfg["rows"]:,} rows x 7 columns (seed {cfg["seed"]}; run r uses seed {cfg["seed"]}*1000+r)'],
              ["Page size", f'{cfg["page_size"]} B'], ["E1 pool", f'{cfg["e1_pool_frames"]} frames ({cfg["pool_frac"]:.0%} of row-table pages)'],
              ["E3 pool", f'{cfg["layout_pool"]} frames (LRU) for both layouts'],
              ["Lookups per E1/E2 run", f'{cfg["ops"]:,}'], ["Simulated read latency", f'{cfg["sim_read_latency_us"]} us'],
              ["Command", "`python bench/run_benchmarks.py" + (" --quick`" if cfg.get("quick") else "`")]]), "",
          "> Wall-clock times include the OS file cache (reads that miss the buffer pool are often served from RAM by the "
          "OS), so **hit ratio and page I/O counts are the primary, hardware-independent metrics**; times are secondary.", ""]

    # ------------------------------------------------------------------ E1
    e1 = group(runs, "E1_policy", ["workload", "policy"],
               ["hit_ratio", "page_reads", "evictions", "time_ms", "throughput", "p50_us", "p95_us"])
    md += ["## E1 - Replacement-policy comparison", "",
           f'Pool = {cfg["e1_pool_frames"]} frames. Same workload replayed for every policy (paired).', ""]
    rows = []
    for wl in WL_ORDER:
        best = max(POLS, key=lambda p: e1[(wl, p)]["hit_ratio"][0])
        for p in POLS:
            m = e1[(wl, p)]
            rows.append([wl, p + (" ★" if p == best else ""), fmt(m["hit_ratio"], 2, True), fmt(m["page_reads"], 0),
                         fmt(m["time_ms"], 0), fmt(m["throughput"], 0),
                         fmt(m["p50_us"], 1) if "p50_us" in m else "-", fmt(m["p95_us"], 1) if "p95_us" in m else "-"])
    md += [table(["workload", "policy", "hit ratio", "page reads", "time (ms)", "throughput (ops/s)", "p50 (µs)", "p95 (µs)"], rows),
           "", "★ = highest hit ratio for that workload. Throughput unit = lookups/s (for `mixed` this is lookups per second of total time, scans included); rows scanned/s for the pure scan loop.", ""]

    # chart E1
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.6))
    w = 0.2
    for k, p in enumerate(POLS):
        xs = [i + (k - 1.5) * w for i in range(len(WL_ORDER))]
        ax[0].bar(xs, [100 * e1[(wl, p)]["hit_ratio"][0] for wl in WL_ORDER], w,
                  yerr=[100 * e1[(wl, p)]["hit_ratio"][1] for wl in WL_ORDER], label=p.upper(), color=COL[p], capsize=2)
        ax[1].bar(xs, [e1[(wl, p)]["page_reads"][0] for wl in WL_ORDER], w,
                  yerr=[e1[(wl, p)]["page_reads"][1] for wl in WL_ORDER], label=p.upper(), color=COL[p], capsize=2)
    for a_, yl, ti in ((ax[0], "Buffer hit ratio (%)", "Hit ratio by policy"), (ax[1], "Physical page reads (count, log scale)", "Page I/O by policy")):
        a_.set_xticks(range(len(WL_ORDER))); a_.set_xticklabels([WL_TITLE[x] for x in WL_ORDER], fontsize=8)
        a_.set_ylabel(yl); a_.set_title(ti); a_.legend(title="policy"); a_.grid(axis="y", alpha=.3)
    ax[1].set_yscale("log")
    fig.suptitle(f'E1: replacement policies (pool = {cfg["e1_pool_frames"]} frames, mean ± std of {n_runs} runs)')
    fig.tight_layout(); fig.savefig(os.path.join(out, "e1_policy_comparison.png"), dpi=140); plt.close(fig)

    # ------------------------------------------------------------------ E2
    e2 = group(runs, "E2_size", ["policy", "pool_pct"], ["hit_ratio", "page_reads", "time_ms"])
    pcts = sorted({k[1] for k in e2})
    md += ["## E2 - Hit ratio vs buffer-pool size (Zipf lookups)", ""]
    md += [table(["pool (% of table pages)"] + [p.upper() for p in POLS],
                 [[f"{x:.1f}"] + [fmt(e2[(p, x)]["hit_ratio"], 1, True) for p in POLS] for x in pcts]), ""]
    fig, ax = plt.subplots(figsize=(7, 4.6))
    for p in POLS:
        ax.errorbar(pcts, [100 * e2[(p, x)]["hit_ratio"][0] for x in pcts], yerr=[100 * e2[(p, x)]["hit_ratio"][1] for x in pcts],
                    marker="o", label=p.upper(), color=COL[p], capsize=2)
    ax.set_xscale("log"); ax.set_xlabel("Buffer pool size (% of table pages, log scale)"); ax.set_ylabel("Buffer hit ratio (%)")
    ax.set_title("E2: hit ratio vs pool size (Zipf s=1.1 point lookups)"); ax.grid(alpha=.3); ax.legend(title="policy")
    fig.tight_layout(); fig.savefig(os.path.join(out, "e2_hit_ratio_vs_pool_size.png"), dpi=140); plt.close(fig)

    # ------------------------------------------------------------------ E3
    e3 = group(runs, "E3_layout", ["op", "layout"], ["time_ms", "page_reads", "hit_ratio", "throughput"])
    ops = list(dict.fromkeys(k[0] for k in e3))
    md += ["## E3 - Row vs column layout", "",
           f'Cold cache every run, {cfg["layout_pool"]} frames, LRU. Row table = {asr["table_pages"]["row"]:,} pages, column table = {asr["table_pages"]["column"]:,} pages.', ""]
    rows_ = []
    for o in ops:
        r_, c_ = e3[(o, "row")], e3[(o, "column")]
        sp = r_["time_ms"][0] / c_["time_ms"][0]
        rows_.append([o, fmt(r_["time_ms"], 1), fmt(c_["time_ms"], 1), f"{sp:.2f}x" + (" (column faster)" if sp > 1 else " (row faster)"),
                      fmt(r_["page_reads"], 0), fmt(c_["page_reads"], 0)])
    md += [table(["operation", "row time (ms)", "column time (ms)", "row/col time ratio", "row page reads", "column page reads"], rows_), ""]
    ins = group(runs, "E3_insert", ["layout"], ["time_ms", "throughput", "page_writes", "pages", "file_bytes", "checkpoint_ms"])
    md += ["### Insert throughput and space", "",
           table(["layout", f'insert {cfg["insert_rows"]:,} rows: time (ms)', "throughput (rows/s)", "page writes", "pages", "file size (MiB)", "checkpoint (ms)"],
                 [[l, fmt(ins[(l,)]["time_ms"], 0), fmt(ins[(l,)]["throughput"], 0), fmt(ins[(l,)]["page_writes"], 0),
                   fmt(ins[(l,)]["pages"], 0), f'{ins[(l,)]["file_bytes"][0] / 2**20:.2f}', fmt(ins[(l,)]["checkpoint_ms"], 0)] for l in ("row", "column")]), ""]

    fig, ax = plt.subplots(1, 2, figsize=(13, 4.8))
    w = 0.38
    for k, l in enumerate(("row", "column")):
        xs = [i + (k - .5) * w for i in range(len(ops))]
        ax[0].bar(xs, [e3[(o, l)]["time_ms"][0] for o in ops], w, yerr=[e3[(o, l)]["time_ms"][1] for o in ops], label=l, color=COL[l], capsize=2)
        ax[1].bar(xs, [e3[(o, l)]["page_reads"][0] for o in ops], w, yerr=[e3[(o, l)]["page_reads"][1] for o in ops], label=l, color=COL[l], capsize=2)
    for a_, yl, ti in ((ax[0], "Wall-clock time (ms, log scale)", "Execution time"), (ax[1], "Physical page reads (log scale)", "Page I/O")):
        a_.set_xticks(range(len(ops))); a_.set_xticklabels([o.replace(" (", "\n(").replace(", ", ",\n") for o in ops], fontsize=7.5)
        a_.set_yscale("log"); a_.set_ylabel(yl); a_.set_title(ti); a_.legend(title="layout"); a_.grid(axis="y", alpha=.3)
    fig.suptitle(f'E3: row vs column layout ({cfg["rows"]:,} rows, cold cache, mean ± std of {n_runs} runs)')
    fig.tight_layout(); fig.savefig(os.path.join(out, "e3_row_vs_column.png"), dpi=140); plt.close(fig)

    fig, ax = plt.subplots(1, 2, figsize=(9, 4))
    for a_, key, yl, ti, sc in ((ax[0], "throughput", "Rows inserted per second", "Row-at-a-time insert throughput", 1),
                                (ax[1], "file_bytes", "Database file size (MiB)", "Space for inserted rows", 2 ** 20)):
        a_.bar(["row", "column"], [ins[(l,)][key][0] / sc for l in ("row", "column")],
               yerr=[ins[(l,)][key][1] / sc for l in ("row", "column")], color=[COL["row"], COL["column"]], capsize=3)
        a_.set_ylabel(yl); a_.set_title(ti); a_.grid(axis="y", alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(out, "e3_insert_and_space.png"), dpi=140); plt.close(fig)

    # ------------------------------------------------------------------ correctness
    md += ["## Correctness assertions recorded alongside the timings", "",
           f'* E1/E2 point lookups returning a wrong/missing row: **{asr["E1_E2_lookup_errors"]}** (expected 0)',
           f'* E3 scans / aggregates / lookups match the in-memory reference: **{asr["E3_results_correct"]}**',
           "* Restart persistence (close -> reopen -> full scan identical): " +
           ", ".join(f'{l}: **{v["identical_after_restart"]}** (reopen {v["reopen_ms"]:.1f} ms)' for l, v in asr["restart"].items()), ""]
    with open(os.path.join(out, "summary.md"), "w") as f:
        f.write("\n".join(md))
    print(f"[report] wrote summary.md and charts to {out}")


if __name__ == "__main__":
    make_report(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
