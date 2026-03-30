"""
transit.py — ETS real-time transit fetcher for HomeStation Pi 4

Fetches:
  - Next arrivals for bus routes 4 and 8 at their nearest stops
  - Next LRT trains at Health Sciences / Jubilee Station (both Capital + Metro lines)

Data source: Edmonton's public GTFS-RT feeds (no API key needed)
  TripUpdates: https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/TripUpdate/TripUpdates.pb
  Static GTFS:  https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/gtfs.zip

Dependencies:
  pip install gtfs-realtime-bindings requests

HOW THE STOP IDs WORK
---------------------
ETS stop IDs in the static GTFS match the 4-5 digit numbers on physical stop signs.
The script discovers them once from the static GTFS zip and caches them.

JUBILEE LRT STOP IDs (confirmed from static GTFS):
  Both Capital Line and Metro Line serve Health Sciences/Jubilee.
  The stop_id values are resolved at startup from the GTFS stops.txt.
  The stop name to search: "Health Sciences/Jubilee" (case-insensitive substring)

BUS STOPS FOR ROUTES 4 & 8:
  You need to decide which direction/stop to show — edit BUS_STOPS below.
  Default: southbound stops near downtown that most people care about.
  Find stop numbers on the physical signs, or from https://www.transsee.ca/routelist?a=edmonton
"""

import io
import time
import logging
import zipfile
import requests
from datetime import datetime, timezone
from google.transit import gtfs_realtime_pb2

log = logging.getLogger("homestation.transit")

# ─────────────────────────────────────────────
# CONFIG — edit stop numbers to match your actual stops
# ─────────────────────────────────────────────

# Stop IDs (numbers on the physical signs) for bus arrivals.
# These are the stops you want to monitor — change to your nearest stops.
# Route 4 (Capilano - Westmount): southbound stop near 109 St & Jasper
# Route 8 (Abbottsfield - Westmount): downtown stops
BUS_STOPS = {
    "4": ["2686", "2689"],
    "8": ["2686", "2689"],
}
BUS_DIRECTIONS = {
    "004": {
        "south": {"keywords": ["capilano", "university"], "label": "4>Cap"},
        "north": {"keywords": ["lewis farms", "westmount"], "label": "4>Lewis"},
    },
    "008": {
        "east":  {"keywords": ["abbottsfield", "coliseum"], "label": "8>Abbot"},
        "west":  {"keywords": ["university", "west"],       "label": "8>Univ"},
    },
}
LRT_NORTH_KEYWORDS = ["nait", "clareview"]
LRT_SOUTH_KEYWORDS = ["century park", "south campus"]

# Jubilee LRT stop name substring to match in stops.txt
JUBILEE_STOP_NAME = "jubilee"

# GTFS-RT endpoints
TRIP_UPDATES_URL = "https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/TripUpdate/TripUpdates.pb"
STATIC_GTFS_URL  = "https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/gtfs.zip"

# How many upcoming arrivals to show per category
MAX_BUS_ARRIVALS = 3
MAX_LRT_ARRIVALS = 4

# Cache the static GTFS for this many seconds before re-downloading
STATIC_CACHE_TTL = 3600  # 1 hour

# ─────────────────────────────────────────────
# STATIC GTFS CACHE
# ─────────────────────────────────────────────

_static_cache = {
    "loaded_at":    0,
    "stop_id_map":  {},   # stop_code → stop_id (they're usually the same for ETS)
    "jubilee_ids":  set(),
    "route_ids":    {},   # short_name → route_id
    "trips":        {},   # trip_id → route_id
    "headsigns":    {},   # trip_id → headsign
}
def _classify_direction(headsign: str, direction_cfg: dict) -> str | None:
    """Return direction key if headsign matches, else None."""
    h = headsign.lower()
    for dir_key, cfg in direction_cfg.items():
        if any(kw in h for kw in cfg["keywords"]):
            return dir_key
    return None
