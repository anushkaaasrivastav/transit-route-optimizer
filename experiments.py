"""
experiments.py -- run every experiment and save the results as CSV files in results/.

    python experiments.py            # full run (a few minutes)
    python experiments.py --quick    # small smoke test, written to results_quick/

Experiments (all on the SAME fixed random query sets, so they are comparable):
  1. algorithms   A* vs Dijkstra on the 80 / 300 / 1000-stop cities: optimal cost (must be equal),
                  expanded states, generated states, median runtime, by trip distance (short/medium/long)
  2. penalty      transfer penalty sweep: how cost, transfers and the chosen route change with p,
                  plus every trip in the 80-stop city whose route flips to fewer transfers as p grows
  3. baseline     "shortest distance" route evaluated under the real cost model, vs the optimal route

Built-in safety checks (the run ABORTS if any fails):
  - A* cost == Dijkstra cost on every query
  - ride + wait + walk + penalty == search cost, on every journey
  - the distance-search cost equals the journey's measured distance (baseline is really shortest)
  - the baseline is never faster than the optimum, and the optimum is never longer in km than it
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from datetime import datetime, timezone

from astar import astar, dijkstra, search, time_search
from journey import breakdown
from network import DEST, ORIGIN, Network, TransitGraph, dist_km
from validate import heuristic_checks

NETWORKS = [("city_80", "data/synthetic_city"), ("city_300", "data/city_300"), ("city_1000", "data/city_1000")]
N_PAIRS = {"city_80": 300, "city_300": 300, "city_1000": 200}
REPEATS = {"city_80": 15, "city_300": 7, "city_1000": 5}
PENALTIES = [0, 2, 5, 8, 10, 15, 20, 30]
DEFAULT_P = 5
PAIR_SEED = 2026
TOL = 1e-6
BUCKETS = ["short", "medium", "long"]


# ----------------------------------------------------------------------------
# the shortest-distance baseline: same graph, but every edge costs its LENGTH in km
# ----------------------------------------------------------------------------
class DistanceGraph(TransitGraph):
    """Same nodes and edges as TransitGraph, but cost = kilometres travelled (ride + walk).
    No waiting, no speeds, no transfer penalty: this is what a 'shortest path' map would do."""

    def _km(self, u, v, kind, q):
        net = self.net
        if kind in ("ride", "alight", "walk", "board"):
            return dist_km(self.position(u, q), self.position(v, q))
        if kind == "access":
            route = net.routes[v[1]]
            stop = net.stops[route.stops[(v[2] - 1) % len(route.stops)]]
            return dist_km(q.origin, stop.pos) + dist_km(stop.pos, self.position(v, q))
        if kind == "egress":
            return dist_km(self.position(u, q), q.dest)
        return dist_km(q.origin, q.dest)                 # "direct"

    def neighbors(self, node, q):
        for v, _, kind in super().neighbors(node, q):
            yield v, self._km(node, v, kind, q), kind

    def heuristic(self, node, q):                        # straight-line km: admissible for km
        return 0.0 if node == DEST else dist_km(self.position(node, q), q.dest)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.4f}" if isinstance(r[k], float) else r[k]) for k in fields})


def pctl(values, p):
    values = sorted(values)
    if not values:
        return float("nan")
    k = (len(values) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def sample_pairs(net, n, rng):
    """n distinct ordered stop pairs, tagged short/medium/long by straight-line distance terciles."""
    ids = sorted(net.stops)
    seen, pairs = set(), []
    while len(pairs) < n:
        a, b = rng.sample(ids, 2)
        if (a, b) not in seen:
            seen.add((a, b))
            pairs.append((a, b, dist_km(net.stops[a].pos, net.stops[b].pos)))
    ds = sorted(p[2] for p in pairs)
    t1, t2 = ds[len(ds) // 3], ds[2 * len(ds) // 3]
    tagged = [(a, b, d, "short" if d <= t1 else "medium" if d <= t2 else "long") for a, b, d in pairs]
    return tagged, (t1, t2)


def make_query(net, a, b, p):
    return net.query(net.stops[a].pos, net.stops[b].pos, p)


def static_graph_size(net, graph):
    far = net.query((1e6, 1e6), (2e6, 2e6), DEFAULT_P)    # no access/egress edges at that distance
    nodes = list(graph.static_nodes())
    edges = sum(1 for u in nodes for _ in graph.neighbors(u, far))
    return len(nodes), edges


def signature(res):
    return hash(tuple(res.path))


# ----------------------------------------------------------------------------
# experiment 1: A* vs Dijkstra
# ----------------------------------------------------------------------------
def run_algorithms(name, net, graph, pairs, repeats, log):
    rows = []
    mismatches = 0
    for k, (a, b, d, bucket) in enumerate(pairs):
        q = make_query(net, a, b, DEFAULT_P)
        gc.collect()
        ra, ta = time_search(astar, graph, q, repeats)
        rd, td = time_search(dijkstra, graph, q, repeats)
        if not ra.found or not rd.found:
            raise RuntimeError(f"{name}: {a}->{b} unreachable but dataset is connected")
        if abs(ra.cost - rd.cost) > TOL:
            mismatches += 1
            raise AssertionError(f"{name}: A* {ra.cost} != Dijkstra {rd.cost} for {a}->{b}")
        for algo, res, t in (("astar", ra, ta), ("dijkstra", rd, td)):
            jn = breakdown(graph, q, res)                       # asserts parts sum to cost
            rows.append({"network": name, "pair_id": k, "origin": a, "dest": b, "straight_km": d,
                         "bucket": bucket, "penalty": DEFAULT_P, "algo": algo, "cost": res.cost,
                         "transfers": res.transfers, "vehicle_stops": jn.vehicle_stops,
                         "ride": jn.ride, "wait": jn.wait, "walk": jn.walk, "penalty_min": jn.penalty,
                         "expanded": res.expanded, "generated": res.generated, "time_us": t})
        if (k + 1) % 100 == 0:
            log(f"    {name}: {k + 1}/{len(pairs)} pairs")
    return rows, mismatches


def summarize_algorithms(rows, net_info):
    by_algo, comp = [], []
    for name in net_info:
        for bucket in ["all"] + BUCKETS:
            sel = [r for r in rows if r["network"] == name and (bucket == "all" or r["bucket"] == bucket)]
            if not sel:
                continue
            per = {}
            for algo in ("astar", "dijkstra"):
                rs = [r for r in sel if r["algo"] == algo]
                per[algo] = rs
                by_algo.append({
                    "network": name, "stops": net_info[name]["stops"], "bucket": bucket, "algo": algo, "n": len(rs),
                    "mean_cost": statistics.mean(r["cost"] for r in rs),
                    "mean_transfers": statistics.mean(r["transfers"] for r in rs),
                    "mean_expanded": statistics.mean(r["expanded"] for r in rs),
                    "median_expanded": statistics.median(r["expanded"] for r in rs),
                    "p90_expanded": pctl([r["expanded"] for r in rs], 90),
                    "mean_generated": statistics.mean(r["generated"] for r in rs),
                    "median_time_us": statistics.median(r["time_us"] for r in rs),
                    "p90_time_us": pctl([r["time_us"] for r in rs], 90)})
            ea = {r["pair_id"]: r for r in per["astar"]}
            ed = {r["pair_id"]: r for r in per["dijkstra"]}
            ratios = [ea[i]["expanded"] / ed[i]["expanded"] for i in ea]
            tr = [ea[i]["time_us"] / ed[i]["time_us"] for i in ea]
            comp.append({
                "network": name, "stops": net_info[name]["stops"], "bucket": bucket, "n": len(ea),
                "median_expanded_astar": statistics.median(r["expanded"] for r in per["astar"]),
                "median_expanded_dijkstra": statistics.median(r["expanded"] for r in per["dijkstra"]),
                "median_expanded_ratio": statistics.median(ratios),
                "mean_expanded_ratio": statistics.mean(ratios),
                "median_time_astar_us": statistics.median(r["time_us"] for r in per["astar"]),
                "median_time_dijkstra_us": statistics.median(r["time_us"] for r in per["dijkstra"]),
                "median_time_ratio": statistics.median(tr),
                "pairs_astar_faster": sum(1 for x in tr if x < 1.0),
                "cost_mismatches": sum(1 for i in ea if abs(ea[i]["cost"] - ed[i]["cost"]) > TOL)})
    return by_algo, comp


# ----------------------------------------------------------------------------
# experiment 2: transfer penalty sweep (+ exhaustive flip search on the 80-stop city)
# ----------------------------------------------------------------------------
def run_penalty(name, net, graph, pairs, log):
    pair_rows, base_sig = [], {}
    for k, (a, b, d, bucket) in enumerate(pairs):
        for p in PENALTIES:
            q = make_query(net, a, b, p)
            t0 = time.perf_counter()
            res = astar(graph, q)
            t = (time.perf_counter() - t0) * 1e6
            jn = breakdown(graph, q, res)
            sig = signature(res)
            if p == PENALTIES[0]:
                base_sig[k] = sig
            pair_rows.append({"network": name, "pair_id": k, "origin": a, "dest": b, "bucket": bucket,
                              "penalty": p, "cost": res.cost, "transfers": res.transfers,
                              "ride": jn.ride, "wait": jn.wait, "walk": jn.walk, "penalty_min": jn.penalty,
                              "distance_km": jn.distance_km, "expanded": res.expanded, "time_us": t,
                              "route_changed_vs_p0": int(sig != base_sig[k])})
    summary = []
    for p in PENALTIES:
        rs = [r for r in pair_rows if r["penalty"] == p]
        n = len(rs)
        summary.append({
            "network": name, "penalty": p, "n": n,
            "mean_cost": statistics.mean(r["cost"] for r in rs),
            "mean_transfers": statistics.mean(r["transfers"] for r in rs),
            "share_0_transfers": sum(r["transfers"] == 0 for r in rs) / n,
            "share_1_transfer": sum(r["transfers"] == 1 for r in rs) / n,
            "share_2_transfers": sum(r["transfers"] == 2 for r in rs) / n,
            "share_3plus_transfers": sum(r["transfers"] >= 3 for r in rs) / n,
            "mean_ride": statistics.mean(r["ride"] for r in rs),
            "mean_wait": statistics.mean(r["wait"] for r in rs),
            "mean_walk": statistics.mean(r["walk"] for r in rs),
            "mean_penalty_min": statistics.mean(r["penalty_min"] for r in rs),
            "mean_distance_km": statistics.mean(r["distance_km"] for r in rs),
            "share_route_changed_vs_p0": sum(r["route_changed_vs_p0"] for r in rs) / n,
            "mean_expanded": statistics.mean(r["expanded"] for r in rs),
            "median_time_us": statistics.median(r["time_us"] for r in rs)})
    log(f"    {name}: penalty sweep done ({len(pairs)} pairs x {len(PENALTIES)} penalties)")
    return pair_rows, summary


def first_flip(net, graph, a, b, eps=1e-3):
    """Exact penalty at which the best route for a->b first changes (searching p in [0, 30]).

    A route's cost is a straight line in the penalty:   cost(p) = A + p * t
        A = minutes that do not depend on p (ride + wait + walk),  t = number of transfers.
    The best route for each p is the LOWEST of all these lines (the "lower envelope"), so the best
    route changes at the point where the p=0 line meets the next line on the envelope.

    How we find that next line with nothing but searches:
      1. cur = best route at p = 0;  hi = best route at p = 30 (fewer transfers, or there is no flip).
      2. The lines cur and hi cross at  p* = (A_hi - A_cur) / (t_cur - t_hi).
      3. Search at p* + eps. If the best route there still lies ON hi's line, hi is next on the
         envelope and p* is exact. Otherwise a third route m is below both: let hi = m, go to 2.
    The result is then VERIFIED with A* on both sides of p* (cost equals cur's line just before and
    hi's line just after), so a wrong answer cannot be saved.
    Returns None when there is no flip, else a dict.
    """
    def at(p):
        r = astar(graph, make_query(net, a, b, p))
        return r, r.cost - p * r.transfers                       # (result, A)

    r0, A0 = at(0.0)
    rhi, Ahi = at(float(PENALTIES[-1]))
    t0, thi = r0.transfers, rhi.transfers
    if thi >= t0:
        return None
    for _ in range(50):
        ps = (Ahi - A0) / (t0 - thi)
        rm, Am = at(ps + eps)
        if abs(rm.cost - (Ahi + (ps + eps) * thi)) < 1e-6:        # hi is optimal just after the crossing
            break
        Ahi, thi = Am, rm.transfers
    else:
        raise AssertionError(f"first_flip did not converge for {a}->{b}")
    before, _ = at(max(ps - eps, 0.0))
    after, _ = at(ps + eps)
    if ps > eps and abs(before.cost - (A0 + (ps - eps) * t0)) > 1e-6:
        raise AssertionError(f"{a}->{b}: expected the p=0 route to still be optimal at p={ps - eps:.4f}")
    if abs(after.cost - (Ahi + (ps + eps) * thi)) > 1e-6 or after.transfers != thi:
        raise AssertionError(f"{a}->{b}: expected the fewer-transfer route to be optimal at p={ps + eps:.4f}")
    return {"first_flip_penalty": ps, "transfers_before": t0, "transfers_after": thi,
            "extra_minutes_without_transfers": Ahi - A0, "cost_p0": r0.cost, "cost_p30": rhi.cost,
            "transfers_p30": rhi.transfers}


def find_flips(net, graph, pairs, log):
    """All trips whose best route changes to one with fewer transfers as the penalty rises (0..30)."""
    flips = []
    for a, b, d, _ in pairs:
        f = first_flip(net, graph, a, b)
        if f is not None:
            flips.append({"origin": a, "dest": b, "straight_km": d, **f})
    # most useful for demos first: flips near the default penalty, with a real time gap
    flips.sort(key=lambda r: (abs(r["first_flip_penalty"] - 8), r["origin"], r["dest"]))
    near = sum(1 for r in flips if 3 <= r["first_flip_penalty"] <= 15)
    log(f"    city_80: {len(flips)} of {len(pairs)} trips have a flip in p=0..30 "
        f"({near} between p=3 and p=15); every flip point was verified against A* on both sides")
    return flips


# ----------------------------------------------------------------------------
# experiment 3: shortest-distance baseline
# ----------------------------------------------------------------------------
def run_baseline(name, net, graph, pairs, log):
    dgraph = DistanceGraph(net)
    rows = []
    for k, (a, b, d, bucket) in enumerate(pairs):
        q = make_query(net, a, b, DEFAULT_P)
        opt = astar(graph, q)
        base = search(dgraph, q, h_weight=1.0)
        jo = breakdown(graph, q, opt)
        jb = breakdown(graph, q, base, verify=False)          # evaluate the km-route under the TIME model
        if abs(jb.distance_km - base.cost) > TOL:
            raise AssertionError(f"{name} {a}->{b}: distance search cost {base.cost} != measured {jb.distance_km}")
        if jb.total < opt.cost - TOL:
            raise AssertionError(f"{name} {a}->{b}: baseline ({jb.total}) beats the optimum ({opt.cost})")
        if jo.distance_km < base.cost - TOL:
            raise AssertionError(f"{name} {a}->{b}: optimum is shorter in km than the shortest-distance route")
        rows.append({"network": name, "pair_id": k, "origin": a, "dest": b, "bucket": bucket, "penalty": DEFAULT_P,
                     "opt_cost": opt.cost, "base_cost": jb.total,
                     "extra_min": jb.total - opt.cost, "extra_pct": 100 * (jb.total - opt.cost) / opt.cost,
                     "opt_transfers": opt.transfers, "base_transfers": jb.transfers,
                     "opt_km": jo.distance_km, "base_km": jb.distance_km, "extra_km": jo.distance_km - jb.distance_km,
                     "opt_wait": jo.wait, "base_wait": jb.wait, "opt_ride": jo.ride, "base_ride": jb.ride,
                     "opt_walk": jo.walk, "base_walk": jb.walk,
                     "same_route": int(signature(opt) == signature(base))})
    log(f"    {name}: baseline done")
    return rows


def summarize_baseline(rows, net_info):
    out = []
    for name in net_info:
        for bucket in ["all"] + BUCKETS:
            sel = [r for r in rows if r["network"] == name and (bucket == "all" or r["bucket"] == bucket)]
            if not sel:
                continue
            n = len(sel)
            out.append({"network": name, "bucket": bucket, "n": n,
                        "share_same_route": sum(r["same_route"] for r in sel) / n,
                        "mean_opt_cost": statistics.mean(r["opt_cost"] for r in sel),
                        "mean_base_cost": statistics.mean(r["base_cost"] for r in sel),
                        "mean_extra_min": statistics.mean(r["extra_min"] for r in sel),
                        "median_extra_pct": statistics.median(r["extra_pct"] for r in sel),
                        "p90_extra_pct": pctl([r["extra_pct"] for r in sel], 90),
                        "mean_opt_transfers": statistics.mean(r["opt_transfers"] for r in sel),
                        "mean_base_transfers": statistics.mean(r["base_transfers"] for r in sel),
                        "mean_opt_km": statistics.mean(r["opt_km"] for r in sel),
                        "mean_base_km": statistics.mean(r["base_km"] for r in sel),
                        "mean_extra_km": statistics.mean(r["extra_km"] for r in sel)})
    return out


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="small smoke test (writes to results_quick/)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or ("results_quick" if a.quick else "results")
    n_pairs = {k: (25 if a.quick else v) for k, v in N_PAIRS.items()}
    repeats = {k: (3 if a.quick else v) for k, v in REPEATS.items()}

    def log(msg):
        print(msg, flush=True)

    t_start = time.time()
    gc.disable()                                   # fewer random pauses while timing
    nets, net_info, all_rows, pen_pairs, pen_sum, base_rows, thresholds = {}, {}, [], [], [], [], {}
    pair_sets = {}
    for idx, (name, path) in enumerate(NETWORKS):
        net = Network.from_dir(path)
        graph = TransitGraph(net)
        nets[name] = (net, graph)
        n_nodes, n_edges = static_graph_size(net, graph)
        with open(os.path.join(path, "metadata.json"), encoding="utf-8") as f:
            meta = json.load(f)
        tight = heuristic_checks(net, 2 if a.quick else 12, seed=0)["ratios"]
        net_info[name] = {"stops": len(net.stops), "routes": len(net.routes), "walk_links": len(net.walk_links),
                          "graph_nodes": n_nodes, "graph_edges": n_edges, "seed": meta["seed"],
                          "dataset_path": path, "median_heuristic_tightness": statistics.median(tight),
                          "checksums_sha256": meta["checksums_sha256"]}
        pairs, th = sample_pairs(net, n_pairs[name], random.Random(PAIR_SEED + idx))
        pair_sets[name] = pairs
        thresholds[name] = {"short_max_km": th[0], "medium_max_km": th[1]}
        log(f"[{name}] {len(net.stops)} stops, {n_nodes} graph nodes, {n_edges} edges, {len(pairs)} pairs "
            f"(short<={th[0]:.1f} km, medium<={th[1]:.1f} km)")

    total_mismatch = 0
    for name, _ in NETWORKS:
        net, graph = nets[name]
        pairs = pair_sets[name]
        log(f"[{name}] experiment 1: A* vs Dijkstra")
        rows, mm = run_algorithms(name, net, graph, pairs, repeats[name], log)
        all_rows += rows
        total_mismatch += mm
        log(f"[{name}] experiment 2: penalty sweep")
        pr, ps = run_penalty(name, net, graph, pairs, log)
        pen_pairs += pr
        pen_sum += ps
        log(f"[{name}] experiment 3: shortest-distance baseline")
        base_rows += run_baseline(name, net, graph, pairs, log)

    log("[city_80] exhaustive flip search over all ordered stop pairs")
    net80, graph80 = nets["city_80"]
    ids = sorted(net80.stops)
    flip_pairs = [(x, y, dist_km(net80.stops[x].pos, net80.stops[y].pos), "")
                  for x in ids for y in ids if x != y] if not a.quick else pair_sets["city_80"]
    flips = find_flips(net80, graph80, flip_pairs, log)
    gc.enable()

    by_algo, comp = summarize_algorithms(all_rows, net_info)
    base_sum = summarize_baseline(base_rows, net_info)

    qf = ["network", "pair_id", "origin", "dest", "straight_km", "bucket", "penalty", "algo", "cost", "transfers",
          "vehicle_stops", "ride", "wait", "walk", "penalty_min", "expanded", "generated", "time_us"]
    write_csv(f"{out}/queries.csv", all_rows, qf)
    write_csv(f"{out}/summary_algorithms.csv", by_algo, list(by_algo[0]))
    write_csv(f"{out}/summary_comparison.csv", comp, list(comp[0]))
    write_csv(f"{out}/penalty_sweep_pairs.csv", pen_pairs, list(pen_pairs[0]))
    write_csv(f"{out}/penalty_sweep_summary.csv", pen_sum, list(pen_sum[0]))
    write_csv(f"{out}/penalty_flips.csv", flips, list(flips[0]) if flips else ["origin"])
    write_csv(f"{out}/baseline_queries.csv", base_rows, list(base_rows[0]))
    write_csv(f"{out}/baseline_summary.csv", base_sum, list(base_sum[0]))
    write_csv(f"{out}/networks.csv", [{"network": k, **{kk: vv for kk, vv in v.items() if kk != "checksums_sha256"}}
                                       for k, v in net_info.items()],
              ["network"] + [k for k in next(iter(net_info.values())) if k != "checksums_sha256"])

    meta = {
        "SYNTHETIC": True,
        "notice": "All results come from fictional synthetic cities (see data/*/DATASET_CARD.md).",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "quick" if a.quick else "full",
        "python": platform.python_version(), "platform": platform.platform(),
        "city_seed": 42, "pair_seed_base": PAIR_SEED,
        "pairs_per_network": n_pairs, "timing_repeats": repeats, "timing_statistic": "median of repeated runs, "
        "perf_counter, GC disabled; absolute microseconds depend on the machine, ratios are more portable",
        "default_penalty_min": DEFAULT_P, "penalties_swept": PENALTIES, "distance_buckets_km": thresholds,
        "expanded_definition": "states popped from the open list and expanded (goal excluded); same for both algorithms",
        "deterministic_columns": "cost, transfers, expanded, generated, breakdowns (identical for identical seeds)",
        "timing_columns": "time_us and anything derived from it (vary between runs and machines)",
        "total_astar_dijkstra_cost_mismatches": total_mismatch,
        "flip_trips_found_city_80": len(flips), "flip_trips_searched_city_80": len(flip_pairs),
        "networks": net_info, "runtime_seconds": round(time.time() - t_start, 1)}
    with open(f"{out}/experiment_meta.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    log(f"\nDone in {meta['runtime_seconds']} s. Results written to {out}/  (cost mismatches: {total_mismatch})")


if __name__ == "__main__":
    main()
