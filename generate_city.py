"""
generate_city.py -- build the SYNTHETIC fictional city dataset.

    python generate_city.py --seed 42 --stops 80 --out data/synthetic_city

Everything is derived from one seeded random.Random, so the same
(seed, stops) always gives byte-identical files (checked by checksums in
metadata.json). The city is fictional: plain x/y kilometres, no real map.

What gets built (in this order):
  1. Metro lines   - straight corridors across the city; where two lines cross,
                     they share one interchange station.
  2. Bus routes    - radial, crosstown, feeder (from a metro station) and ring (loop)
                     routes. New bus stops that land within MERGE_R of an existing stop
                     are merged into it, which creates natural transfer points.
  3. Walk links    - between stops closer than WALK_LINK_R (each stop keeps its 4 nearest).
  4. Connectivity  - if the network fell into separate pieces, bridge them with the
                     shortest possible walk link (and record it in metadata.json).
Every ride time is derived from distance and a mode speed, and every speed is
below the declared v_max, so the A* heuristic is admissible by construction
(validate.py checks this independently instead of trusting it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random

from network import DEFAULT_CONFIG, Route, Stop, dist_km, write_dataset

GEN_VERSION = "1.0"

# ---- generator parameters (all recorded in metadata.json) -------------------
MARGIN = 0.4            # km kept free at the city edge for lines/routes
METRO_SPACING = 1.25    # km between metro stations (+-15%)
METRO_CRUISE_KMH = 36.0
METRO_DWELL_MIN = 0.5
BUS_SPACING = (0.55, 0.85)
BUS_SPEED_KMH = (14.0, 22.0)
BUS_CONGESTION = (1.0, 1.25)   # per-segment slowdown multiplier
BUS_DWELL_MIN = 0.3
MERGE_R = 0.30          # km: bus point this close to an existing stop reuses that stop
WALK_LINK_R = 0.50      # km
WALK_LINK_K = 4         # keep at most this many nearest links per stop
WALK_DETOUR = (1.1, 1.3)
REPAIR_MAX_KM = 0.8     # longest walk link the connectivity repair may add
SPEED_BOUNDS_KMH = {"metro": (15.0, 40.0), "bus": (5.0, 30.0)}   # used by validate.py

NAME_A = ["Alder", "Brook", "Cedar", "Dunmore", "Elm", "Fairview", "Garnet", "Harbor", "Ivy", "Juniper",
          "Kestrel", "Linden", "Maple", "Northgate", "Oakley", "Pinewood", "Quarry", "Ridge", "Sunhill",
          "Thorne", "Upland", "Vale", "Willow", "Yarrow", "Zephyr"]
NAME_B = ["Cross", "Square", "Green", "Market", "Park", "Gate", "Court", "Hill", "Road", "Bridge", "Lane",
          "Yard", "Row"]


class CityBuilder:
    def __init__(self, rng, size):
        self.rng = rng
        self.size = size
        self.stops: list[Stop] = []
        self._names = set()

    def _name(self, suffix):
        for _ in range(50):
            n = f"{self.rng.choice(NAME_A)} {self.rng.choice(NAME_B)}{suffix}"
            if n not in self._names:
                self._names.add(n)
                return n
        n = f"{self.rng.choice(NAME_A)} {self.rng.choice(NAME_B)} {len(self.stops) + 1}{suffix}"
        self._names.add(n)
        return n

    def nearest(self, p, within):
        best, best_d = None, within
        for i, s in enumerate(self.stops):
            d = dist_km(s.pos, p)
            if d <= best_d:
                best, best_d = i, d
        return best

    def add_stop(self, p, kind):
        lo, hi = 0.2, self.size - 0.2
        x, y = round(min(max(p[0], lo), hi), 3), round(min(max(p[1], lo), hi), 3)
        idx = len(self.stops)
        self.stops.append(Stop(f"S{idx + 1:03d}", self._name(" Station" if kind == "metro" else ""), x, y))
        return idx

    def get_or_make(self, p, kind, merge_r):
        idx = self.nearest(p, merge_r)
        return idx if idx is not None else self.add_stop(p, kind)

    def d(self, i, j):
        return dist_km(self.stops[i].pos, self.stops[j].pos)


def clip_range(p0, d, lo, hi):
    """Range of t for which p0 + t*d stays inside the square [lo,hi]^2."""
    tmin, tmax = -math.inf, math.inf
    for k in (0, 1):
        if abs(d[k]) < 1e-12:
            if not lo <= p0[k] <= hi:
                return None
            continue
        t1, t2 = (lo - p0[k]) / d[k], (hi - p0[k]) / d[k]
        tmin, tmax = max(tmin, min(t1, t2)), min(tmax, max(t1, t2))
    return (tmin, tmax) if tmin < tmax else None


def make_directions(base_id, base_name, mode, headway, idx_list, loop, segs, builder):
    """Create the two directed Route objects (out / in) from one stop sequence."""
    n = len(idx_list)
    sid = lambda k: builder.stops[k].id
    if loop:
        out_stops = [sid(idx_list[k]) for k in range(n)]
        out_ride = [segs[n - 1]] + segs[: n - 1]
        in_stops = [sid(idx_list[(-k) % n]) for k in range(n)]
        in_ride = [segs[(-k) % n] for k in range(n)]
    else:
        out_stops = [sid(k) for k in idx_list]
        out_ride = [0.0] + segs
        in_stops = list(reversed(out_stops))
        in_ride = [0.0] + list(reversed(segs))
    return [
        Route(f"{base_id}_out", f"{base_name} (out)", mode, headway, loop, out_stops, out_ride),
        Route(f"{base_id}_in", f"{base_name} (in)", mode, headway, loop, in_stops, in_ride),
    ]


def generate(seed: int, n_target: int):
    rng = random.Random(seed)
    size = round(12.0 * math.sqrt(n_target / 80.0), 2)
    cx = cy = size / 2
    b = CityBuilder(rng, size)
    routes: list[Route] = []

    # ------------------------------------------------------------------ metro
    n_metro = 2 if n_target < 150 else 3 if n_target < 400 else 4
    lines = []
    for k in range(n_metro):
        theta = math.pi * k / n_metro + rng.uniform(-0.25, 0.25) + 0.15
        d = (math.cos(theta), math.sin(theta))
        perp = (-d[1], d[0])
        off = rng.uniform(-0.08, 0.08) * size
        p0 = (cx + perp[0] * off, cy + perp[1] * off)
        tmin, tmax = clip_range(p0, d, MARGIN, size - MARGIN)
        ts, t = [], tmin + rng.uniform(0, 0.2 * METRO_SPACING)
        while t <= tmax:
            ts.append(t)
            t += METRO_SPACING * rng.uniform(0.85, 1.15)
        entries = [{"t": t, "lat": rng.uniform(-0.1, 0.1), "shared": None} for t in ts]
        lines.append({"p0": p0, "d": d, "perp": perp, "entries": entries, "range": (tmin, tmax)})

    # shared interchange station where two lines cross
    for i in range(n_metro):
        for j in range(i + 1, n_metro):
            a, bb = lines[i], lines[j]
            det = -a["d"][0] * bb["d"][1] + bb["d"][0] * a["d"][1]
            if abs(det) < 1e-6:
                continue
            rx, ry = bb["p0"][0] - a["p0"][0], bb["p0"][1] - a["p0"][1]
            ti = (rx * (-bb["d"][1]) - (-bb["d"][0]) * ry) / det
            tj = (a["d"][0] * ry - a["d"][1] * rx) / det
            pos = (a["p0"][0] + ti * a["d"][0], a["p0"][1] + ti * a["d"][1])
            if not (MARGIN <= pos[0] <= size - MARGIN and MARGIN <= pos[1] <= size - MARGIN):
                continue
            for line, t in ((a, ti), (bb, tj)):
                line["entries"] = [e for e in line["entries"] if abs(e["t"] - t) > 0.6]
                line["entries"].append({"t": t, "lat": 0.0, "shared": pos})
                line["entries"].sort(key=lambda e: e["t"])

    metro_stop_idx = set()
    for k, line in enumerate(lines):
        idxs = []
        for e in line["entries"]:
            if e["shared"] is not None:
                p = e["shared"]
            else:
                p = (line["p0"][0] + e["t"] * line["d"][0] + e["lat"] * line["perp"][0],
                     line["p0"][1] + e["t"] * line["d"][1] + e["lat"] * line["perp"][1])
            i = b.get_or_make(p, "metro", 0.05)
            if not idxs or idxs[-1] != i:
                idxs.append(i)
        metro_stop_idx.update(idxs)
        segs = [round(b.d(idxs[m], idxs[m + 1]) * 60.0 / METRO_CRUISE_KMH + METRO_DWELL_MIN, 2)
                for m in range(len(idxs) - 1)]
        routes += make_directions(f"M{k + 1}", f"Metro Line {k + 1}", "metro", float(rng.choice([4, 5, 6])),
                                  idxs, False, segs, b)

    # ------------------------------------------------------------------- bus
    def chord_points(p0, d, perp, frac_lo, frac_hi):
        tmin, tmax = clip_range(p0, d, MARGIN, size - MARGIN)
        length = tmax - tmin
        frac = rng.uniform(frac_lo, frac_hi)
        start = tmin + rng.uniform(0, (1 - frac) * length)
        end = start + frac * length
        sp = rng.uniform(*BUS_SPACING)
        pts, t = [], start
        while t <= end:
            lat = rng.uniform(-0.12, 0.12)
            pts.append((p0[0] + t * d[0] + lat * perp[0], p0[1] + t * d[1] + lat * perp[1]))
            t += sp * rng.uniform(0.9, 1.1)
        return pts

    def make_points(kind):
        if kind in ("radial", "crosstown"):
            theta = rng.uniform(0, math.pi)
            d = (math.cos(theta), math.sin(theta))
            perp = (-d[1], d[0])
            off = rng.uniform(-0.12, 0.12) * size if kind == "radial" else rng.choice([-1, 1]) * rng.uniform(0.2, 0.38) * size
            p0 = (cx + perp[0] * off, cy + perp[1] * off)
            return chord_points(p0, d, perp, 0.5, 0.75), False
        if kind == "feeder":
            station = b.stops[rng.choice(sorted(metro_stop_idx))]
            for _ in range(20):
                theta = rng.uniform(0, 2 * math.pi)
                d = (math.cos(theta), math.sin(theta))
                length = rng.uniform(0.22, 0.34) * size
                end = (station.x + d[0] * length, station.y + d[1] * length)
                if MARGIN <= end[0] <= size - MARGIN and MARGIN <= end[1] <= size - MARGIN:
                    break
            sp = rng.uniform(*BUS_SPACING)
            n = max(3, int(length / sp))
            return [(station.x + d[0] * length * k / n, station.y + d[1] * length * k / n) for k in range(n + 1)], False
        # ring
        r = rng.uniform(0.16, 0.24) * size
        sp = rng.uniform(0.95, 1.15)
        n = max(6, int(2 * math.pi * r / sp))
        a0 = rng.uniform(0, 2 * math.pi)
        return [(cx + r * math.cos(a0 + 2 * math.pi * k / n) + rng.uniform(-0.1, 0.1),
                 cy + r * math.sin(a0 + 2 * math.pi * k / n) + rng.uniform(-0.1, 0.1)) for k in range(n)], True

    kinds_cycle = ["radial", "crosstown", "feeder", "ring"]
    ring_budget = max(1, n_target // 120)
    bus_no, attempt = 0, 0
    while len(b.stops) < n_target and attempt < 200:
        kind = kinds_cycle[attempt % len(kinds_cycle)]
        attempt += 1
        if kind == "ring":
            if ring_budget <= 0:
                continue
        pts, loop = make_points(kind)
        chosen, seen_old = [], set()
        for p in pts:
            i = b.nearest(p, MERGE_R)
            if i is None:
                chosen.append(("new", p))
            elif i not in seen_old and not (chosen and chosen[-1] == ("old", i)):
                seen_old.add(i)
                chosen.append(("old", i))
        if loop and len(chosen) > 1 and chosen[0][0] == "old" and chosen[0] == chosen[-1]:
            chosen.pop()
        if len(chosen) < (4 if loop else 3):
            continue
        if sum(1 for c in chosen if c[0] == "new") < 2:
            continue                                  # adds nothing new to the network
        if len(b.stops) + sum(1 for c in chosen if c[0] == "new") > n_target + 4 and attempt < 120:
            continue                                  # would overshoot the target; try another route shape
        if kind == "ring":
            ring_budget -= 1
        idxs = [c[1] if c[0] == "old" else b.add_stop(c[1], "bus") for c in chosen]
        v = rng.uniform(*BUS_SPEED_KMH)
        n = len(idxs)
        m = n if loop else n - 1
        segs = [round(b.d(idxs[k], idxs[(k + 1) % n]) * 60.0 / v * rng.uniform(*BUS_CONGESTION) + BUS_DWELL_MIN, 2)
                for k in range(m)]
        headway = float(rng.choice({"radial": [8, 10, 12], "crosstown": [10, 12, 15],
                                    "feeder": [10, 12, 15, 20], "ring": [12, 15]}[kind]))
        bus_no += 1
        routes += make_directions(f"B{bus_no}", f"Bus {bus_no} {kind}", "bus", headway, idxs, loop, segs, b)

    # ------------------------------------------------------------- walk links
    n_st = len(b.stops)
    near = {i: [] for i in range(n_st)}
    for i in range(n_st):
        for j in range(i + 1, n_st):
            d = b.d(i, j)
            if d <= WALK_LINK_R:
                near[i].append((d, j))
                near[j].append((d, i))
    keep = set()
    for i in range(n_st):
        for d, j in sorted(near[i])[:WALK_LINK_K]:
            keep.add((min(i, j), max(i, j)))
    links = []
    for i, j in sorted(keep):
        d = b.d(i, j)
        det = round(rng.uniform(*WALK_DETOUR), 2)
        links.append((b.stops[i].id, b.stops[j].id, d, round(d * det * 60.0 / DEFAULT_CONFIG["walk_speed_kmh"], 2)))

    # ------------------------------------------------------------ connectivity
    id_to_idx = {s.id: i for i, s in enumerate(b.stops)}
    parent = list(range(n_st))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for r in routes:
        for k in range(len(r.stops) - 1):
            union(id_to_idx[r.stops[k]], id_to_idx[r.stops[k + 1]])
    for a, c, _, _ in links:
        union(id_to_idx[a], id_to_idx[c])
    repairs = []
    while len({find(i) for i in range(n_st)}) > 1:
        best = None
        for i in range(n_st):
            for j in range(i + 1, n_st):
                if find(i) != find(j):
                    d = b.d(i, j)
                    if best is None or d < best[0]:
                        best = (d, i, j)
        d, i, j = best
        if d > REPAIR_MAX_KM:
            raise RuntimeError(f"seed {seed} leaves the network disconnected (gap {d:.2f} km); try another seed")
        det = round(rng.uniform(*WALK_DETOUR), 2)
        links.append((b.stops[i].id, b.stops[j].id, d, round(d * det * 60.0 / DEFAULT_CONFIG["walk_speed_kmh"], 2)))
        repairs.append([b.stops[i].id, b.stops[j].id])
        union(i, j)

    params = {
        "city_size_km": size, "margin_km": MARGIN, "metro_lines": n_metro, "metro_spacing_km": METRO_SPACING,
        "metro_cruise_kmh": METRO_CRUISE_KMH, "metro_dwell_min": METRO_DWELL_MIN,
        "bus_spacing_km": list(BUS_SPACING), "bus_speed_kmh": list(BUS_SPEED_KMH),
        "bus_congestion": list(BUS_CONGESTION), "bus_dwell_min": BUS_DWELL_MIN, "merge_radius_km": MERGE_R,
        "walk_link_radius_km": WALK_LINK_R, "walk_link_k": WALK_LINK_K, "walk_detour": list(WALK_DETOUR),
        "speed_bounds_kmh": SPEED_BOUNDS_KMH, "repair_max_km": REPAIR_MAX_KM,
    }
    return b.stops, routes, links, params, repairs


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def write_card(path, meta):
    c = meta["counts"]
    text = f"""# Dataset card: {meta['name']}

