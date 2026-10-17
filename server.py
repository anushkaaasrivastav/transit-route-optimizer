"""
server.py -- the website's web server (FastAPI). It is deliberately thin.

    python server.py                 # then open http://127.0.0.1:8000
    python server.py --port 8080 --open

Architecture (each layer only talks to the one below it):

    browser (web/)  ->  server.py (HTTP, validation)  ->  service.py (builds the JSON)
                                                      ->  astar.py / journey.py / network.py
                                                          (the algorithms: the source of truth)

This file contains no routing logic and no numbers of its own. It checks the request, calls a function
in service.py, and returns the result as JSON. Experiment charts are read from results/*.csv by service.py.

Endpoints
    GET /api/health
    GET /api/city                      stops, lines (with colours), walking links, cost-model settings
    GET /api/route?origin=&dest=&algo=&penalty=&replay=
                                       best route with the real A* / Dijkstra, breakdown, legs, statistics
    GET /api/sweep?origin=&dest=       the same trip re-planned at penalties 0..30 (A*)
    GET /api/flips?limit=              real trips whose best route changes with the penalty (from the CSVs)
    GET /api/experiments               everything the experiments page shows (from results/*.csv)
    GET /results/<file>.csv            the raw experiment files
"""
from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import service
from service import UserError

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
RESULTS = BASE / "results"

app = FastAPI(title="Interchange route planner", version="1.0")


def guarded(fn, *args, **kwargs):
    """Turn the service layer's 'bad request' and 'no results yet' errors into clean HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except UserError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/health")
def health():
    return {"ok": True, "results_available": (RESULTS / "experiment_meta.json").exists(),
            "stops": len(service.get_city().net.stops)}


@app.get("/api/city")
def city():
    return service.city_payload()


@app.get("/api/route")
def route(origin: str, dest: str, algo: Literal["astar", "dijkstra"] = "astar",
          penalty: float = Query(5.0, ge=0, le=60), replay: int = Query(0, ge=0, le=1)):
    return guarded(service.plan, origin, dest, algo=algo, penalty=penalty, replay=bool(replay))


@app.get("/api/sweep")
def sweep(origin: str, dest: str):
    return guarded(service.sweep, origin, dest)


@app.get("/api/flips")
def flips(limit: int = Query(12, ge=1, le=50)):
    return guarded(service.demo_flips, limit)


@app.get("/api/experiments")
def experiments():
    return guarded(service.experiments_payload)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


for folder in ("css", "js", "fonts", "vendor"):
    app.mount(f"/{folder}", StaticFiles(directory=WEB / folder), name=folder)
if RESULTS.exists():
    app.mount("/results", StaticFiles(directory=RESULTS), name="results")


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser(description="Interchange route planner website")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true", help="open the website in your browser")
    a = ap.parse_args()
    loaded = service.get_city()                     # load the city once, up front
    print(f"Loaded the {len(loaded.net.stops)}-stop synthetic city.")
    print(f"Open http://{a.host}:{a.port}   (Ctrl+C to stop)")
    if a.open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
