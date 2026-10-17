"""
plot_city.py -- draw a dataset as an interactive Plotly map (plain x/y km, no tiles).

    python plot_city.py [dataset_dir] [out.html]

Hover a stop to see its id, name and which routes serve it. Click legend entries
to hide/show routes. Interchanges (stops served by 2+ lines) are drawn larger.
The title carries the SYNTHETIC label so a screenshot can never pass as real data.
"""
import sys
from collections import defaultdict

import plotly.graph_objects as go

from network import read_raw

METRO_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd"]
BUS_COLORS = ["#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#ff7f0e", "#393b79", "#637939",
              "#843c39", "#7b4173", "#3182bd", "#e6550d"]


def main(path="data/synthetic_city", out="city_map.html"):
    raw = read_raw(path)
    pos = {s["id"]: (s["x"], s["y"]) for s in raw["stops"]}
    names = {s["id"]: s["name"] for s in raw["stops"]}
    serve = defaultdict(set)
    fig = go.Figure()

    metro_i = bus_i = 0
    for r in raw["routes"]:
        base = r["id"].rsplit("_", 1)[0]
        serve_name = r["name"].rsplit(" (", 1)[0] if r["id"].endswith("_out") else None
        for s in r["stops"]:
            serve[s].add(base)
        if not r["id"].endswith("_out"):
            continue
        if r["mode"] == "metro":
            color, width = METRO_COLORS[metro_i % len(METRO_COLORS)], 5
            metro_i += 1
        else:
            color, width = BUS_COLORS[bus_i % len(BUS_COLORS)], 2.5
            bus_i += 1
        seq = r["stops"] + ([r["stops"][0]] if r["loop"] else [])
        fig.add_trace(go.Scatter(
            x=[pos[s][0] for s in seq], y=[pos[s][1] for s in seq], mode="lines",
            line=dict(color=color, width=width, dash="solid" if r["mode"] == "metro" else "dot"),
            name=f"{serve_name} (every {r['headway']:g} min)", hoverinfo="name", opacity=0.85))

    for w in raw["walk_links"]:
        fig.add_trace(go.Scatter(x=[pos[w["a"]][0], pos[w["b"]][0]], y=[pos[w["a"]][1], pos[w["b"]][1]],
                                 mode="lines", line=dict(color="black", width=3), showlegend=False,
                                 hoverinfo="text", text=f"walk {w['a']}-{w['b']}: {w['walk_min']} min"))

    ids = list(pos)
    fig.add_trace(go.Scatter(
        x=[pos[s][0] for s in ids], y=[pos[s][1] for s in ids], mode="markers",
        marker=dict(size=[13 if len(serve[s]) > 1 else 7 for s in ids],
                    color=["white" if len(serve[s]) > 1 else "#333" for s in ids],
                    line=dict(color="#111", width=1.5)),
        text=[f"<b>{s}</b> {names[s]}<br>routes: {', '.join(sorted(serve[s]))}" for s in ids],
        hoverinfo="text", name="stops (large = interchange)"))

    meta = raw["metadata"]
    fig.update_layout(
        title=f"SYNTHETIC fictional city (seed {meta.get('seed')}) - not real transit data",
        xaxis=dict(title="x (km)", scaleanchor="y", scaleratio=1), yaxis=dict(title="y (km)"),
        plot_bgcolor="#fafafa", height=820, legend=dict(font=dict(size=11)))
    fig.write_html(out, include_plotlyjs="cdn")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
