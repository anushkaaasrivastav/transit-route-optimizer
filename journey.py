"""
journey.py -- turn a SearchResult (a list of graph nodes) into something a person can read.

The search only knows one number per edge ("cost"). Here we take the found path apart and
split every edge's cost into what it is made of:

    ride     minutes spent in a vehicle
    wait     expected waiting time at the stop (headway / 2)
    walk     minutes walking (to the first stop, between stops, from the last stop)
    penalty  the transfer penalty (charged on every boarding after the first)

and we build a list of "legs" (walk leg / ride leg) that the website will draw.

SAFETY NET: ride + wait + walk + penalty must equal the cost the search reported. Journey.check()
verifies this, so the breakdown can never drift away from the real cost model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from network import DEST, ORIGIN, TransitGraph, Query, dist_km


@dataclass
class Journey:
    ride: float = 0.0
    wait: float = 0.0
    walk: float = 0.0
    penalty: float = 0.0
    transfers: int = 0
    vehicle_stops: int = 0       # number of stop-to-stop segments spent inside vehicles
    distance_km: float = 0.0     # straight-line distance of all ride and walk segments
    legs: list = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.ride + self.wait + self.walk + self.penalty

    def check(self, search_cost: float, tol: float = 1e-6):
        if abs(self.total - search_cost) > tol:
            raise AssertionError(f"breakdown {self.total:.6f} != search cost {search_cost:.6f}")


def _base(route_id: str) -> str:
    return route_id.rsplit("_", 1)[0]          # "B3_out" -> "B3"


def breakdown(graph: TransitGraph, q: Query, result, verify: bool = True) -> Journey:
    """verify=True asserts the parts add up to result.cost. Pass verify=False when the path was
    found with a DIFFERENT cost (e.g. the shortest-distance baseline) and you only want to
    evaluate it under the real time-based cost model."""
    net = graph.net
    wf = graph.wait_factor
    j = Journey()
    cur = None                                  # the ride leg currently being built

    def pos(sid):
        return net.stops[sid].pos

    def walk_leg(frm, to, minutes, km):
        j.walk += minutes
        j.distance_km += km
        j.legs.append({"kind": "walk", "from": frm, "to": to, "minutes": minutes, "km": km})

    def start_ride(route, board_idx, first_idx, wait, penalty):
        nonlocal cur
        j.wait += wait
        j.penalty += penalty
        j.ride += route.ride[first_idx]
        j.vehicle_stops += 1
        seg_km = dist_km(pos(route.stops[board_idx]), pos(route.stops[first_idx]))
        j.distance_km += seg_km
        cur = {"kind": "ride", "route": _base(route.id), "route_id": route.id, "name": route.name,
               "mode": route.mode, "stops": [route.stops[board_idx], route.stops[first_idx]],
               "wait": wait, "penalty": penalty, "ride": route.ride[first_idx], "km": seg_km}
        j.legs.append(cur)

    for (u, v, kind) in zip(result.path, result.path[1:], result.kinds):
        if kind == "access":                    # Origin -> first vehicle (no penalty)
            route = net.routes[v[1]]
            i = (v[2] - 1) % len(route.stops)
            stop = route.stops[i]
            d = dist_km(q.origin, pos(stop))
            if d > 0:
                walk_leg("origin", stop, net.walk_minutes(d), d)
            start_ride(route, i, v[2], wf * route.headway, 0.0)

        elif kind == "board":                   # a TRANSFER: wait + penalty + first segment
            route = net.routes[v[1]]
            i = (v[2] - 1) % len(route.stops)
            start_ride(route, i, v[2], wf * route.headway, q.transfer_penalty)
            j.transfers += 1

        elif kind == "ride":
            route = net.routes[u[1]]
            nxt = route.stops[v[2]]
            seg_km = dist_km(pos(route.stops[u[2]]), pos(nxt))
            j.ride += route.ride[v[2]]
            j.vehicle_stops += 1
            j.distance_km += seg_km
            cur["stops"].append(nxt)
            cur["ride"] += route.ride[v[2]]
            cur["km"] += seg_km

        elif kind == "alight":
            cur = None

        elif kind == "walk":                    # stop -> nearby stop on foot
            a, b = u[1], v[1]
            minutes = next(m for s, m in net.walk_adj[a] if s == b)
            walk_leg(a, b, minutes, dist_km(pos(a), pos(b)))

        elif kind == "egress":                  # last stop -> destination point
            d = dist_km(pos(u[1]), q.dest)
            if d > 0:
                walk_leg(u[1], "destination", net.walk_minutes(d), d)

        elif kind == "direct":                  # walk the whole way
            d = dist_km(q.origin, q.dest)
            walk_leg("origin", "destination", net.walk_minutes(d), d)

    if verify:
        j.check(result.cost)
    return j
