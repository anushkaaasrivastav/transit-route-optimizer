"""
oracle.py -- reference solvers used ONLY for validation.

These are NOT the project's A*/Dijkstra (you will write those yourself next).
They exist so the dataset, the graph and the heuristic can be checked against
answers obtained in other ways:

  graph_dijkstra()    plain Dijkstra over TransitGraph (checks the graph builds sensible edges)
  reverse_distances() exact remaining cost h*(n) from every node to Destination (to test h <= h*)
  brute_force()       enumerates whole JOURNEYS (rides + walks) straight from the raw tables,
                      without touching TransitGraph, so it can catch graph-construction bugs
"""
from __future__ import annotations

import heapq
import math

from network import DEST, ORIGIN, TransitGraph, dist_km


def graph_dijkstra(graph: TransitGraph, q):
    """Return dict(cost, path, kinds, rides, transfers, max_walk_chain) or None if unreachable."""
    dist = {ORIGIN: 0.0}
    prev = {}
    heap = [(0.0, 0, ORIGIN)]
    tie = 0
    done = set()
    while heap:
        d, _, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == DEST:
            break
        for v, w, kind in graph.neighbors(u, q):
            nd = d + w
            if nd < dist.get(v, math.inf) - 1e-12:
                dist[v] = nd
                prev[v] = (u, kind)
                tie += 1
                heapq.heappush(heap, (nd, tie, v))
    if DEST not in done:
        return None
    path, kinds, node = [DEST], [], DEST
    while node != ORIGIN:
        node, kind = prev[node][0], prev[node][1]
        kinds.append(kind)
        path.append(node)
    path.reverse()
    kinds.reverse()
    rides = sum(1 for k in kinds if k in ("access", "board"))
    chain = best = 0
    for k in kinds:
        chain = chain + 1 if k == "walk" else 0
        best = max(best, chain)
    return {"cost": dist[DEST], "path": path, "kinds": kinds, "rides": rides,
            "transfers": sum(1 for k in kinds if k == "board"), "max_walk_chain": best}


def reverse_distances(graph: TransitGraph, q):
    """h*(n): exact cheapest remaining cost from node n to Destination, for every node that can reach it."""
    rev = {}
    for u in graph.all_nodes():
        for v, w, _ in graph.neighbors(u, q):
            rev.setdefault(v, []).append((u, w))
    dist = {DEST: 0.0}
    heap = [(0.0, 0, DEST)]
    tie = 0
    while heap:
        d, _, v = heapq.heappop(heap)
        if d > dist.get(v, math.inf):
            continue
        for u, w in rev.get(v, ()):
            nd = d + w
            if nd < dist.get(u, math.inf) - 1e-12:
                dist[u] = nd
                tie += 1
                heapq.heappush(heap, (nd, tie, u))
    return dist


def brute_force(net, q, max_rides=4, max_walks=2, bound=math.inf):
    """Cheapest journey found by enumerating every combination of rides and walks.

    Works from net.routes / net.stops / net.walk_adj only. Limits: at most `max_rides`
    vehicle legs and `max_walks` consecutive walk links between legs. Branches costing
    >= the best known (or `bound`) are pruned (valid, since all costs are >= 0).
    Returns the best cost (< bound) or None when nothing cheaper than `bound` exists.
    """
    cfg = net.config
    radius, cap, wf = cfg["access_radius_km"], cfg["walk_cap_km"], cfg["wait_factor"]
    p = q.transfer_penalty
    best = [bound]
    found = [False]

    def consider(c):
        if c < best[0] - 1e-12:
            best[0] = c
            found[0] = True

    d0 = dist_km(q.origin, q.dest)
    if d0 <= cap:
        consider(net.walk_minutes(d0))

    def rides_from(r, i):
        """(alight index, minutes riding) for every stop reachable on r from position i."""
        n = len(r.stops)
        out, total = [], 0.0
        if r.loop:
            for k in range(1, n):
                total += r.ride[(i + k) % n]
                out.append(((i + k) % n, total))
        else:
            for j in range(i + 1, n):
                total += r.ride[j]
                out.append((j, total))
        return out

    def boardings(sid):
        for r in net.routes.values():
            n = len(r.stops)
            for i, s in enumerate(r.stops):
                if s == sid and (r.loop or i < n - 1):
                    yield r, i

    def after_alight(stop, cost, rides_used):
        stack = [(stop, 0.0, 0, frozenset([stop]))]
        while stack:
            u, wcost, k, seen = stack.pop()
            total = cost + wcost
            if total >= best[0]:
                continue
            d = dist_km(net.stops[u].pos, q.dest)
            if d <= radius:
                consider(total + net.walk_minutes(d))
            if rides_used < max_rides:
                for r, i in boardings(u):
                    base = total + wf * r.headway + p
                    for j, rt in rides_from(r, i):
                        if base + rt < best[0]:
                            after_alight(r.stops[j], base + rt, rides_used + 1)
            if k < max_walks:
                for v, m in net.walk_adj[u]:
                    if v not in seen:
                        stack.append((v, wcost + m, k + 1, seen | {v}))

    for sid, s in net.stops.items():
        d = dist_km(s.pos, q.origin)
        if d <= radius:
            acc = net.walk_minutes(d)
            for r, i in boardings(sid):
                base = acc + wf * r.headway
                for j, rt in rides_from(r, i):
                    if base + rt < best[0]:
                        after_alight(r.stops[j], base + rt, 1)

    return best[0] if found[0] else None
