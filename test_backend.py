"""
test_backend.py -- the API must return exactly what the algorithms compute (no drift, no mock data).

    python test_backend.py          (or: pytest test_backend.py)

What is checked
  1. /api/route cost, transfers and breakdown equal direct calls to astar.py + journey.py
  2. A* and Dijkstra agree on cost through the API; the breakdown adds up to the cost
  3. the legs join up into one continuous path from the start to the destination
  4. the replay trace has exactly one entry per expanded state
  5. /api/sweep agrees with /api/route at every penalty, and with the exact flip points in the CSV
  6. /api/experiments equals the CSV files it is read from
  7. bad requests give clear errors, not crashes
"""
import csv
import json
import os
import random

from fastapi.testclient import TestClient

import server
import service
from astar import astar, dijkstra
from journey import breakdown
from network import dist_km

client = TestClient(server.app)
CITY = service.get_city()
STOPS = sorted(CITY.net.stops)
TOL = 1e-3          # the API rounds to 4 decimals


def get(path, **params):
    r = client.get(path, params=params)
    return r


def test_city_payload():
    c = get("/api/city").json()
    assert c["synthetic"] is True and "fictional" in c["notice"].lower() or "synthetic" in c["notice"].lower()
    assert len(c["stops"]) == len(CITY.net.stops)
    assert len(c["lines"]) == len(CITY.lines)
    assert {l["code"] for l in c["lines"]} == set(CITY.lines)
    assert len({l["color"] for l in c["lines"]}) == len(c["lines"]), "every line needs its own colour"
    for l in c["lines"]:
        assert len(l["coords"]) == len(l["stop_ids"]) + (1 if l["loop"] else 0)


def test_route_matches_algorithms():
    rng = random.Random(11)
    for k in range(60):
        a, b = rng.sample(STOPS, 2)
        p = rng.choice([0, 2, 5, 8, 15, 30])
        for algo, fn in (("astar", astar), ("dijkstra", dijkstra)):
            r = get("/api/route", origin=a, dest=b, algo=algo, penalty=p).json()
            q = CITY.net.query(CITY.net.stops[a].pos, CITY.net.stops[b].pos, p)
            direct = fn(CITY.graph, q)
            jn = breakdown(CITY.graph, q, direct)
            assert r["found"] == direct.found
            assert abs(r["cost"] - direct.cost) < TOL, (a, b, p, algo)
            assert r["transfers"] == direct.transfers
            assert r["stats"]["expanded"] == direct.expanded            # deterministic, identical to a direct call
            assert r["stats"]["generated"] == direct.generated
            bd = r["breakdown"]
            assert abs(bd["ride"] - jn.ride) < TOL and abs(bd["wait"] - jn.wait) < TOL
            assert abs(bd["walk"] - jn.walk) < TOL and abs(bd["penalty"] - jn.penalty) < TOL
            assert abs(sum(bd.values()) - r["cost"]) < 4 * TOL, "breakdown must add up to the cost"
            assert abs(bd["penalty"] - p * r["transfers"]) < TOL
        assert r["compare"]["same_cost"] is True


def test_legs_are_continuous():
    rng = random.Random(5)
    for _ in range(40):
        a, b = rng.sample(STOPS, 2)
        r = get("/api/route", origin=a, dest=b, penalty=rng.choice([0, 5, 15])).json()
        legs = r["legs"]
        assert legs, "route must have legs"
        o, d = CITY.net.stops[a].pos, CITY.net.stops[b].pos
        assert dist_km(legs[0]["coords"][0], o) < 1e-6 and dist_km(legs[-1]["coords"][-1], d) < 1e-6
        for x, y in zip(legs, legs[1:]):
            assert dist_km(x["coords"][-1], y["coords"][0]) < 1e-6, "legs must connect end to start"
        assert sum(1 for l in legs if l["kind"] == "ride") == r["transfers"] + 1 or r["transfers"] == 0 and \
            all(l["kind"] == "walk" for l in legs)