> **SYNTHETIC DATA.** This is a fictional city generated by `generate_city.py`
> (seed {meta['seed']}). It does not describe any real place, operator or timetable.
> Never present it as real transit data.

| | |
|---|---|
| Stops | {c['stops']} ({c['metro_stops']} served by metro) |
| Routes (directed) | {c['routes']} ({c['metro_routes']} metro, {c['bus_routes']} bus) |
| Walk links | {c['walk_links']} (connectivity repairs: {len(meta['connectivity_repairs'])}) |
| City size | {meta['parameters']['city_size_km']} km x {meta['parameters']['city_size_km']} km, plain x/y coordinates |

## Files
- `stops.csv`        `stop_id, name, x_km, y_km`
- `routes.csv`       `route_id, name, mode, headway_min, loop` (one row per *direction*)
- `route_stops.csv`  `route_id, seq, stop_id, ride_min_from_prev` (for loop routes, seq 0 holds the closing segment time)
- `walk_links.csv`   `stop_a, stop_b, distance_km, walk_min` (undirected)
- `config.json`      cost model + physics (transfer penalty, wait factor, walk speed, v_max ...)
- `metadata.json`    seed, generator version, parameters, SHA-256 checksums

## How times were made up (the modelling assumptions)
- Metro: ride = distance / {METRO_CRUISE_KMH:g} km/h + {METRO_DWELL_MIN} min dwell.
- Bus: ride = distance / (route speed {BUS_SPEED_KMH[0]:g}-{BUS_SPEED_KMH[1]:g} km/h) x congestion {BUS_CONGESTION[0]}-{BUS_CONGESTION[1]} + {BUS_DWELL_MIN} min dwell.
- Walking: distance x detour {WALK_DETOUR[0]}-{WALK_DETOUR[1]} / {DEFAULT_CONFIG['walk_speed_kmh']} km/h.
- Service is frequency-based (headway only, no timetable); expected wait = headway / 2.
- Declared fastest speed anywhere (heuristic constant): v_max = {DEFAULT_CONFIG['v_max_kmh']:g} km/h.