def _load_static_gtfs():
    """Download and parse static GTFS zip into lookup tables."""
    now = time.time()
    if now - _static_cache["loaded_at"] < STATIC_CACHE_TTL:
        return  # already fresh

    log.info("[transit] Downloading static GTFS...")
    try:
        r = requests.get(STATIC_GTFS_URL, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.error(f"[transit] Failed to download static GTFS: {e}")
        return

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        # stops.txt → find Jubilee stop IDs
        with zf.open("stops.txt") as f:
            lines  = f.read().decode("utf-8").splitlines()
            header = lines[0].split(",")
            id_col   = header.index("stop_id")
            code_col = header.index("stop_code") if "stop_code" in header else id_col
            name_col = header.index("stop_name")
            jubilee_ids = set()
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) <= max(id_col, name_col):
                    continue
                sid   = parts[id_col].strip().strip('"')
                scode = parts[code_col].strip().strip('"')
                sname = parts[name_col].strip().strip('"').lower()
                _static_cache["stop_id_map"][scode] = sid
                if JUBILEE_STOP_NAME in sname:
                    jubilee_ids.add(sid)
            _static_cache["jubilee_ids"] = jubilee_ids
            log.info(f"[transit] Jubilee stop IDs: {jubilee_ids}")

        # routes.txt → route short_name → route_id
        with zf.open("routes.txt") as f:
            lines  = f.read().decode("utf-8").splitlines()
            header = lines[0].split(",")
            rid_col   = header.index("route_id")
            rname_col = header.index("route_short_name")
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) <= max(rid_col, rname_col):
                    continue
                rid   = parts[rid_col].strip().strip('"')
                rname = parts[rname_col].strip().strip('"')
                _static_cache["route_ids"][rname] = rid

        # trips.txt → trip_id → route_id + headsign
        with zf.open("trips.txt") as f:
            lines  = f.read().decode("utf-8").splitlines()
            header = lines[0].split(",")
            trip_id_col = header.index("trip_id")
            route_col   = header.index("route_id")
            head_col    = header.index("trip_headsign") if "trip_headsign" in header else -1
            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) <= max(trip_id_col, route_col):
                    continue
                tid = parts[trip_id_col].strip().strip('"')
                rid = parts[route_col].strip().strip('"')
                _static_cache["trips"][tid] = rid
                if head_col >= 0 and len(parts) > head_col:
                    _static_cache["headsigns"][tid] = parts[head_col].strip().strip('"')

    _static_cache["loaded_at"] = now
    log.info("[transit] Static GTFS loaded")

# ─────────────────────────────────────────────
# GTFS-RT FETCHER
# ─────────────────────────────────────────────

def _fetch_trip_updates():
    """Fetch and parse the GTFS-RT TripUpdates protobuf."""
    try:
        r = requests.get(TRIP_UPDATES_URL, timeout=10)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        return feed
    except Exception as e:
        log.error(f"[transit] TripUpdates fetch failed: {e}")
        return None

def _now_epoch():
    return int(datetime.now(timezone.utc).timestamp())

