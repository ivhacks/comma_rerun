#!/usr/bin/env python3
"""Live version of viz.py: same three panels, streamed instead of from a .npz.

Runs under the interpreter that has rerun-sdk (3.14), not openpilot's venv:

    cd ~/openpilot && uv run python ~/comma_rerun/live_sub.py | python3 ~/comma_rerun/live_viz.py
"""
import datetime
import io
import json
import math
import sys
import urllib.request

import numpy as np
import rerun as rr
import rerun.blueprint as rrb
from PIL import Image

TIMELINE = "t"
WINDOW_SECONDS = 30.0

# Destination to mark on the map, matching the ParkingDestination param. The param
# itself is CLEAR_ON_MANAGER_START so it does not survive a reboot; set it here too.
DEST = None  # fallback if the device param is unset; live_sub streams the real one

# Esri World Imagery: web-mercator tiles, no API key unlike Mapbox or Google.
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE = 256
ZOOM = 19
SPAN = 2  # tiles either side of centre
# This car (VOLKSWAGEN_ID4_MK2) is steerControlType=curvature, so the lateral output
# lands in actuators.curvature and actuators.torque is always zero. Torque-control
# cars like the EV6 are the other way round.

# The viewer renders timestamps in UTC. Shifting by the local offset makes the axis
# read local wall-clock without touching viewer settings.
UTC_OFFSET = datetime.datetime.now().astimezone().utcoffset().total_seconds()

def world_xy(lat, lon, zoom):
  """Continuous tile coordinates; integer part is the tile, fraction the pixel in it."""
  n = 2 ** zoom
  return ((lon + 180.0) / 360.0 * n,
          (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)


def build_map(lat, lon):
  """Stitch tiles around a fix and return the image plus an exact lat/lon -> pixel map."""
  cx, cy = world_xy(lat, lon, ZOOM)
  x0, y0 = int(cx) - SPAN, int(cy) - SPAN
  size = (2 * SPAN + 1) * TILE
  im = Image.new("RGB", (size, size))
  for dx in range(2 * SPAN + 1):
    for dy in range(2 * SPAN + 1):
      req = urllib.request.Request(TILE_URL.format(z=ZOOM, x=x0 + dx, y=y0 + dy),
                                   headers={"User-Agent": "comma-rerun"})
      with urllib.request.urlopen(req, timeout=20) as r:
        im.paste(Image.open(io.BytesIO(r.read())).convert("RGB"), (dx * TILE, dy * TILE))

  def to_pixel(la, lo):
    wx, wy = world_xy(la, lo, ZOOM)
    return (wx - x0) * TILE, (wy - y0) * TILE

  return im, to_pixel


STYLE = {
  "blinker":          ((255, 200, 60), "leftBlinker", 3.0),
  "rblinker":         ((120, 220, 255), "rightBlinker", 3.0),
  "latActive":        ((90, 200, 120), "latActive", 2.0),
  "torque/commanded": ((80, 160, 255), "commanded curvature", 3.0),
  "torque/sent":      ((180, 120, 255), "sent curvature", 2.0),
  "steeringAngleDeg": ((200, 200, 200), "steeringAngleDeg", 2.0),
}

# which NDJSON field lands at which entity path, and how to scale it
PATHS = {
  "blinker": ("blinker", 1.0),
  "rblinker": ("rblinker", 1.0),
  "lat_active": ("latActive", 1.0),
  "cmd": ("torque/commanded", 1.0),
  "sent": ("torque/sent", 1.0),
  "angle": ("steeringAngleDeg", 1.0),
}

def draw_destination(to_pixel, latlon):
  """Not static: the param can change mid-drive and the marker has to move with it."""
  dx, dy = to_pixel(*latlon)
  rr.log("map/destination", rr.Points2D([[dx, dy]], radii=11.0, colors=(80, 255, 140),
                                        labels=[f"{latlon[0]:.6f}, {latlon[1]:.6f}"]))


# Connect to a standing viewer when one is up: a spawned viewer dies with the
# script and has crashed under load, taking the stream with it.
rr.init("comma-live")
try:
  rr.connect_grpc("rerun+http://127.0.0.1:9876/proxy")
except Exception:
  rr.spawn()

# Rolling window, unlike the offline template: live data is always at the right
# edge. cursor_relative(0) pins the right edge to the playhead, which rerun keeps
# at the newest sample as long as you have not scrubbed away from it.
window = rrb.VisibleTimeRange(
  TIMELINE,
  start=rrb.TimeRangeBoundary.cursor_relative(seconds=-WINDOW_SECONDS),
  end=rrb.TimeRangeBoundary.cursor_relative(seconds=0),
)
rr.send_blueprint(rrb.Blueprint(
  rrb.Horizontal(
    rrb.Spatial2DView(origin="/map", name="position"),
    rrb.Vertical(
      rrb.TimeSeriesView(origin="/", name="stalk + gate",
                         contents=["+ /blinker", "+ /rblinker", "+ /latActive"], time_ranges=window),
      rrb.TimeSeriesView(origin="/", name="curvature: commanded vs sent (1/m)",
                         contents=["+ /torque/**"], time_ranges=window,
                         axis_y=rrb.ScalarAxis(range=(-1.0, 1.0), zoom_lock=True)),
      rrb.TimeSeriesView(origin="/", name="car",
                         contents=["+ /steeringAngleDeg"], time_ranges=window,
                         axis_y=rrb.ScalarAxis(range=(-450.0, 450.0), zoom_lock=True)),
      row_shares=[1, 2, 1],
    ),
    column_shares=[1, 1],
  ),
  collapse_panels=True,
))

for path, (color, name, width) in STYLE.items():
  rr.log(path, rr.SeriesLines(colors=color, names=name, widths=width), static=True)

to_pixel = None   # set once the first fix arrives and the tiles are fetched
trail = []
dest = DEST       # updated live from the device's ParkingDestination param
n = 0

for line in sys.stdin:
  line = line.strip()
  if not line:
    continue
  try:
    rec = json.loads(line)
  except json.JSONDecodeError:
    continue
  if "_meta" in rec:
    print(f"subscriber up: {rec.get('host')}", file=sys.stderr)
    continue

  rr.set_time(TIMELINE, timestamp=rec["t"] + UTC_OFFSET)

  if "dest_lat" in rec["v"]:
    dest = (rec["v"]["dest_lat"], rec["v"]["dest_lon"])
    print(f"destination -> {dest[0]:.6f},{dest[1]:.6f}", file=sys.stderr)
    if to_pixel is not None:
      draw_destination(to_pixel, dest)
    continue

  if "lat" in rec["v"]:
    lat, lon = rec["v"]["lat"], rec["v"]["lon"]
    if to_pixel is None:
      # centre on the first fix, so the view is always where the car actually is
      im, to_pixel = build_map(lat, lon)
      rr.log("map", rr.Image(np.asarray(im)), static=True)
      print(f"map built at {lat:.6f},{lon:.6f}", file=sys.stderr)
      if dest is not None:
        draw_destination(to_pixel, dest)

    x, y = to_pixel(lat, lon)
    trail.append((x, y))
    del trail[:-2000]
    rr.log("map/car", rr.Points2D([[x, y]], radii=7.0, colors=(255, 40, 40)))
    if len(trail) > 1:
      rr.log("map/trail", rr.LineStrips2D([trail], colors=(255, 180, 60), radii=1.5))
    continue

  for key, val in rec["v"].items():
    path, scale = PATHS[key]
    rr.log(path, rr.Scalars(val * scale))

  n += 1
  if n % 500 == 0:
    print(f"{n} msgs", file=sys.stderr)
