"""
check_tests.py -- run every hand-derived test query through ALL solvers and compare.

For each query in data/tests/*.json the following must agree on the optimal cost:
  expected   derived by hand (build_test_cases.py has the arithmetic)
  brute      exhaustive enumeration of whole journeys from the raw tables (oracle.py)
  A*         astar.py, h = straight-line distance / v_max
  Dijkstra   astar.py, h = 0
It also checks the number of transfers, and prints expanded states + median runtime.

    python check_tests.py                 # the 16 hand-built queries
    python check_tests.py --city 300      # + 300 random city pairs: A* cost must equal Dijkstra cost
"""
import argparse
import glob
import json
import os
import random
import statistics
import sys

from astar import astar, dijkstra, search, time_search
from journey import breakdown
from network import Network, TransitGraph
from oracle import brute_force

TOL = 1e-6


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOL


def cost_of(res):
    return res.cost if res.found else None


def fmt(x):
    return "no route" if x is None else f"{x:.2f}"


def run_tests(test_dir, repeats):
    total = bad = 0
    sum_a = sum_d = 0
    for fp in sorted(glob.glob(os.path.join(test_dir, "*.json"))):
        with open(fp, encoding="utf-8") as f:
            case = json.load(f)
        net = Network.from_dict({**case["network"], "config": case.get("config", {})})
        graph = TransitGraph(net)
        print(f"\n{case['name']}\n  {case['description']}")
        print(f"  {'query':<26}{'p':>4}{'expected':>10}{'brute':>9}{'A*':>9}{'Dijkstra':>10}"
              f"{'xfers':>6}{'exp A*':>8}{'exp Dij':>8}{'us A*':>8}{'us Dij':>8}   result")
        for qd in case["queries"]:
            q = net.query(qd["origin"], qd["dest"], qd["transfer_penalty"])
            exp = qd["expected_cost"]
            brute = brute_force(net, q, max_rides=4, max_walks=2)
            ra, ta = time_search(astar, graph, q, repeats)
            rd, td = time_search(dijkstra, graph, q, repeats)

            ok = same(exp, brute) and same(exp, cost_of(ra)) and same(exp, cost_of(rd))
            note = ""
            if ok and ra.found:                      # cost parts must add up, penalty = p * transfers
                bd = breakdown(graph, q, ra)
                if abs(bd.penalty - q.transfer_penalty * ra.transfers) > TOL:
                    ok, note = False, " (penalty part != p * transfers)"
            if ok and ra.found:
                allowed = qd.get("expected_transfers_any")
                if allowed is None and qd.get("expected_transfers") is not None:
                    allowed = [qd["expected_transfers"]]
                if allowed is not None and not (ra.transfers in allowed and rd.transfers in allowed):
                    ok, note = False, f" (transfers A*={ra.transfers} Dij={rd.transfers}, allowed {allowed})"
            total += 1
            bad += 0 if ok else 1
            sum_a += ra.expanded
            sum_d += rd.expanded
            print(f"  {qd['label']:<26}{qd['transfer_penalty']:>4g}{fmt(exp):>10}{fmt(brute):>9}"
                  f"{fmt(cost_of(ra)):>9}{fmt(cost_of(rd)):>10}{(ra.transfers if ra.found else '-'):>6}"
                  f"{ra.expanded:>8}{rd.expanded:>8}{ta:>8.0f}{td:>8.0f}   {'PASS' if ok else 'FAIL' + note}")
    print(f"\n{total - bad}/{total} queries pass.  Total expanded states: A* {sum_a}, Dijkstra {sum_d}")
    return bad


def run_city(path, n, seed, repeats=15):
    """Bigger correctness check: A* and Dijkstra must return the same cost on random city pairs."""
    net = Network.from_dir(path)
    graph = TransitGraph(net)
    rng = random.Random(seed)
    ids = sorted(net.stops)
    mismatches, exp_a, exp_d, t_a, t_d = [], [], [], [], []
    for k in range(n):
        a, b = rng.sample(ids, 2)
        p = (0, 5, 10, 15)[k % 4]
        q = net.query(net.stops[a].pos, net.stops[b].pos, p)
        ra, ta = time_search(astar, graph, q, repeats)
        rd, td = time_search(dijkstra, graph, q, repeats)
        if not same(cost_of(ra), cost_of(rd)):
            mismatches.append(f"{a}->{b} p={p}: A* {fmt(cost_of(ra))} vs Dijkstra {fmt(cost_of(rd))}")
        exp_a.append(ra.expanded)
        exp_d.append(rd.expanded)
        t_a.append(ta)
        t_d.append(td)
    print(f"\nCity check on {path}: {n} random stop pairs, penalties 0/5/10/15")
    print(f"  cost mismatches between A* and Dijkstra: {len(mismatches)}")
    for m in mismatches[:5]:
        print(f"    {m}")
    print(f"  expanded states, median: A* {statistics.median(exp_a):.0f}, Dijkstra {statistics.median(exp_d):.0f}"
          f"  (A*/Dijkstra = {statistics.median(exp_a) / statistics.median(exp_d):.2f})")
    print(f"  runtime us, median:      A* {statistics.median(t_a):.0f}, Dijkstra {statistics.median(t_d):.0f}")
    return len(mismatches)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tests", default="data/tests")
    ap.add_argument("--repeats", type=int, default=200, help="timing repeats per query")
    ap.add_argument("--city", type=int, default=0, help="also compare on N random pairs of the city")
    ap.add_argument("--dataset", default="data/synthetic_city")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    bad = run_tests(a.tests, a.repeats)
    if a.city:
        bad += run_city(a.dataset, a.city, a.seed)
    sys.exit(1 if bad else 0)