Regenerate with: `python generate_city.py --seed {meta['seed']} --stops {meta['target_stops']}`
and compare the checksums in `metadata.json`.
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build(seed, n_target, out):
    stops, routes, links, params, repairs = generate(seed, n_target)
    write_dataset(out, stops, routes, links, dict(DEFAULT_CONFIG))
    metro_ids = {sid for r in routes if r.mode == "metro" for sid in r.stops}
    meta = {
        "name": f"synthetic_city_seed{seed}_n{n_target}",
        "SYNTHETIC": True,
        "notice": "Fictional city generated by generate_city.py. Not real transit data.",
        "generator_version": GEN_VERSION, "seed": seed, "target_stops": n_target,
        "counts": {"stops": len(stops), "metro_stops": len(metro_ids), "routes": len(routes),
                   "metro_routes": sum(r.mode == "metro" for r in routes),
                   "bus_routes": sum(r.mode == "bus" for r in routes), "walk_links": len(links)},
        "parameters": params, "connectivity_repairs": repairs,
    }
    write_card(os.path.join(out, "DATASET_CARD.md"), meta)
    meta["checksums_sha256"] = {f: sha256(os.path.join(out, f)) for f in
                                ["stops.csv", "routes.csv", "route_stops.csv", "walk_links.csv", "config.json",
                                 "DATASET_CARD.md"]}
    with open(os.path.join(out, "metadata.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stops", type=int, default=80, help="target number of stops (approximate)")
    ap.add_argument("--out", default="data/synthetic_city")
    a = ap.parse_args()
    m = build(a.seed, a.stops, a.out)
    c = m["counts"]
    print(f"wrote {a.out}: {c['stops']} stops ({c['metro_stops']} metro), {c['routes']} directed routes "
          f"({c['metro_routes']} metro / {c['bus_routes']} bus), {c['walk_links']} walk links, "
          f"{len(m['connectivity_repairs'])} connectivity repairs")
