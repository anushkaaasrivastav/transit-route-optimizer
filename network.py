"""
network.py -- data model, CSV I/O, and the state-expanded search graph.

This is the ONE place that defines what the graph is and what every edge costs.
A* and Dijkstra (written later) both call TransitGraph.neighbors(), so they are
guaranteed to use the exact same costs.

--------------------------------------------------------------------------
THE GRAPH (state-expanded, so transfers can be counted correctly)
--------------------------------------------------------------------------
Nodes
  ("R", route_id, seq)  "I am on a vehicle of route_id that has JUST ARRIVED at its seq-th stop"
                        (so every R-node was reached by riding at least one segment)
  ("A", stop_id)        "I am standing at stop_id, having just got off a vehicle"
  ("O",)                virtual Origin point of one query
  ("D",)                virtual Destination point of one query

Edges (cost in minutes)                                       kind
  ride    (R,r,i)  -> (R,r,i+1)   ride time of that segment            "ride"
  alight  (R,r,i)  -> (A,stop_i)  0                                    "alight"
  walk    (A,s)    -> (A,s2)      walk time of the link                "walk"
  board   (A,s)    -> (R,r,j)     headway/2 + TRANSFER PENALTY         "board"
                                  + ride time of the first segment (j = next stop after s)
  access  (O)      -> (R,r,j)     walk(origin->s) + headway/2          "access"  (NO penalty)
                                  + ride time of the first segment
  egress  (A,s)    -> (D)         walk(stop->dest)                     "egress"
  direct  (O)      -> (D)         walk(origin->dest), if short         "direct"

Boarding lands on the NEXT stop (it includes the first ride segment) so that a
passenger can never "board and alight at the same stop" -- that would be a phantom
journey leg that charges a wait and a penalty for nothing.

Why this makes transfers right: the FIRST boarding is an 'access' edge (no
penalty). Every later boarding has to leave from an ("A", stop) node, i.e. after
getting off a vehicle, and that is a 'board' edge which carries the penalty.
So  transfers = (number of 'board' edges on the path),  and walking before the
first boarding never counts as a transfer.

Origin/Destination are POINTS (x, y). The graph itself is immutable; the
Origin/Destination edges are produced on the fly per query ("overlay").
--------------------------------------------------------------------------
"""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass

# ----------------------------------------------------------------------------
# Cost-model / physics parameters (stored in config.json next to the CSVs)
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "transfer_penalty_min": 5.0,   # p: minutes added for every boarding after the first
    "wait_factor": 0.5,            # expected wait = wait_factor * headway   (random arrivals)
    "walk_speed_kmh": 4.8,         # 80 m/min
    "walk_detour": 1.2,            # real walking path is 1.2x the straight line
    "access_radius_km": 0.6,       # max walk from origin to a boarding stop / from a stop to dest
    "walk_cap_km": 1.5,            # origin->dest direct walking allowed only up to this
    "v_max_kmh": 40.0,             # fastest speed of ANY edge; used by the heuristic
}

DATASET_FILES = ["stops.csv", "routes.csv", "route_stops.csv", "walk_links.csv"]


def dist_km(p, q) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


# ----------------------------------------------------------------------------
# Plain data containers
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Stop:
    id: str
    name: str
    x: float
    y: float

    @property
    def pos(self):
        return (self.x, self.y)


@dataclass
class Route:
    """One DIRECTION of a service. stops[i] is visited at position i.

    ride[i] = minutes from stops[i-1] to stops[i].
      - normal route: ride[0] == 0
      - loop route:   ride[0] = minutes from the LAST stop back to stops[0]
        (the vehicle keeps circulating; stops are NOT repeated at the end)
    """
    id: str
    name: str
    mode: str          # "bus" | "metro"
    headway: float     # minutes between vehicles
    loop: bool
    stops: list
    ride: list


