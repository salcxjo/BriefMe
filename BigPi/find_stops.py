import requests, zipfile, io

r = requests.get("https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/gtfs.zip")
with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
    # trips.txt: find all trip_ids for route 004 and 008
    trips = {}
    for line in zf.open("trips.txt").read().decode().splitlines()[1:]:
        p = line.split(",")
        route_id = p[0].strip('"').strip()
        trip_id  = p[2].strip('"').strip()
        if route_id in ("004", "008"):
            trips[trip_id] = route_id

    print(f"Found {len(trips)} trips for routes 4 and 8")

    # stop_times.txt: collect stop_ids used by those trips
    stops = {"004": set(), "008": set()}
    for line in zf.open("stop_times.txt").read().decode().splitlines()[1:]:
        p = line.split(",")
        tid = p[0].strip('"').strip()
        sid = p[3].strip('"').strip()
        if tid in trips:
            stops[trips[tid]].add(sid)

    # stops.txt: get stop names
    stop_info = {}
    for line in zf.open("stops.txt").read().decode().splitlines()[1:]:
        p = line.split(",")
        sid   = p[0].strip('"').strip()
        sname = p[2].strip('"').strip()
        stop_info[sid] = sname

    for route, sids in stops.items():
        print(f"\n=== Route {route} ({len(sids)} stops) ===")
        for sid in sorted(sids):
            print(f"  {sid}: {stop_info.get(sid, '?')}")