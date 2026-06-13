#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Télécharge le fond de carte (rivières, plans d'eau, littoral, parcs, routes,
et — pour les petits villages — bâtiments et sentiers) de chaque ville depuis
OpenStreetMap (API Overpass) et génère citymap-data.js, lu par index.html pour
dessiner les cartes de ville « façon croquis », comme la carte nationale.

    python3 download_citymaps.py

Aucune dépendance : uniquement la bibliothèque standard de Python 3.
Données © contributeurs OpenStreetMap (ODbL).
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, "citymap-data.js")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "GeorgieTripMap/1.0 (carte de voyage perso, usage non commercial)"
RDP_TOL = 0.00022          # simplification des lignes (~22 m)
BAT_TOL = 0.00004          # simplification fine des bâtiments (~4 m, garde la forme)
BAT_MIN = 0.00013          # diagonale mini d'un bâtiment retenu (~14 m : on jette les cabanes)
MAX_BATIMENTS = 3000

# bbox (sud, ouest, nord, est) + niveau : 'city' = grandes villes (axes majeurs),
# 'village' = bourgs de montagne (bâtiments + tout le réseau + sentiers)
VILLES = {
    "tbilissi": {"bbox": (41.66, 44.74, 41.74, 44.84), "level": "city"},
    "mtskheta": {"bbox": (41.81, 44.68, 41.87, 44.75), "level": "village"},
    "ananuri":  {"bbox": (42.10, 44.66, 42.20, 44.74), "level": "village"},
    "kazbegi":  {"bbox": (42.61, 44.55, 42.70, 44.70), "level": "village"},
    "borjomi":  {"bbox": (41.80, 43.33, 41.88, 43.45), "level": "village"},
    "kutaisi":  {"bbox": (42.22, 42.65, 42.32, 42.75), "level": "city"},
    "batumi":   {"bbox": (41.59, 41.58, 41.70, 41.68), "level": "city"},
}


def overpass_query(bbox, level):
    s, w, n, e = bbox
    b = "%f,%f,%f,%f" % (s, w, n, e)
    q = ["[out:json][timeout:120];("]
    q.append('way["waterway"~"^(river|canal)$"](%s);' % b)
    q.append('way["natural"="water"](%s);' % b)
    q.append('relation["natural"="water"](%s);' % b)
    q.append('way["natural"="coastline"](%s);' % b)
    q.append('way["leisure"="park"](%s);' % b)
    q.append('way["landuse"~"^(forest|grass|meadow|recreation_ground|cemetery)$"](%s);' % b)
    if level == "city":
        q.append('way["highway"~"^(motorway|trunk|primary|secondary)$"](%s);' % b)
    else:
        q.append('way["natural"="glacier"](%s);' % b)
        q.append('relation["natural"="glacier"](%s);' % b)
        q.append('way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|track)$"](%s);' % b)
        q.append('way["highway"~"^(path|footway|pedestrian|steps)$"](%s);' % b)
        q.append('way["building"](%s);' % b)
    q.append(");out geom;")
    return "".join(q)


def fetch(query):
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last = None
    for ep in ENDPOINTS:
        delay = 3.0
        for attempt in range(4):
            try:
                req = urllib.request.Request(ep, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=150) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as ex:
                last = ex
                if ex.code in (429, 504, 503) and attempt < 3:
                    print("    … HTTP %d — nouvelle tentative dans %.0fs" % (ex.code, delay))
                    time.sleep(delay); delay = min(delay * 2, 40); continue
                break
            except Exception as ex:
                last = ex; time.sleep(delay); delay = min(delay * 2, 40)
        print("    ! endpoint %s KO (%s) — essai suivant" % (ep.split("/")[2], last))
    raise RuntimeError("Overpass injoignable : %s" % last)


def rdp(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[1] - a[1], b[0] - a[0]
    nrm = (dx * dx + dy * dy) ** 0.5 or 1e-9
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        d = abs((p[1] - a[1]) * dy - (p[0] - a[0]) * dx) / nrm
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        return rdp(pts[:idx + 1], tol)[:-1] + rdp(pts[idx:], tol)
    return [a, b]


def geom_to_line(geom, tol=RDP_TOL):
    pts = [[round(g["lat"], 5), round(g["lon"], 5)] for g in geom if "lat" in g and "lon" in g]
    return rdp(pts, tol) if len(pts) >= 2 else None


def bbox_diag(line):
    xs = [p[1] for p in line]
    ys = [p[0] for p in line]
    return ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5


def classify(el, layers):
    tags = el.get("tags", {})
    if el["type"] == "node":
        return
    geoms = []
    if el["type"] == "way" and "geometry" in el:
        geoms = [el["geometry"]]
    elif el["type"] == "relation":
        geoms = [m["geometry"] for m in el.get("members", [])
                 if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry")]
    hw = tags.get("highway")
    for gm in geoms:
        if tags.get("building") is not None:
            line = geom_to_line(gm, BAT_TOL)
            if line and len(line) >= 3 and bbox_diag(line) >= BAT_MIN:
                layers["buildings"].append(line)
            continue
        line = geom_to_line(gm)
        if not line:
            continue
        if tags.get("natural") == "coastline":
            layers["coast"].append(line)
        elif tags.get("natural") == "glacier":
            layers["glacier"].append(line)
        elif tags.get("natural") == "water":
            layers["water"].append(line)
        elif tags.get("waterway") in ("river", "canal"):
            layers["rivers"].append(line)
        elif hw in ("path", "footway", "pedestrian", "steps"):
            layers["paths"].append(line)
        elif hw:
            layers["roads"].append(line)
        elif tags.get("leisure") == "park" or tags.get("landuse"):
            layers["green"].append(line)


def main():
    out = {}
    order = ["rivers", "water", "coast", "glacier", "green", "buildings", "roads", "paths"]
    print("Fond de carte OpenStreetMap (Overpass)…\n")
    for key, cfg in VILLES.items():
        print("• %-9s (%s)" % (key, cfg["level"]))
        try:
            data = fetch(overpass_query(cfg["bbox"], cfg["level"]))
        except Exception as ex:
            print("    ! %s — ville ignorée" % ex)
            out[key] = {}
            continue
        layers = {k: [] for k in order}
        for el in data.get("elements", []):
            classify(el, layers)
        if len(layers["buildings"]) > MAX_BATIMENTS:
            layers["buildings"] = layers["buildings"][:MAX_BATIMENTS]
        layers = {k: layers[k] for k in order if layers[k]}
        print("    " + (" · ".join("%s:%d" % (k, len(v)) for k, v in layers.items()) or "rien"))
        out[key] = layers
        time.sleep(2.0)

    with open(SORTIE, "w", encoding="utf-8") as fh:
        fh.write("/* Fond de carte des villes — © contributeurs OpenStreetMap (ODbL). */\n")
        fh.write("/* Généré par download_citymaps.py. Coordonnées [lat, lng]. */\n")
        fh.write("window.CITYMAPS = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print("\nÉcrit : %s (%.0f Ko)" % (os.path.relpath(SORTIE, ICI), os.path.getsize(SORTIE) / 1024))


if __name__ == "__main__":
    main()