class Network:
    def __init__(self, stops, routes, walk_links, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.stops = {s.id: s for s in stops}
        self.routes = {r.id: r for r in routes}
        self.walk_links = list(walk_links)  # (a, b, walk_min) undirected
        self.walk_adj = {sid: [] for sid in self.stops}
        for a, b, minutes in self.walk_links:
            self.walk_adj[a].append((b, minutes))
            self.walk_adj[b].append((a, minutes))
        # where can a passenger board? (not at the last stop of a non-loop route)
        # entries are (route_id, i, j): board at position i, first segment ends at position j
        self.board_at = {sid: [] for sid in self.stops}
        for r in self.routes.values():
            n = len(r.stops)
            for i, sid in enumerate(r.stops):
                if r.loop:
                    self.board_at[sid].append((r.id, i, (i + 1) % n))
                elif i < n - 1:
                    self.board_at[sid].append((r.id, i, i + 1))

    # -- construction -------------------------------------------------------
    @classmethod
    def from_dict(cls, raw):
        stops = [Stop(s["id"], s.get("name", s["id"]), float(s["x"]), float(s["y"])) for s in raw["stops"]]
        routes = [
            Route(r["id"], r.get("name", r["id"]), r["mode"], float(r["headway"]), bool(r.get("loop", False)),
                  list(r["stops"]), [float(t) for t in r["ride"]])
            for r in raw["routes"]
        ]
        links = [(w["a"], w["b"], float(w["walk_min"])) for w in raw.get("walk_links", [])]
        return cls(stops, routes, links, raw.get("config"))

    @classmethod
    def from_dir(cls, path):
        return cls.from_dict(read_raw(path))

    # -- helpers --------------------------------------------------------------
    def walk_minutes(self, d_km: float) -> float:
        c = self.config
        return d_km * c["walk_detour"] * 60.0 / c["walk_speed_kmh"]

    def query(self, origin, dest, transfer_penalty=None) -> "Query":
        p = self.config["transfer_penalty_min"] if transfer_penalty is None else transfer_penalty
        return Query(tuple(origin), tuple(dest), float(p))


@dataclass(frozen=True)
class Query:
    origin: tuple
    dest: tuple
    transfer_penalty: float


# ----------------------------------------------------------------------------
# The graph used by BOTH search algorithms
# ----------------------------------------------------------------------------
ORIGIN = ("O",)
DEST = ("D",)


class TransitGraph:
    def __init__(self, net: Network):
        self.net = net
        c = net.config
        self.wait_factor = c["wait_factor"]
        self.radius = c["access_radius_km"]
        self.walk_cap = c["walk_cap_km"]
        self.v_max = c["v_max_kmh"] / 60.0  # km per minute

    # -- node bookkeeping -----------------------------------------------------
    def static_nodes(self):
        for r in self.net.routes.values():
            for i in range(len(r.stops)):
                yield ("R", r.id, i)
        for sid in self.net.stops:
            yield ("A", sid)

    def all_nodes(self):
        yield from self.static_nodes()
        yield ORIGIN
        yield DEST

    def position(self, node, q: Query):
        t = node[0]
        if t == "R":
            return self.net.stops[self.net.routes[node[1]].stops[node[2]]].pos
        if t == "A":
            return self.net.stops[node[1]].pos
        return q.origin if t == "O" else q.dest

    # -- edges -----------------------------------------------------------------
    def neighbors(self, node, q: Query):
        """Yield (next_node, cost_minutes, kind). Single source of truth for costs."""
        net = self.net
        t = node[0]

        if t == "R":
            _, rid, i = node
            r = net.routes[rid]
            n = len(r.stops)
            if r.loop:
                j = (i + 1) % n
                yield ("R", rid, j), r.ride[j], "ride"
            elif i + 1 < n:
                yield ("R", rid, i + 1), r.ride[i + 1], "ride"
            yield ("A", r.stops[i]), 0.0, "alight"

        elif t == "A":
            sid = node[1]
            for sid2, minutes in net.walk_adj[sid]:
                yield ("A", sid2), minutes, "walk"
            for rid, i, j in net.board_at[sid]:
                r = net.routes[rid]
                yield ("R", rid, j), self.wait_factor * r.headway + q.transfer_penalty + r.ride[j], "board"
            d = dist_km(net.stops[sid].pos, q.dest)
            if d <= self.radius:
                yield DEST, net.walk_minutes(d), "egress"

        elif t == "O":
            for sid, s in net.stops.items():
                d = dist_km(s.pos, q.origin)
                if d <= self.radius:
                    walk = net.walk_minutes(d)
                    for rid, i, j in net.board_at[sid]:
                        r = net.routes[rid]
                        yield ("R", rid, j), walk + self.wait_factor * r.headway + r.ride[j], "access"  # no penalty
            d = dist_km(q.origin, q.dest)
            if d <= self.walk_cap:
                yield DEST, net.walk_minutes(d), "direct"
        # DEST has no outgoing edges

    # -- heuristic ---------------------------------------------------------------
    def heuristic(self, node, q: Query) -> float:
        """h(n) = straight-line distance to destination / fastest speed anywhere (minutes).

        Admissible: no edge covers more than v_max km per minute and waiting /
        penalties are >= 0, so the real remaining cost can never be smaller.
        Consistent: h(u) - h(v) <= dist(u,v)/v_max <= cost(u,v).
        """
        if node == DEST:
            return 0.0
        return dist_km(self.position(node, q), q.dest) / self.v_max


# ----------------------------------------------------------------------------
# CSV reading / writing (format lives here so generator & loader never drift)
# ----------------------------------------------------------------------------
def read_raw(path) -> dict:
    """Read a dataset folder into plain dicts (no validation, no objects)."""
    def rows(name):
        with open(os.path.join(path, name), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    stops = [{"id": r["stop_id"], "name": r["name"], "x": float(r["x_km"]), "y": float(r["y_km"])}
             for r in rows("stops.csv")]

    seqs = {}
    for r in rows("route_stops.csv"):
        seqs.setdefault(r["route_id"], []).append((int(r["seq"]), r["stop_id"], float(r["ride_min_from_prev"])))

    routes = []
    for r in rows("routes.csv"):
        items = sorted(seqs.get(r["route_id"], []))
        routes.append({
            "id": r["route_id"], "name": r["name"], "mode": r["mode"],
            "headway": float(r["headway_min"]), "loop": r["loop"] == "1",
            "stops": [s for _, s, _ in items], "ride": [t for _, _, t in items],
            "seqs": [q for q, _, _ in items],
        })

    links = [{"a": r["stop_a"], "b": r["stop_b"], "distance_km": float(r["distance_km"]),
              "walk_min": float(r["walk_min"])} for r in rows("walk_links.csv")]

    with open(os.path.join(path, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    meta = {}
    mp = os.path.join(path, "metadata.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
    return {"stops": stops, "routes": routes, "walk_links": links, "config": config, "metadata": meta}


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_dataset(path, stops, routes, walk_links, config):
    """stops: list[Stop]; routes: list[Route]; walk_links: list of (a, b, distance_km, walk_min)."""
    os.makedirs(path, exist_ok=True)
    _write_csv(os.path.join(path, "stops.csv"), ["stop_id", "name", "x_km", "y_km"],
               [(s.id, s.name, f"{s.x:.3f}", f"{s.y:.3f}") for s in stops])
    _write_csv(os.path.join(path, "routes.csv"), ["route_id", "name", "mode", "headway_min", "loop"],
               [(r.id, r.name, r.mode, f"{r.headway:g}", int(r.loop)) for r in routes])
    rs = []
    for r in routes:
        for i, (sid, t) in enumerate(zip(r.stops, r.ride)):
            rs.append((r.id, i, sid, f"{t:.2f}"))
    _write_csv(os.path.join(path, "route_stops.csv"), ["route_id", "seq", "stop_id", "ride_min_from_prev"], rs)
    _write_csv(os.path.join(path, "walk_links.csv"), ["stop_a", "stop_b", "distance_km", "walk_min"],
               [(a, b, f"{d:.3f}", f"{m:.2f}") for a, b, d, m in walk_links])
    with open(os.path.join(path, "config.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")
