"""
validate.py -- check that a dataset (or the hand-built test networks) is internally
consistent and that the A* heuristic is genuinely valid.

    python validate.py                      # city dataset + all test networks
    python validate.py --dataset data/synthetic_city
    python validate.py --tests data/tests
    python validate.py --negative-control   # prove the heuristic check CAN fail

Checks
  1. Schema        ids unique, references valid, sequences contiguous, in/out routes paired
  2. Physics       every ride/walk edge slower than the declared v_max; per-mode speed bounds
  3. Connectivity  every stop can reach every other stop (rides + walking)
  4. Heuristic     consistency on EVERY edge; admissibility against the exact remaining cost
  5. Graph check   Dijkstra over the graph agrees with an exhaustive journey enumeration
  6. Checksums     files still match what the generator wrote (warning only)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict, deque

from network import Network, TransitGraph, dist_km, read_raw, DEST, ORIGIN
from oracle import brute_force, graph_dijkstra, reverse_distances

TOL = 1e-9


class Report:
    def __init__(self):
        self.fails = 0
        self.warns = 0

    def line(self, status, name, detail=""):
        if status == "FAIL":
            self.fails += 1
        if status == "WARN":
            self.warns += 1
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail else ""))

    def check(self, name, errors, ok_detail="", warn=False):
        if errors:
            self.line("WARN" if warn else "FAIL", name, f"{len(errors)} problem(s)")
            for e in errors[:8]:
                print(f"         - {e}")
            if len(errors) > 8:
                print(f"         ... and {len(errors) - 8} more")
        else:
            self.line("PASS", name, ok_detail)


# ----------------------------------------------------------------------------
# 1. schema
# ----------------------------------------------------------------------------
def schema_errors(raw, dataset=True):
    errs, warns = [], []
    stops = raw["stops"]
    ids = [s["id"] for s in stops]
    if len(set(ids)) != len(ids):
        errs.append("duplicate stop ids")
    for s in stops:
        if not s["id"] or not math.isfinite(s["x"]) or not math.isfinite(s["y"]):
            errs.append(f"bad stop row {s}")
    idset = set(ids)

    if dataset:
        size = raw.get("metadata", {}).get("parameters", {}).get("city_size_km")
        if size:
            for s in stops:
                if not (0 <= s["x"] <= size and 0 <= s["y"] <= size):
                    errs.append(f"stop {s['id']} outside the {size} km city")
        pts = [(s["x"], s["y"], s["id"]) for s in stops]
        closest = min((dist_km(a[:2], b[:2]), a[2], b[2]) for i, a in enumerate(pts) for b in pts[i + 1:])
        if closest[0] < 0.05:
            errs.append(f"stops {closest[1]} and {closest[2]} are only {closest[0] * 1000:.0f} m apart")

    rids = [r["id"] for r in raw["routes"]]
    if len(set(rids)) != len(rids):
        errs.append("duplicate route ids")
    served = set()
    by_id = {r["id"]: r for r in raw["routes"]}
    for r in raw["routes"]:
        n = len(r["stops"])
        rid = r["id"]
        if r["mode"] not in ("bus", "metro"):
            errs.append(f"{rid}: unknown mode {r['mode']!r}")
        if not r["headway"] > 0:
            errs.append(f"{rid}: headway must be > 0")
        if n < (3 if r["loop"] else 2):
            errs.append(f"{rid}: too few stops ({n})")
        if len(r["ride"]) != n:
            errs.append(f"{rid}: {len(r['ride'])} ride times for {n} stops")
            continue
        if "seqs" in r and r["seqs"] != list(range(n)):
            errs.append(f"{rid}: seq numbers are not 0..{n - 1}")
        for sid in r["stops"]:
            if sid not in idset:
                errs.append(f"{rid}: unknown stop {sid}")
        served.update(r["stops"])
        for i in range(n):
            if r["stops"][i] == r["stops"][(i + 1) % n] and (r["loop"] or i + 1 < n):
                errs.append(f"{rid}: same stop twice in a row at position {i}")
        if not r["loop"] and len(set(r["stops"])) != n:
            errs.append(f"{rid}: visits a stop twice")
        if r["loop"]:
            if any(not t > 0 for t in r["ride"]):
                errs.append(f"{rid}: loop ride times must all be > 0")
        else:
            if r["ride"][0] != 0 or any(not t > 0 for t in r["ride"][1:]):
                errs.append(f"{rid}: ride[0] must be 0 and the rest > 0")

    if dataset:
        for rid, r in by_id.items():
            if not rid.endswith("_out"):
                continue
            other = by_id.get(rid[:-4] + "_in")
            if other is None:
                errs.append(f"{rid}: missing opposite direction")
                continue
            n = len(r["stops"])
            expect = [r["stops"][(-k) % n] for k in range(n)] if r["loop"] else list(reversed(r["stops"]))
            if other["stops"] != expect:
                errs.append(f"{rid}: opposite direction does not visit the same stops in reverse")
            if other["headway"] != r["headway"] or other["mode"] != r["mode"]:
                errs.append(f"{rid}: opposite direction differs in headway/mode")
            if abs(sum(r["ride"]) - sum(other["ride"])) > 0.011:
                errs.append(f"{rid}: total ride time differs between directions")
        for sid in ids:
            if sid not in served:
                errs.append(f"stop {sid} is served by no route")
    else:
        for sid in ids:
            if sid not in served:
                warns.append(f"stop {sid} is served by no route")

    seen = set()
    for w in raw["walk_links"]:
        key = frozenset((w["a"], w["b"]))
        if w["a"] not in idset or w["b"] not in idset:
            errs.append(f"walk link {w['a']}-{w['b']}: unknown stop")
        elif w["a"] == w["b"]:
            errs.append(f"walk link {w['a']}: self link")
        if key in seen:
            errs.append(f"walk link {w['a']}-{w['b']}: duplicate")
        seen.add(key)
        if not w["walk_min"] > 0:
            errs.append(f"walk link {w['a']}-{w['b']}: walk_min must be > 0")
    return errs


# ----------------------------------------------------------------------------
# 2. physics
# ----------------------------------------------------------------------------
def physics(raw, dataset=True):
    cfg = {**__import__("network").DEFAULT_CONFIG, **raw.get("config", {})}
    vmax = cfg["v_max_kmh"]
    pos = {s["id"]: (s["x"], s["y"]) for s in raw["stops"]}
    bounds = raw.get("metadata", {}).get("parameters", {}).get("speed_bounds_kmh", {}) if dataset else {}
    errs = []
    speeds = defaultdict(list)
    for r in raw["routes"]:
        n = len(r["stops"])
        for i in range(n):
            if i == 0 and not r["loop"]:
                continue
            a, b = r["stops"][(i - 1) % n], r["stops"][i]
            d = dist_km(pos[a], pos[b])
            if r["ride"][i] <= 0:
                continue
            v = d / (r["ride"][i] / 60.0)
            speeds[r["mode"]].append(v)
            if v > vmax * (1 + TOL):
                errs.append(f"{r['id']} {a}->{b}: {v:.1f} km/h exceeds v_max {vmax:g}")
            if r["mode"] in bounds and not bounds[r["mode"]][0] <= v <= bounds[r["mode"]][1]:
                errs.append(f"{r['id']} {a}->{b}: {v:.1f} km/h outside {r['mode']} bounds {bounds[r['mode']]}")
            if dataset and d < 0.2:
                errs.append(f"{r['id']} {a}->{b}: segment only {d * 1000:.0f} m long")
    walk_speeds = []
    for w in raw["walk_links"]:
        d = dist_km(pos[w["a"]], pos[w["b"]])
        v = d / (w["walk_min"] / 60.0)
        walk_speeds.append(v)
        if v > cfg["walk_speed_kmh"] * 1.01:
            errs.append(f"walk {w['a']}-{w['b']}: implied {v:.2f} km/h faster than walking speed")
        if v > vmax:
            errs.append(f"walk {w['a']}-{w['b']}: exceeds v_max")
        if dataset:
            if v < cfg["walk_speed_kmh"] / 1.5:
                errs.append(f"walk {w['a']}-{w['b']}: detour factor above 1.5")
            if abs(d - w["distance_km"]) > 0.002:
                errs.append(f"walk {w['a']}-{w['b']}: distance_km {w['distance_km']} != coordinates {d:.3f}")
            if d > 0.8:
                errs.append(f"walk {w['a']}-{w['b']}: link longer than 800 m")
    if cfg["walk_speed_kmh"] >= vmax:
        errs.append("walking speed must be below v_max")
    info = {m: (min(v), max(v)) for m, v in speeds.items()}
    if walk_speeds:
        info["walk"] = (min(walk_speeds), max(walk_speeds))
    return errs, info


# ----------------------------------------------------------------------------
# 3. connectivity
# ----------------------------------------------------------------------------
def connectivity_errors(raw):
    ids = [s["id"] for s in raw["stops"]]
    fwd, bwd = defaultdict(set), defaultdict(set)
    for r in raw["routes"]:
        n = len(r["stops"])
        for i in range(n if r["loop"] else n - 1):
            a, b = r["stops"][i], r["stops"][(i + 1) % n]
            fwd[a].add(b)
            bwd[b].add(a)
    for w in raw["walk_links"]:
        for a, b in ((w["a"], w["b"]), (w["b"], w["a"])):
            fwd[a].add(b)
            bwd[b].add(a)

    def reach(adj, start):
        seen, dq = {start}, deque([start])
        while dq:
            u = dq.popleft()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    dq.append(v)
        return seen

    errs = []
    f, b = reach(fwd, ids[0]), reach(bwd, ids[0])
    for sid in ids:
        if sid not in f:
            errs.append(f"{sid} cannot be reached from {ids[0]}")
        if sid not in b:
            errs.append(f"{sid} cannot reach {ids[0]}")
    return errs


# ----------------------------------------------------------------------------
# 4. heuristic checks (consistency on all edges + admissibility vs exact h*)
# ----------------------------------------------------------------------------
def sample_queries(net, n, seed):
    rng = random.Random(seed)
    xs = [s.x for s in net.stops.values()]
    ys = [s.y for s in net.stops.values()]
    lo_x, hi_x, lo_y, hi_y = min(xs) - 0.5, max(xs) + 0.5, min(ys) - 0.5, max(ys) + 0.5
    stops = list(net.stops.values())

    def point():
        if rng.random() < 0.5:
            return (rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y))
        return rng.choice(stops).pos

    return [net.query(point(), point(), rng.choice([0, 5, 15])) for _ in range(n)]


def heuristic_checks(net, n_queries=25, seed=0, vmax_kmh=None):
    graph = TransitGraph(net)
    if vmax_kmh is not None:
        graph.v_max = vmax_kmh / 60.0
    cons_viol, adm_viol, edges_checked, nodes_checked = [], [], 0, 0
    ratios = []
    for q in sample_queries(net, n_queries, seed):
        nodes = list(graph.all_nodes())
        h = {u: graph.heuristic(u, q) for u in nodes}
        for u in nodes:
            for v, w, kind in graph.neighbors(u, q):
                edges_checked += 1
                if h[u] > w + h[v] + TOL:
                    cons_viol.append(f"edge {u}->{v} ({kind}): h(u)={h[u]:.3f} > w+h(v)={w + h[v]:.3f}")
        hstar = reverse_distances(graph, q)
        for u in nodes:
            nodes_checked += 1
            if u in hstar:
                if h[u] > hstar[u] + TOL:
                    adm_viol.append(f"node {u}: h={h[u]:.3f} > true remaining cost {hstar[u]:.3f}")
                elif u[0] in ("R", "A") and hstar[u] > 0:
                    ratios.append(h[u] / hstar[u])
    return {"consistency": cons_viol, "admissibility": adm_viol, "edges": edges_checked,
            "nodes": nodes_checked, "ratios": ratios, "queries": n_queries}


def report_heuristic(rep, res, label=""):
    rep.check(f"heuristic consistent on every edge{label}", res["consistency"],
              f"{res['edges']:,} edges over {res['queries']} random queries")
    rep.check(f"heuristic admissible (h <= true remaining cost){label}", res["admissibility"],
              f"{res['nodes']:,} node checks against exact reverse Dijkstra")
    if res["ratios"]:
        r = res["ratios"]
        rep.line("INFO", f"heuristic tightness h/h*{label}",
                 f"mean {statistics.mean(r):.2f}, median {statistics.median(r):.2f}, min {min(r):.2f} "
                 f"(1.0 = perfect, lower = looser = A* helps less)")


# ----------------------------------------------------------------------------
# 5. graph vs exhaustive journey enumeration
# ----------------------------------------------------------------------------
def graph_cross_check(net, pairs=40, seed=1, max_rides=3, max_walks=2):
    rng = random.Random(seed)
    graph = TransitGraph(net)
    ids = sorted(net.stops)
    errs, matched, skipped = [], 0, 0
    for k in range(pairs):
        a, b = rng.sample(ids, 2)
        p = (0, 5, 10)[k % 3]
        q = net.query(net.stops[a].pos, net.stops[b].pos, p)
        res = graph_dijkstra(graph, q)
        if res is None:
            errs.append(f"{a}->{b} (p={p}): graph says unreachable")
            continue
        bf = brute_force(net, q, max_rides=max_rides, max_walks=max_walks, bound=res["cost"] + 1e-6)
        within = res["rides"] <= max_rides and res["max_walk_chain"] <= max_walks
        if bf is None:
            if within:
                errs.append(f"{a}->{b} (p={p}): graph cost {res['cost']:.3f} but no real journey achieves it")
            else:
                skipped += 1
        elif bf < res["cost"] - 1e-6:
            errs.append(f"{a}->{b} (p={p}): a journey costing {bf:.3f} exists but Dijkstra found {res['cost']:.3f}")
        else:
            matched += 1
    return errs, matched, skipped


# ----------------------------------------------------------------------------
# 6. checksums
# ----------------------------------------------------------------------------
def checksum_errors(path, meta):
    errs = []
    for f, want in meta.get("checksums_sha256", {}).items():
        fp = os.path.join(path, f)
        if not os.path.exists(fp):
            errs.append(f"{f} is missing")
            continue
        with open(fp, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != want:
                errs.append(f"{f} differs from what the generator wrote")
    return errs


# ----------------------------------------------------------------------------
# drivers
# ----------------------------------------------------------------------------
def validate_dataset(rep, path, n_queries, pairs):
    print(f"\n== Dataset: {path}")
    raw = read_raw(path)
    meta = raw["metadata"]
    c = meta.get("counts", {})
    rep.line("INFO", "synthetic label", f"SYNTHETIC={meta.get('SYNTHETIC')} seed={meta.get('seed')} "
             f"({len(raw['stops'])} stops, {len(raw['routes'])} directed routes, {len(raw['walk_links'])} walk links)")
    if meta.get("SYNTHETIC") is not True:
        rep.line("FAIL", "metadata marks the data as synthetic")
    rep.check("schema and references", schema_errors(raw, True))
    errs, info = physics(raw, True)
    rep.check("physical plausibility (speeds <= v_max, per-mode bounds)", errs,
              "; ".join(f"{m} {lo:.1f}-{hi:.1f} km/h" for m, (lo, hi) in sorted(info.items())))
    rep.check("every stop reaches every other stop", connectivity_errors(raw))
    net = Network.from_dict(raw)
    report_heuristic(rep, heuristic_checks(net, n_queries))
    errs, matched, skipped = graph_cross_check(net, pairs)
    rep.check("graph+Dijkstra agrees with exhaustive journey enumeration", errs,
              f"{matched} pairs matched exactly, {skipped} skipped (optimum uses more legs than the enumeration limit)")
    rep.check("files match generator checksums", checksum_errors(path, meta), warn=True)
    return net


def validate_tests(rep, test_dir, n_queries):
    print(f"\n== Test networks: {test_dir}")
    for fp in sorted(glob.glob(os.path.join(test_dir, "*.json"))):
        with open(fp, encoding="utf-8") as f:
            case = json.load(f)
        raw = {**case["network"], "config": case.get("config", {})}
        errs = schema_errors(raw, False)
        e2, _ = physics(raw, False)
        net = Network.from_dict(raw)
        res = heuristic_checks(net, max(5, n_queries // 3))
        problems = errs + e2 + res["consistency"] + res["admissibility"]
        rep.check(case["name"], problems, "schema, physics, heuristic consistent + admissible")


def negative_control(rep, net):
    print("\n== Negative control (the check must be able to FAIL)")
    res = heuristic_checks(net, 10, vmax_kmh=20.0)
    bad = len(res["admissibility"]) + len(res["consistency"])
    if bad > 0:
        rep.line("PASS", "validator catches an inadmissible heuristic",
                 f"with v_max forced to 20 km/h (below the metro's real speed) it flagged {bad} violations")
    else:
        rep.line("FAIL", "validator did NOT catch v_max=20 km/h -- the heuristic check is not trustworthy")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", action="append", help="dataset folder (repeatable); default data/synthetic_city")
    ap.add_argument("--tests", default="data/tests")
    ap.add_argument("--no-tests", action="store_true")
    ap.add_argument("--queries", type=int, default=25, help="random queries for heuristic checks")
    ap.add_argument("--pairs", type=int, default=40, help="stop pairs for the graph cross-check")
    ap.add_argument("--negative-control", action="store_true")
    a = ap.parse_args()

    rep = Report()
    nets = []
    for d in a.dataset or ["data/synthetic_city"]:
        nets.append(validate_dataset(rep, d, a.queries, a.pairs))
    if not a.no_tests and os.path.isdir(a.tests):
        validate_tests(rep, a.tests, a.queries)
    if a.negative_control:
        negative_control(rep, nets[0])
    print(f"\nSUMMARY: {rep.fails} failure(s), {rep.warns} warning(s)")
    sys.exit(1 if rep.fails else 0)
