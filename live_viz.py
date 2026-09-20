#!/usr/bin/env python3
"""Live version of viz.py: same three panels, streamed instead of from a .npz.

Runs under the interpreter that has rerun-sdk (3.14), not openpilot's venv:

    cd ~/openpilot && uv run python ~/comma_rerun/live_sub.py | python3 ~/comma_rerun/live_viz.py
"""
import datetime
import json
import sys

import rerun as rr
import rerun.blueprint as rrb

TIMELINE = "t"
WINDOW_SECONDS = 30.0
STEER_MAX = 270  # hyundai CANFD, for normalising torqueOutputCan back to a fraction

# The viewer renders timestamps in UTC. Shifting by the local offset makes the axis
# read local wall-clock without touching viewer settings.
UTC_OFFSET = datetime.datetime.now().astimezone().utcoffset().total_seconds()

STYLE = {
  "blinker":          ((255, 200, 60), "leftBlinker", 3.0),
  "rblinker":         ((120, 220, 255), "rightBlinker", 3.0),
  "latActive":        ((90, 200, 120), "latActive", 2.0),
  "torque/commanded": ((80, 160, 255), "commanded", 3.0),
  "torque/sent":      ((180, 120, 255), "sent (/STEER_MAX)", 2.0),
  "steeringAngleDeg": ((200, 200, 200), "steeringAngleDeg", 2.0),
}

# which NDJSON field lands at which entity path, and how to scale it
PATHS = {
  "blinker": ("blinker", 1.0),
  "rblinker": ("rblinker", 1.0),
  "lat_active": ("latActive", 1.0),
  "cmd": ("torque/commanded", 1.0),
  "sent": ("torque/sent", 1.0 / STEER_MAX),
  "angle": ("steeringAngleDeg", 1.0),
}

rr.init("comma-live", spawn=True)

# Rolling window, unlike the offline template: live data is always at the right
# edge. cursor_relative(0) pins the right edge to the playhead, which rerun keeps
# at the newest sample as long as you have not scrubbed away from it.
window = rrb.VisibleTimeRange(
  TIMELINE,
  start=rrb.TimeRangeBoundary.cursor_relative(seconds=-WINDOW_SECONDS),
  end=rrb.TimeRangeBoundary.cursor_relative(seconds=0),
)
rr.send_blueprint(rrb.Blueprint(
  rrb.Vertical(
    rrb.TimeSeriesView(origin="/", name="stalk + gate",
                       contents=["+ /blinker", "+ /rblinker", "+ /latActive"], time_ranges=window),
    rrb.TimeSeriesView(origin="/", name="torque: commanded vs sent (fraction of STEER_MAX)",
                       contents=["+ /torque/**"], time_ranges=window),
    rrb.TimeSeriesView(origin="/", name="car",
                       contents=["+ /steeringAngleDeg"], time_ranges=window),
    row_shares=[1, 2, 1],
  ),
  collapse_panels=True,
))

for path, (color, name, width) in STYLE.items():
  rr.log(path, rr.SeriesLines(colors=color, names=name, widths=width), static=True)

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
  for key, val in rec["v"].items():
    path, scale = PATHS[key]
    rr.log(path, rr.Scalars(val * scale))

  n += 1
  if n % 500 == 0:
    print(f"{n} msgs", file=sys.stderr)
