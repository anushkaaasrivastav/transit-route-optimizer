"""
astar.py -- A* and Dijkstra as ONE search function.

The whole idea (for the viva):

    f(n) = g(n) + h(n)

    g(n)  cost of the best path found so far from Origin to n       (known)
    h(n)  estimate of the remaining cost from n to Destination      (guess)

The search always expands the open node with the smallest f.
    - With h(n) = 0 for every node, f = g and this IS Dijkstra's algorithm.
    - With h(n) = straight-line distance / v_max, it is A*.
Because both are the same function with only h changed, any difference in the
number of expanded states is caused by the heuristic and nothing else.

Both use TransitGraph.neighbors(), so they see exactly the same edges and costs.
"""
from __future__ import annotations

import heapq
import itertools
import math
import statistics
import time
from dataclasses import dataclass, field

from network import DEST, ORIGIN, TransitGraph, Query


@dataclass
class SearchResult:
    found: bool
    cost: float = math.inf       # total generalized cost in minutes (inf if no route)
    path: list = field(default_factory=list)    # nodes from Origin to Destination
    kinds: list = field(default_factory=list)   # edge kind for each step of the path
    expanded: int = 0            # states removed from the open list and expanded
    generated: int = 0           # states pushed onto the open list
    transfers: int = 0           # boardings after the first = number of "board" edges


def search(graph: TransitGraph, q: Query, h_weight: float = 1.0, trace: list | None = None) -> SearchResult:
    """Best-first search from Origin to Destination.

    h_weight = 1  -> A*        (admissible, consistent heuristic -> optimal)
    h_weight = 0  -> Dijkstra  (no guidance)
    h_weight > 1  -> weighted A* (faster but may be suboptimal; only used for experiments)

    trace: pass an empty list to receive every expanded node in expansion order (the website uses
    this to replay the search). It never changes the result; leave it as None for timing runs.
    """
    h_cache = {}

    def h(n):
        if h_weight == 0:
            return 0.0
        v = h_cache.get(n)
        if v is None:
            v = h_cache[n] = h_weight * graph.heuristic(n, q)
        return v

    g = {ORIGIN: 0.0}          # best known cost from Origin to each node
    parent = {}                # node -> (previous node, kind of edge used to reach it)
    closed = set()             # nodes already expanded; their g is final
    tie = itertools.count()    # tie-breaker, see below
    open_heap = [(h(ORIGIN), next(tie), ORIGIN)]
    expanded = 0
    generated = 1

    while open_heap:
        _, _, u = heapq.heappop(open_heap)

        if u in closed:
            continue           # stale duplicate (a cheaper copy of u was already expanded)
        closed.add(u)

        if u == DEST:          # first time the goal is POPPED its g is optimal
            break

        expanded += 1
        if trace is not None:
            trace.append(u)
        gu = g[u]
        for v, w, kind in graph.neighbors(u, q):
            if v in closed:
                continue       # safe only because h is consistent (checked by validate.py)
            new_g = gu + w
            if new_g < g.get(v, math.inf):      # found a cheaper way to reach v
                g[v] = new_g
                parent[v] = (u, kind)
                heapq.heappush(open_heap, (new_g + h(v), next(tie), v))
                generated += 1

    if DEST not in closed:
        return SearchResult(False, expanded=expanded, generated=generated)

    path, kinds, node = [DEST], [], DEST
    while node != ORIGIN:
        node, kind = parent[node]
        path.append(node)
        kinds.append(kind)
    path.reverse()
    kinds.reverse()
    return SearchResult(True, g[DEST], path, kinds, expanded, generated,
                        transfers=sum(1 for k in kinds if k == "board"))


def astar(graph: TransitGraph, q: Query) -> SearchResult:
    return search(graph, q, h_weight=1.0)


def dijkstra(graph: TransitGraph, q: Query) -> SearchResult:
    return search(graph, q, h_weight=0.0)


def time_search(fn, graph, q, repeats=200):
    """Median wall-clock time of fn(graph, q) in microseconds, plus the result of the last run.

    One search on a tiny network takes microseconds, so a single measurement is mostly noise.
    We repeat it and take the median (robust to the occasional slow run).
    """
    result = None
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn(graph, q)
        times.append((time.perf_counter() - t0) * 1e6)
    return result, statistics.median(times)
