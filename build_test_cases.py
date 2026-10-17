"""
build_test_cases.py -- write the small hand-checkable test networks to data/tests/*.json

Every expected cost below was derived BY HAND (the arithmetic is written out in
each query's "derivation" field). check_tests.py then confirms them two other
ways: an exhaustive enumeration of journeys, and the graph + Dijkstra.

Cost model recap (default config: walk 4.8 km/h, detour 1.2, wait = headway/2):
    cost = access walk + wait + ride  [+ walk + wait + penalty + ride per transfer] + egress walk
    walking d km takes d * 1.2 * 60 / 4.8 = 15 * d minutes
"""
import json
import os

OUT = os.path.join("data", "tests")


def stop(i, x, y):
    return {"id": i, "name": i, "x": x, "y": y}


def route(i, mode, headway, stops, ride, loop=False):
    return {"id": i, "name": i, "mode": mode, "headway": headway, "loop": loop, "stops": stops, "ride": ride}


def q(label, origin, dest, p, cost, transfers=None, derivation="", transfers_any=None):
    return {"label": label, "origin": origin, "dest": dest, "transfer_penalty": p, "expected_cost": cost,
            "expected_transfers": transfers, "expected_transfers_any": transfers_any, "derivation": derivation}


CASES = []

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t1_penalty_flip_direct_vs_transfer",
    "description": "Direct slow bus X vs metro Y + bus Z with a transfer at M. The preferred route flips at p = 7.",
    "network": {
        "stops": [stop("O", 0, 0), stop("M", 4.8, 0), stop("D", 9.3, 0)],
        "routes": [route("X", "bus", 10, ["O", "D"], [0, 30]),
                   route("Y", "metro", 6, ["O", "M"], [0, 8]),
                   route("Z", "bus", 10, ["M", "D"], [0, 12])],
        "walk_links": [],
    },
    "queries": [
        q("p=0", [0, 0], [9.3, 0], 0, 28.0, 1,
          "X = 5 + 30 = 35.  Y+Z = 3 + 8 + (5 + p) + 12 = 28 + p = 28.  Y+Z wins."),
        q("p=5 (default)", [0, 0], [9.3, 0], 5, 33.0, 1,
          "X = 35.  Y+Z = 28 + 5 = 33.  Y+Z wins (1 transfer)."),
        q("p=10", [0, 0], [9.3, 0], 10, 35.0, 0,
          "X = 35.  Y+Z = 28 + 10 = 38.  Direct X wins (0 transfers)."),
        q("p=7 (exact tie)", [0, 0], [9.3, 0], 7, 35.0, None,
          "X = 35.  Y+Z = 28 + 7 = 35.  Tie: cost must be 35 whichever route is returned.",
          transfers_any=[0, 1]),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t2_walking_beats_waiting",
    "description": "Destination is 1.2 km away. A 30-min-headway bus is slower than just walking.",
    "network": {
        "stops": [stop("O", 0, 0), stop("D", 1.2, 0)],
        "routes": [route("B", "bus", 30, ["O", "D"], [0, 4])],
        "walk_links": [],
    },
    "queries": [
        q("p=5", [0, 0], [1.2, 0], 5, 18.0, 0,
          "Bus = 15 (wait) + 4 = 19.  Walk = 1.2 km * 1.2 detour * 60 / 4.8 = 18.0.  Walking wins; no vehicle boarded."),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t3_one_way_direction",
    "description": "Route P only runs A->D. Going D->A must use the slow route Q; going A->D uses P. Catches bidirectional-edge bugs.",
    "network": {
        "stops": [stop("A", 0, 0), stop("B", 3, 0), stop("C", 6, 0), stop("D", 9, 0), stop("E", 4.5, 4)],
        "routes": [route("P", "metro", 10, ["A", "B", "C", "D"], [0, 5, 5, 5]),
                   route("Q", "bus", 12, ["D", "E", "A"], [0, 20, 20])],
        "walk_links": [],
    },
    "queries": [
        q("A -> D", [0, 0], [9, 0], 5, 20.0, 0, "P: wait 5 + (5+5+5) = 20."),
        q("D -> A", [9, 0], [0, 0], 5, 46.0, 0,
          "P cannot be ridden backwards.  Q: wait 6 + 20 + 20 = 46.  (A bidirectional bug would wrongly give 20.)"),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t4_no_path",
    "description": "Two disconnected pieces far apart. The search must report 'no route' (cost null).",
    "network": {
        "stops": [stop("A", 0, 0), stop("B", 3, 0), stop("C", 12, 0), stop("D", 15, 0)],
        "routes": [route("R1", "bus", 10, ["A", "B"], [0, 6]), route("R2", "bus", 10, ["C", "D"], [0, 6])],
        "walk_links": [],
    },
    "queries": [
        q("A -> D", [0, 0], [15, 0], 5, None, None, "No ride, walk link or short direct walk connects the two pieces."),
        q("A -> B", [0, 0], [3, 0], 5, 11.0, 0, "R1: wait 5 + 6 = 11 (sanity check that the first piece works)."),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t5_walk_transfer_flip",
    "description": "Transfer between two DIFFERENT stops via a walk link, vs a slow direct bus. Flips between p=5 and p=10.",
    "network": {
        "stops": [stop("O", 0, 0), stop("M1", 5, 0), stop("M2", 5.3, 0), stop("D", 10.3, 0)],
        "routes": [route("R1", "bus", 8, ["O", "M1"], [0, 10]),
                   route("R2", "bus", 12, ["M2", "D"], [0, 10]),
                   route("R3", "bus", 20, ["O", "D"], [0, 30])],
        "walk_links": [{"a": "M1", "b": "M2", "walk_min": 4.0}],
    },
    "queries": [
        q("p=5", [0, 0], [10.3, 0], 5, 39.0, 1,
          "R1+walk+R2 = 4 (wait) + 10 + 4 (walk) + (6 (wait) + 5 (penalty)) + 10 = 39.  R3 = 10 + 30 = 40.  Transfer route wins."),
        q("p=10", [0, 0], [10.3, 0], 10, 40.0, 0,
          "R1+walk+R2 = 34 + 10 = 44.  R3 = 40.  Direct R3 wins."),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t6_first_boarding_free_and_access_egress",
    "description": "One route only. Origin/dest are POINTS near the stops. The penalty must NOT be charged for the first boarding.",
    "network": {
        "stops": [stop("O", 0, 0), stop("D", 5, 0)],
        "routes": [route("R", "bus", 12, ["O", "D"], [0, 14])],
        "walk_links": [],
    },
    "queries": [
        q("p=0", [-0.4, 0], [5.3, 0], 0, 30.5, 0,
          "Access walk 0.4 km = 6.0.  Wait 6.  Ride 14.  Egress walk 0.3 km = 4.5.  Total 30.5."),
        q("p=5", [-0.4, 0], [5.3, 0], 5, 30.5, 0, "Same journey, same 30.5: the first boarding is not a transfer."),
        q("p=100", [-0.4, 0], [5.3, 0], 100, 30.5, 0, "Even a huge penalty changes nothing: 0 transfers."),
    ],
})

# ---------------------------------------------------------------------------
CASES.append({
    "name": "t7_loop_route_seam",
    "description": "Loop route P->Q->R->P... Riding R->P->Q crosses the 'seam' (last stop back to first) without a transfer.",
    "network": {
        "stops": [stop("P", 0, 0), stop("Q", 4, 0), stop("R", 2, 3.5)],
        "routes": [route("L", "bus", 10, ["P", "Q", "R"], [7, 7, 7], loop=True)],
        "walk_links": [],
    },
    "queries": [
        q("R -> Q (crosses the seam)", [2, 3.5], [4, 0], 5, 19.0, 0,
          "Wait 5 + ride R->P 7 + ride P->Q 7 = 19.  No transfer: the vehicle just keeps going."),
        q("R -> P", [2, 3.5], [0, 0], 5, 12.0, 0, "Wait 5 + ride R->P 7 = 12."),
    ],
})


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for c in CASES:
        c = {**c, "config": {}}
        with open(os.path.join(OUT, c["name"] + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(c, f, indent=2)
            f.write("\n")
    print(f"wrote {len(CASES)} test networks to {OUT}/ ({sum(len(c['queries']) for c in CASES)} queries)")
