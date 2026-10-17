"""
service.py -- everything the website needs, as plain Python functions (no web framework here).

    server.py (FastAPI)  ->  service.py  ->  astar.py / journey.py / network.py   (the source of truth)

Every number shown in the planner comes from calling the real algorithms in this module.
Every number shown on the experiments dashboard is read from the CSV files in results/.
Nothing is computed or invented in the browser.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
from functools import lru_cache

from astar import astar, dijkstra, search, time_search
from journey import breakdown
from network import DEST, ORIGIN, Network, TransitGraph, dist_km

ROOT = os.path.dirname(os.path.abspath(__file__))
CITY_DIR = os.path.join(ROOT, "data", "synthetic_city")
RESULTS_DIR = os.path.join(ROOT, "results")

# One colour per line, fixed by line id so the same line has the same colour everywhere.
# (palettes checked with the dataviz validator; the map also prints a badge on every line)
METRO_COLORS = ["#c8102e", "#0b4f9c", "#6b2c91", "#1d6b3a"]
BUS_COLORS = ["#eb6834", "#1baf7a", "#b98300", "#e87ba4", "#008300", "#6250d6", "#1496b8", "#b04e1e"]

ALGOS = {"astar": astar, "dijkstra": dijkstra}


class UserError(ValueError):
    """A problem with the request itself (shown to the user as a 400)."""


# ----------------------------------------------------------------------------
# the city (loaded once)
# ----------------------------------------------------------------------------
class City:
    def __init__(self, path):
        self.path = path
        self.raw = None
        self.net = Network.from_dir(path)
        self.graph = TransitGraph(self.net)
        self.lock = threading.Lock()          # the search is pure Python; keep timings clean
        self.lines = self._build_lines()
        self.payload = self._build_payload()

    def _build_lines(self):
        from network import read_raw
        self.raw = read_raw(self.path)
        lines, metro_i, bus_i = {}, 0, 0
        for r in self.net.routes.values():
            if not r.id.endswith("_out"):
                continue
            base = r.id[:-4]
            if r.mode == "metro":
                color = METRO_COLORS[metro_i % len(METRO_COLORS)]
                metro_i += 1
            else:
                color = BUS_COLORS[bus_i % len(BUS_COLORS)]
                bus_i += 1
            lines[base] = {"code": base, "name": re.sub(r"\s*\((out|in)\)$", "", r.name), "mode": r.mode,
                           "headway": r.headway, "loop": r.loop, "color": color, "stops": list(r.stops),
                           "ride_minutes": round(sum(r.ride), 2)}
        return lines

    def _build_payload(self):
        serve = {sid: [] for sid in self.net.stops}
        for code, ln in self.lines.items():
            for s in dict.fromkeys(ln["stops"]):
                serve[s].append(code)
        stops = []
        for sid, s in self.net.stops.items():
            codes = serve[sid]
            modes = sorted({self.lines[c]["mode"] for c in codes})
            stops.append({"id": sid, "name": s.name, "x": s.x, "y": s.y, "lines": codes, "modes": modes,
                          "interchange": len(codes) >= 2, "metro": "metro" in modes})
        pos = {s["id"]: (s["x"], s["y"]) for s in stops}
        lines = []
        for code, ln in self.lines.items():
            seq = ln["stops"] + ([ln["stops"][0]] if ln["loop"] else [])
            lines.append({**{k: v for k, v in ln.items() if k != "stops"}, "stop_ids": ln["stops"],
                          "coords": [pos[s] for s in seq]})
        walks = [{"a": a, "b": b, "minutes": m, "coords": [pos[a], pos[b]]} for a, b, m in self.net.walk_links]
        meta = self.raw["metadata"]
        size = meta["parameters"]["city_size_km"]
        return {"synthetic": True, "notice": meta["notice"], "name": meta["name"], "seed": meta["seed"],
                "size_km": size, "stops": stops, "lines": lines, "walks": walks,
                "config": self.net.config, "counts": meta["counts"]}


_city = None
_city_lock = threading.Lock()


def get_city() -> City:
    global _city
    with _city_lock:
        if _city is None:
            _city = City(CITY_DIR)
        return _city


def city_payload():
    return get_city().payload


# ----------------------------------------------------------------------------
# planning a trip
# ----------------------------------------------------------------------------
_POINT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _endpoint(city: City, spec: str):
    """'S037' (a stop) or '4.2,7.9' (a free point in km). Returns (position, description)."""
    if spec in city.net.stops:
        s = city.net.stops[spec]
        return s.pos, {"type": "stop", "id": s.id, "name": s.name, "x": s.x, "y": s.y}
    m = _POINT.match(spec or "")
    if m:
        x, y = float(m.group(1)), float(m.group(2))
        size = city.payload["size_km"]
        if not (-0.5 <= x <= size + 0.5 and -0.5 <= y <= size + 0.5):
            raise UserError("That point is outside the city map.")
        return (x, y), {"type": "point", "id": None, "name": f"Point ({x:.2f}, {y:.2f} km)", "x": x, "y": y}
    raise UserError(f"Unknown stop or point: {spec!r}")


def _leg_payload(city: City, leg, o_desc, d_desc):
    pos = {sid: (s.x, s.y) for sid, s in city.net.stops.items()}

    def where(ref):
        if ref == "origin":
            return (o_desc["x"], o_desc["y"])
        if ref == "destination":
            return (d_desc["x"], d_desc["y"])
        return pos[ref]

    if leg["kind"] == "ride":
        line = city.lines[leg["route"]]
        return {"kind": "ride", "line": leg["route"], "name": line["name"], "mode": line["mode"],
                "color": line["color"], "headway": line["headway"], "stop_ids": leg["stops"],
                "stop_names": [city.net.stops[s].name for s in leg["stops"]],
                "coords": [pos[s] for s in leg["stops"]], "ride_min": round(leg["ride"], 3),
                "wait_min": round(leg["wait"], 3), "penalty_min": round(leg["penalty"], 3),
                "km": round(leg["km"], 3)}
    a, b = leg["from"], leg["to"]
    return {"kind": "walk", "from": a, "to": b,
            "from_name": {"origin": o_desc["name"]}.get(a, city.net.stops[a].name if a in city.net.stops else a),
            "to_name": {"destination": d_desc["name"]}.get(b, city.net.stops[b].name if b in city.net.stops else b),
            "coords": [where(a), where(b)], "walk_min": round(leg["minutes"], 3), "km": round(leg["km"], 3)}


def _trace_payload(city: City, nodes):
    """Expanded graph nodes -> [stop_id or 'O', kind] so the browser can replay the search on the map."""
    out = []
    for n in nodes:
        if n == ORIGIN:
            out.append(["O", "O"])
        elif n[0] == "R":
            out.append([city.net.routes[n[1]].stops[n[2]], "R"])
        elif n[0] == "A":
            out.append([n[1], "A"])
    return out


def _summary(legs):
    """'M2 > M1 > B3' from the raw journey legs (journey.py names the line key 'route')."""
    rides = [l["route"] for l in legs if l["kind"] == "ride"]
    return " > ".join(rides) if rides else "Walk"


def plan(origin: str, dest: str, algo: str = "astar", penalty: float = 5.0, replay: bool = False,
         repeats: int = 15):
    """Find the best route with the real A* / Dijkstra and describe it for the browser."""
    if algo not in ALGOS:
        raise UserError(f"Unknown algorithm {algo!r}; use 'astar' or 'dijkstra'.")
    if not 0 <= penalty <= 60:
        raise UserError("Transfer penalty must be between 0 and 60 minutes.")
    city = get_city()
    o_pos, o_desc = _endpoint(city, origin)
    d_pos, d_desc = _endpoint(city, dest)
    if dist_km(o_pos, d_pos) < 1e-9:
        raise UserError("Start and destination are the same place.")
    q = city.net.query(o_pos, d_pos, penalty)

    with city.lock:
        runs = {name: time_search(fn, city.graph, q, repeats) for name, fn in ALGOS.items()}
        res, t_us = runs[algo]
        trace = []
        if replay:
            search(city.graph, q, 1.0 if algo == "astar" else 0.0, trace=trace)

    compare = {name: {"found": r.found, "cost": round(r.cost, 4) if r.found else None, "expanded": r.expanded,
                      "generated": r.generated, "time_us": round(t, 2)} for name, (r, t) in runs.items()}
    compare["same_cost"] = (runs["astar"][0].found == runs["dijkstra"][0].found and
                            (not res.found or abs(runs["astar"][0].cost - runs["dijkstra"][0].cost) < 1e-6))

    out = {"ok": True, "found": res.found, "algo": algo, "penalty": penalty, "origin": o_desc, "dest": d_desc,
           "stats": {"expanded": res.expanded, "generated": res.generated, "time_us": round(t_us, 2)},
           "compare": compare, "graph": {"nodes": len(list(city.graph.static_nodes())) + 2}}
    if replay:
        out["trace"] = _trace_payload(city, trace)
    if not res.found:
        out["message"] = ("No route found. A start or end point more than "
                          f"{int(city.net.config['access_radius_km'] * 1000)} m from every stop can only be walked "
                          f"when the whole trip is under {city.net.config['walk_cap_km']} km.")
        return out

    jn = breakdown(city.graph, q, res)                     # asserts ride+wait+walk+penalty == cost
    legs = [_leg_payload(city, l, o_desc, d_desc) for l in jn.legs]
    out.update({
        "cost": round(res.cost, 4),
        "breakdown": {"ride": round(jn.ride, 4), "wait": round(jn.wait, 4), "walk": round(jn.walk, 4),
                      "penalty": round(jn.penalty, 4)},
        "transfers": jn.transfers, "vehicle_stops": jn.vehicle_stops, "distance_km": round(jn.distance_km, 3),
        "summary": _summary(jn.legs), "legs": legs})
    return out


def sweep(origin: str, dest: str, max_penalty: int = 30, step: int = 1):
    """Re-plan one trip at many penalties (A*) so the browser can show how the route reacts."""
    city = get_city()
    o_pos, o_desc = _endpoint(city, origin)
    d_pos, d_desc = _endpoint(city, dest)
    if dist_km(o_pos, d_pos) < 1e-9:
        raise UserError("Start and destination are the same place.")
    points = []
    with city.lock:
        for p in range(0, max_penalty + 1, step):
            q = city.net.query(o_pos, d_pos, float(p))
            res = astar(city.graph, q)
            if not res.found:
                return {"ok": True, "found": False, "points": []}
            jn = breakdown(city.graph, q, res)
            points.append({"penalty": p, "cost": round(res.cost, 4), "transfers": res.transfers,
                           "ride": round(jn.ride, 4), "wait": round(jn.wait, 4), "walk": round(jn.walk, 4),
                           "penalty_min": round(jn.penalty, 4), "summary": _summary(jn.legs)})
    changes = [b["penalty"] for a, b in zip(points, points[1:]) if a["summary"] != b["summary"]]
    return {"ok": True, "found": True, "origin": o_desc, "dest": d_desc, "points": points,
            "route_changes_at": changes}


# ----------------------------------------------------------------------------
# experiment results (read from the CSV files; never computed here)
# ----------------------------------------------------------------------------
def _num(v):
    if v is None or v == "":
        return v
    try:
        f = float(v)
        return int(f) if re.fullmatch(r"-?\d+", v.strip()) else f
    except ValueError:
        return v


def _read_csv(name):
    path = os.path.join(RESULTS_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{name} not found. Run `python experiments.py` first.")
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: _num(v) for k, v in row.items()} for row in csv.DictReader(f)]


@lru_cache(maxsize=4)
def _experiments_cached(stamp):
    meta_path = os.path.join(RESULTS_DIR, "experiment_meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    # compact per-trip table: one row per trip with both algorithms side by side
    rows = _read_csv("queries.csv")
    by_trip = {}
    for r in rows:
        by_trip.setdefault((r["network"], r["pair_id"]), {})[r["algo"]] = r
    trips = []
    for (net, pid), d in by_trip.items():
        a, b = d["astar"], d["dijkstra"]
        trips.append({"network": net, "bucket": a["bucket"], "km": round(a["straight_km"], 2),
                      "cost": a["cost"], "transfers": a["transfers"], "exp_astar": a["expanded"],
                      "exp_dijkstra": b["expanded"], "t_astar": a["time_us"], "t_dijkstra": b["time_us"]})
    base = _read_csv("baseline_queries.csv")
    base_trips = [{"network": r["network"], "bucket": r["bucket"], "extra_pct": round(r["extra_pct"], 3),
                   "extra_min": round(r["extra_min"], 3), "opt_transfers": r["opt_transfers"],
                   "base_transfers": r["base_transfers"], "same_route": r["same_route"]} for r in base]
    flips = _read_csv("penalty_flips.csv")
    return {"meta": meta, "networks": _read_csv("networks.csv"), "comparison": _read_csv("summary_comparison.csv"),
            "algorithms": _read_csv("summary_algorithms.csv"), "sweep": _read_csv("penalty_sweep_summary.csv"),
            "baseline": _read_csv("baseline_summary.csv"), "trips": trips, "baseline_trips": base_trips,
            "flips_total": len(flips), "flips": flips[:60]}


def experiments_payload():
    meta_path = os.path.join(RESULTS_DIR, "experiment_meta.json")
    if not os.path.exists(meta_path):
        raise FileNotFoundError("No experiment results found. Run `python experiments.py` first.")
    return _experiments_cached(os.path.getmtime(meta_path))


def demo_flips(limit: int = 12):
    """Interesting trips (from penalty_flips.csv): the route changes when the penalty crosses a value."""
    flips = experiments_payload()["flips"]
    city = get_city()
    out, seen = [], set()
    for f in flips:
        key = (f["origin"], f["dest"])
        if f["origin"] not in city.net.stops or f["dest"] not in city.net.stops or key in seen:
            continue
        if f["extra_minutes_without_transfers"] < 1.0 or not 3 <= f["first_flip_penalty"] <= 20:
            continue
        seen.add(key)
        out.append({"origin": f["origin"], "dest": f["dest"],
                    "origin_name": city.net.stops[f["origin"]].name, "dest_name": city.net.stops[f["dest"]].name,
                    "flip_penalty": round(f["first_flip_penalty"], 2), "transfers_before": f["transfers_before"],
                    "transfers_after": f["transfers_after"],
                    "extra_minutes": round(f["extra_minutes_without_transfers"], 2), "km": round(f["straight_km"], 1)})
        if len(out) >= limit:
            break
    return out