def test_replay_trace_matches_expanded():
    for algo in ("astar", "dijkstra"):
        r = get("/api/route", origin="S037", dest="S052", algo=algo, replay=1).json()
        assert len(r["trace"]) == r["stats"]["expanded"]
        assert r["trace"][0] == ["O", "O"]
    ra = get("/api/route", origin="S037", dest="S052", algo="astar", replay=1).json()
    rd = get("/api/route", origin="S037", dest="S052", algo="dijkstra", replay=1).json()
    assert ra["stats"]["expanded"] <= rd["stats"]["expanded"]


def test_free_points_and_unreachable():
    ok = get("/api/route", origin="1.0,1.0", dest="S010").json()
    assert ok["ok"] and ok["origin"]["type"] == "point"
    far = get("/api/route", origin="0.0,11.9", dest="11.9,0.0").json()       # corners far from any stop
    assert far["ok"] and (far["found"] is False or far["found"] is True)
    if not far["found"]:
        assert "No route" in far["message"]


def test_sweep_matches_route_and_csv_flips():
    s = get("/api/sweep", origin="S037", dest="S052").json()
    assert s["found"] and len(s["points"]) == 31
    for p in s["points"][::5]:
        r = get("/api/route", origin="S037", dest="S052", penalty=p["penalty"]).json()
        assert abs(r["cost"] - p["cost"]) < TOL and r["transfers"] == p["transfers"]
    with open(os.path.join(service.RESULTS_DIR, "penalty_flips.csv"), newline="") as f:
        row = next(x for x in csv.DictReader(f) if x["origin"] == "S037" and x["dest"] == "S052")
    flip = float(row["first_flip_penalty"])
    below = get("/api/route", origin="S037", dest="S052", penalty=flip - 0.05).json()
    above = get("/api/route", origin="S037", dest="S052", penalty=flip + 0.05).json()
    assert below["transfers"] == int(row["transfers_before"]) and above["transfers"] == int(row["transfers_after"])


def test_experiments_equal_csv():
    e = get("/api/experiments").json()
    with open(os.path.join(service.RESULTS_DIR, "summary_comparison.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(e["comparison"]) == len(rows)
    for api, raw in zip(e["comparison"], rows):
        assert api["network"] == raw["network"] and api["bucket"] == raw["bucket"]
        assert abs(api["median_expanded_ratio"] - float(raw["median_expanded_ratio"])) < 1e-9
    with open(os.path.join(service.RESULTS_DIR, "experiment_meta.json")) as f:
        meta = json.load(f)
    assert e["meta"]["total_astar_dijkstra_cost_mismatches"] == meta["total_astar_dijkstra_cost_mismatches"] == 0
    n_trips = sum(meta["pairs_per_network"].values())
    assert len(e["trips"]) == n_trips and len(e["baseline_trips"]) == n_trips
    assert e["flips_total"] == meta["flip_trips_found_city_80"]
    assert all(t["exp_astar"] > 0 and t["exp_dijkstra"] > 0 for t in e["trips"])


def test_demo_flips_are_real():
    for f in get("/api/flips", limit=8).json():
        lo = get("/api/route", origin=f["origin"], dest=f["dest"], penalty=max(f["flip_penalty"] - 0.05, 0)).json()
        hi = get("/api/route", origin=f["origin"], dest=f["dest"], penalty=f["flip_penalty"] + 0.05).json()
        assert lo["transfers"] == f["transfers_before"] and hi["transfers"] == f["transfers_after"]


def test_errors_are_clear():
    assert get("/api/route", origin="S001", dest="S001").status_code == 400
    r = get("/api/route", origin="S999", dest="S001")
    assert r.status_code == 400 and "Unknown stop" in r.json()["detail"]
    assert get("/api/route", origin="S001", dest="S002", algo="bfs").status_code == 422
    assert get("/api/route", origin="S001", dest="S002", penalty=-1).status_code == 422
    assert get("/api/route", origin="50,50", dest="S002").status_code == 400
    assert get("/api/health").json()["ok"] is True
    assert get("/").status_code == 200


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as ex:                      # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__}: {type(ex).__name__}: {ex}")
    print(f"\n{len(tests) - failed}/{len(tests)} backend tests pass")
    raise SystemExit(1 if failed else 0)