def _mins_until(epoch_time: int) -> int:
    return max(0, (epoch_time - _now_epoch()) // 60)

def _fmt_time(epoch_time: int) -> str:
    dt = datetime.fromtimestamp(epoch_time)
    return dt.strftime("%H:%M")

# ─────────────────────────────────────────────
# BUS ARRIVALS
# ─────────────────────────────────────────────

def get_bus_arrivals(feed) -> dict:
    """
    Returns dict keyed by label string ? list of {time_str, mins, headsign}
    Two directions per route, sorted by arrival time.
    """
    _load_static_gtfs()
    if feed is None:
        return {}

    # monitored stop_ids
    all_stop_ids = set()
    for sid in BUS_STOPS.get("4", []) + BUS_STOPS.get("8", []):
        real_sid = _static_cache["stop_id_map"].get(sid, sid)
        all_stop_ids.add(real_sid)

    # reverse route lookup: route_id ? short_name
    rev_routes = {v: k for k, v in _static_cache["route_ids"].items()}

    # result structure: label ? arrivals list
    results = {}
    for route_id, dir_cfg in BUS_DIRECTIONS.items():
        for dir_key, cfg in dir_cfg.items():
            results[cfg["label"]] = []

    now = _now_epoch()

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu       = entity.trip_update
        trip_id  = tu.trip.trip_id
        route_id = _static_cache["trips"].get(trip_id, "")
        headsign = _static_cache["headsigns"].get(trip_id, "")

        if route_id not in BUS_DIRECTIONS:
            continue

        dir_cfg  = BUS_DIRECTIONS[route_id]
        dir_key  = _classify_direction(headsign, dir_cfg)
        if dir_key is None:
            continue
        label = dir_cfg[dir_key]["label"]

        for stu in tu.stop_time_update:
            stop_id = str(stu.stop_id)
            if stop_id not in all_stop_ids:
                continue
            t = None
            if stu.HasField("departure") and stu.departure.time > now:
                t = stu.departure.time
            elif stu.HasField("arrival") and stu.arrival.time > now:
                t = stu.arrival.time
            if t:
                results[label].append({
                    "time_str": _fmt_time(t),
                    "mins":     _mins_until(t),
                    "headsign": headsign[:10],
                })

    for label in results:
        results[label].sort(key=lambda x: x["mins"])
        results[label] = results[label][:2]

    return results

# ─────────────────────────────────────────────
# LRT ARRIVALS AT JUBILEE
# ─────────────────────────────────────────────

def get_lrt_arrivals(feed) -> dict:
    """
    Returns {"north": [...], "south": [...]} for Jubilee station.
    North = NAIT / Clareview, South = Century Park.
    """
    _load_static_gtfs()
    if feed is None:
        return {"north": [], "south": []}

    jubilee_ids = _static_cache["jubilee_ids"]
    lrt_route_ids = {
        v for k, v in _static_cache["route_ids"].items()
        if k in ("Capital", "Metro", "Valley")
    }

    results = {"north": [], "south": []}
    now     = _now_epoch()

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu       = entity.trip_update
        trip_id  = tu.trip.trip_id
        route_id = _static_cache["trips"].get(trip_id, "")
        headsign = _static_cache["headsigns"].get(trip_id, "").lower()

        if route_id not in lrt_route_ids:
            continue

        if any(kw in headsign for kw in LRT_NORTH_KEYWORDS):
            direction = "north"
        elif any(kw in headsign for kw in LRT_SOUTH_KEYWORDS):
            direction = "south"
        else:
            continue

        for stu in tu.stop_time_update:
            stop_id = str(stu.stop_id)
            if stop_id not in jubilee_ids:
                continue
            t = None
            if stu.HasField("departure") and stu.departure.time > now:
                t = stu.departure.time
            elif stu.HasField("arrival") and stu.arrival.time > now:
                t = stu.arrival.time
            if t:
                results[direction].append({
                    "time_str": _fmt_time(t),
                    "mins":     _mins_until(t),
                    "headsign": _static_cache["headsigns"].get(trip_id, "")[:10],
                })

    for d in results:
        results[d].sort(key=lambda x: x["mins"])
        results[d] = results[d][:2]

    return results


def fetch_transit_data() -> dict:
    feed  = _fetch_trip_updates()
    buses = get_bus_arrivals(feed)
    lrt   = get_lrt_arrivals(feed)
    return {
        "buses":   buses,   # {"4?Capilano": [...], "4?Lewis Fm": [...], ...}
        "lrt":     lrt,     # {"north": [...], "south": [...]}
        "updated": datetime.now().strftime("%H:%M"),
    }
# ─────────────────────────────────────────────
# MAIN ENTRY POINT (called from main.py)
# ─────────────────────────────────────────────



if __name__ == "__main__":
    # Quick CLI test
    logging.basicConfig(level=logging.INFO)
    data = fetch_transit_data()
    print("\n=== BUS ARRIVALS ===")
    for route, arr in data["buses"].items():
        print(f"Route {route}:")
        for a in arr:
            print(f"  {a['time_str']} ({a['mins']}min) → {a['headsign']}")
    print("\n=== LRT AT JUBILEE ===")
    for a in data["lrt"]:
        print(f"  {a['time_str']} ({a['mins']}min) {a['route_name']} → {a['headsign']}")
    print(f"\nUpdated: {data['updated']}")