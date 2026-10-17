# Transit Route Optimizer

**A* vs. Dijkstra for transfer-aware public transport routing**

An algorithmic route-planning system built around a synthetic public transport network. It compares A* and Dijkstra while accounting for ride time, expected waiting time, walking, and transfer penalties.

The project includes a custom state-expanded graph, heuristic validation, reproducible experiments, a FastAPI backend, and an interactive web interface.

> **Dataset:** All transport data is synthetically generated. The network does not represent a real city or transport operator.

## Overview

- **Route planning:** Find journeys between stops or map coordinates.
- **Algorithm comparison:** Compare A* and Dijkstra using journey cost, expanded states, and execution time.
- **Transfer-aware costs:** Account for expected waiting time and configurable transfer penalties.
- **Journey breakdown:** Separate ride, wait, walking, and penalty costs.
- **Interactive visualization:** Explore routes, inspect the search process, and replay search progress.
- **Penalty experiments:** Investigate how changing the transfer penalty changes the preferred route.
- **Scalability evaluation:** Benchmark networks containing approximately 80, 300, and 1,000 stops.
- **Validation:** Check heuristic properties, hand-derived test cases, and agreement between algorithms.

## Table of Contents

- [Cost Model](#cost-model)
- [Graph Model](#graph-model)
- [A* Heuristic](#a-heuristic)
- [Project Structure](#project-structure)
- [Dataset and Validation](#dataset-and-validation)
- [Experiments and Results](#experiments-and-results)
- [Web Application](#web-application)
- [API Reference](#api-reference)
- [Installation and Usage](#installation-and-usage)
- [Technical Notes](#technical-notes)
- [Limitations](#limitations)

## Cost Model

The planner minimizes a generalized journey cost:

\[
C = T_{\text{ride}} + T_{\text{wait}} + T_{\text{walk}} + P \cdot N_{\text{transfers}}
\]

Expected waiting time is estimated as half the service headway for each boarding:

\[
T_{\text{wait}} = \frac{\text{headway}}{2}
\]

The number of transfers is:

\[
N_{\text{transfers}} = \max(0,\ \text{boardings}-1)
\]

The first boarding does not incur a transfer penalty.

### Default configuration

| Parameter | Default |
|---|---:|
| Transfer penalty | 5 minutes |
| Walking speed | 4.8 km/h |
| Walking distance detour factor | 1.2 |
| Access radius | 0.6 km |
| Direct-walk distance cap | 1.5 km |
| Maximum speed used by heuristic | 40 km/h |

These values are configurable through the network configuration.

## Graph Model

A normal stop-only graph cannot fully represent this cost model because the cost of continuing a journey depends on the route the passenger arrived on.

The graph therefore uses expanded states:

| State | Meaning |
|---|---|
| `("R", route, i)` | On a vehicle that has arrived at position `i` on a route |
| `("A", stop)` | Standing at a stop after alighting |
| `("O",)` | Journey origin |
| `("D",)` | Journey destination |

### Edge types

| Edge | Cost |
|---|---|
| Ride | Segment ride time |
| Alight | 0 |
| Walk (`A → A`) | Walking-link time |
| Board (`A → R`) | Expected wait + transfer penalty + first segment ride time |
| Access (`O → R`) | Walk to stop + expected wait + first segment ride time, without a transfer penalty |
| Egress (`A → D`) | Walking time to destination |
| Direct (`O → D`) | Walking time, only when within the direct-walk cap |

A boarding edge includes the first ride segment. This prevents a passenger from boarding and immediately alighting at the same stop without travelling.

All edge costs are defined centrally in `network.py`, allowing A* and Dijkstra to operate on the same graph and cost model.

## A* Heuristic

The heuristic estimates the minimum remaining travel cost using straight-line distance:

\[
h(n) = \frac{d_{\text{euclidean}}(n,\text{destination})}{v_{\max}}
\]

A* prioritizes states using:

\[
f(n) = g(n) + h(n)
\]

where:

- \(g(n)\) is the accumulated journey cost.
- \(h(n)\) is the estimated remaining cost.
- \(f(n)\) is the estimated total cost through the state.

The heuristic is designed to be admissible and consistent because the maximum-speed bound limits movement cost per unit distance, while waiting and transfer penalties are non-negative. `validate.py` checks these properties across the generated graph rather than relying solely on the argument.

**Implementation detail:** A* and Dijkstra use the same `search()` function in `astar.py`. A* uses `h_weight=1`; Dijkstra uses `h_weight=0`. Their differences in state expansion therefore arise from the heuristic.

## Project Structure

```text
transit-route-optimizer/
├── astar.py                  # A* and Dijkstra search
├── network.py                # Data model, graph, edge costs and heuristic
├── journey.py                # Journey legs and cost breakdown
├── generate_city.py          # Seeded synthetic network generator
├── build_test_cases.py       # Hand-derived test network generator
├── validate.py               # Dataset and heuristic validation
├── check_tests.py            # Hand-derived test checks
├── oracle.py                 # Independent reference solvers
├── experiments.py            # Benchmark and penalty experiments
├── plot_city.py              # Interactive city-map generator
├── server.py                 # FastAPI application
├── service.py                # Routing and experiment data services
├── test_backend.py           # Backend tests
├── data/
│   ├── synthetic_city/
│   ├── city_300/
│   ├── city_1000/
│   └── tests/
├── results/                  # Saved experiment outputs
├── web/
│   ├── index.html
│   ├── css/
│   ├── js/
│   ├── fonts/
│   └── vendor/
├── requirements.txt
└── README.md
```

### Key modules

| Module | Responsibility |
|---|---|
| `network.py` | CSV loading and writing, state-expanded graph, edge costs, heuristic |
| `generate_city.py` | Deterministic synthetic network generation |
| `build_test_cases.py` | Generates seven hand-derived test networks containing 16 queries |
| `validate.py` | Dataset and heuristic validation, including a negative control |
| `check_tests.py` | Checks hand-derived answers using independent methods |
| `oracle.py` | Reference solvers used for validation |
| `astar.py` | A* and Dijkstra search and timing |
| `journey.py` | Journey legs and ride/wait/walk/penalty breakdown |
| `experiments.py` | Algorithm comparisons, baseline comparisons, and penalty sweeps |
| `server.py` | FastAPI request handling |
| `service.py` | Builds route, city, penalty-sweep, and experiment responses |

## Dataset and Validation

The project generates synthetic networks using a fixed random seed. The included datasets contain approximately 80, 300, and 1,000 stops.

### Generate and validate

Run the following commands from the project root:

```bash
python generate_city.py --seed 42 --stops 80 --out data/synthetic_city
python build_test_cases.py
python validate.py --negative-control
python check_tests.py
```

The negative control deliberately tests a case that should fail, demonstrating that the validation check can detect a violation.

### Dataset review checklist

- `stops.csv`: Check selected stop coordinates against `city_map.html`.
- `routes.csv`: Confirm that routes have inbound and outbound variants and that configured headways reflect the intended service frequencies.
- `route_stops.csv`: Check segment ride times, including sequence-zero and loop-route handling.
- `walk_links.csv`: Compare walking times against the configured walking speed and distance detour factor.
- `data/tests/*.json`: Inspect hand-derived query costs in each test's `derivation` field.

## Experiments and Results

Run the complete experiment suite with:

```bash
python experiments.py
```

A quick smoke test is available with:

```bash
python experiments.py --quick
```

The experiment suite uses seed 42 and networks with 80, 303, and 1,003 stops. It evaluates fixed random stop pairs across short, medium, and long trip-distance groups, using a default transfer penalty of five minutes.

The run checks that:

- A* and Dijkstra return identical optimal costs.
- Journey breakdown components sum to the reported journey cost.
- Verified route-change thresholds are reproduced by A*.

### Experiment outputs

| File | Contents |
|---|---|
| `queries.csv` | Per-query algorithm results, costs, transfers, breakdowns, states expanded and generated, and execution time |
| `summary_algorithms.csv` | Per-network and distance-group algorithm summaries |
| `summary_comparison.csv` | A*/Dijkstra ratios and comparison metrics |
| `penalty_sweep_summary.csv` | Aggregate results for transfer penalties from 0 to 30 |
| `penalty_sweep_pairs.csv` | Per-trip results across the penalty sweep |
| `penalty_flips.csv` | 80-stop trips whose preferred route changes to use fewer transfers |
| `baseline_queries.csv` | Shortest-distance baseline evaluated under the journey cost model |
| `baseline_summary.csv` | Summary of baseline results |
| `networks.csv` | Network sizes and metadata |
| `experiment_meta.json` | Seeds, checksums, definitions, and timing information |

Cost, transfer count, state expansion, generated-state count, and breakdown values are deterministic for identical seeds. Runtime varies by machine and run, so ratios are more informative than isolated timing measurements.

### Headline results

Median values per trip from the included experiment outputs:

| Network | A* / Dijkstra states expanded | A* / Dijkstra runtime | Heuristic tightness (`h/h*`) |
|---|---:|---:|---:|
| 80 stops | 0.76 | 1.15 — A* slower | 0.14 |
| 303 stops | 0.62 | 0.96 | 0.18 |
| 1,003 stops | 0.54 | 0.79 — A* faster | 0.27 |

Across 800 trips, the reported results show **zero cost mismatches** between A* and Dijkstra.

A* expands fewer states in all three reported network sizes. However, fewer expansions do not automatically mean lower runtime: on the smallest network, heuristic computation outweighs the reduction in explored states. The larger networks show a more favourable runtime comparison.

These results describe the included synthetic networks and experimental setup, not real-world transport performance.

## Web Application

The project includes an interactive frontend served by FastAPI.

### Features

- Journey planner for stop IDs and map coordinates.
- Route visualization with route lines and stops.
- A* versus Dijkstra comparison.
- Ride, wait, walking, and transfer-penalty breakdown.
- Configurable transfer penalty and penalty-sweep analysis.
- Search replay and route inspection.
- Experiment dashboard driven by the saved CSV results.
- How-it-works view explaining the graph and search model.

### Architecture

```text
Browser
   │
   ▼
FastAPI (server.py)
   │
   ▼
Service layer (service.py)
   │
   ├── astar.py       Search algorithms
   ├── journey.py     Journey reconstruction and cost breakdown
   └── network.py     Graph, costs and heuristic

Experiment dashboard
   │
   ▼
results/*.csv
```

The browser requests routes from the backend; it does not independently calculate the route. Experiment charts are populated from the saved results through the experiment API.

## API Reference

| Endpoint | Purpose |
|---|---|
| `GET /api/city` | Returns stops, route lines, walking links, and cost settings |
| `GET /api/route?origin=&dest=&algo=astar\|dijkstra&penalty=&replay=0\|1` | Returns route legs, cost breakdown, transfers, search metrics, and optional search trace |
| `GET /api/sweep?origin=&dest=` | Evaluates the journey at transfer penalties from 0 to 30 |
| `GET /api/flips` | Returns trips whose preferred route changes as the transfer penalty changes |
| `GET /api/experiments` | Returns data for the experiment dashboard |

Origins and destinations can be stop IDs such as `S037` or map coordinates expressed as `x,y` in kilometres. Coordinate-based journeys walk to a stop within the configured access radius.

## Installation and Usage

### Requirements

- Python 3
- `pip`

### 1. Clone the repository

```bash
git clone https://github.com/anushkaaasrivastav/transit-route-optimizer.git
cd transit-route-optimizer
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Start the application

```bash
python server.py --open
```

The application is served at:

`http://127.0.0.1:8000`

If the browser does not open automatically, navigate to that address manually.

### 5. Run the checks

```bash
python check_tests.py
python validate.py --negative-control
python -m pytest test_backend.py -q
```

To regenerate the experiment results:

```bash
python experiments.py
```

The existing experiment CSVs are included in the repository, so regenerating them is not required just to launch the website.

## Technical Notes

- **Shared search implementation:** `search(h_weight=1)` runs A*; `search(h_weight=0)` runs Dijkstra.
- **State-expanded graph:** Route-aware states represent the effect of arrival route on subsequent boarding and transfer costs.
- **Heuristic validation:** Straight-line distance divided by the configured maximum speed is checked for admissibility and consistency.
- **Independent checks:** Hand-derived test cases and reference solvers provide checks beyond comparing the two main algorithms.
- **Shortest-distance baseline:** Demonstrates why minimizing distance alone may not minimize the generalized journey cost.
- **Penalty sensitivity:** Increasing the transfer penalty can change which journey is optimal, even when the underlying network remains unchanged.

## Limitations

- The network is synthetic and does not represent a real city or operator.
- Waiting time is estimated from service frequency; exact timetables, live delays, and vehicle positions are not modelled.
- Some areas have limited or no service because of the corridor-based network generator.
- The heuristic is relatively loose on smaller networks.
- Runtime measurements depend on the machine, network structure, and query distribution.
- The results should not be generalized to real-world transit networks without further testing.

---

**Project focus:** informed search, state-expanded graph modelling, cost-aware route planning, algorithm validation, and empirical performance evaluation.
