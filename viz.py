#!/usr/bin/env python3
"""Plot an extracted route in Rerun.

Runs under the interpreter that has rerun-sdk (3.14), not openpilot's venv:

    python3 ~/comma_rerun/viz.py

START/END crop to the interesting span. A whole route is mostly parked and flat,
and a plot that opens on dead air looks like broken data.
"""
import datetime

import numpy as np
import rerun as rr
import rerun.blueprint as rrb

NPZ = "/home/iv/steering-test/00000011.npz"
START = 84.0
END = 142.0

TIMELINE = "t"
KICK_TORQUE = 0.5
KICK_SECONDS = 4.0
STEER_MAX = 270  # hyundai CANFD, for normalising torqueOutputCan back to a fraction

raw = np.load(NPZ)

# The viewer renders timestamps in UTC. Shifting by the local offset makes the axis
# read local wall-clock without touching viewer settings. Computed at the recording's
# own epoch, so DST is right for that date.
EPOCH = float(raw["t_start_epoch"])
EPOCH += datetime.datetime.fromtimestamp(EPOCH).astimezone().utcoffset().total_seconds()

# keep only the driving lap; the rest of the route is parked and flat
d = {}
for tkey in ("cs_t", "cc_t", "co_t"):
  t = raw[tkey]
  keep = (t >= START) & (t <= END)
  d[tkey] = t[keep]
  for k in raw.files:
    if k.endswith("_t") or np.ndim(raw[k]) == 0 or len(raw[k]) != len(t) or k in d:
      continue
    d[k] = raw[k][keep]


def series(path, t, vals, color, name, width=2.0):
  rr.log(path, rr.SeriesLines(colors=color, names=name, widths=width), static=True)
  rr.send_columns(path, [rr.TimeColumn(TIMELINE, timestamp=t + EPOCH)],
                  rr.Scalars.columns(scalars=vals))


rr.init("blinker-kick", spawn=True)

# No rolling window: the whole lap is the interesting unit, and a cursor-relative
# range opens at t=0 where nothing has happened yet.
rr.send_blueprint(rrb.Blueprint(
  rrb.Vertical(
    rrb.TimeSeriesView(origin="/", name="stalk + gate",
                       contents=["+ /blinker", "+ /latActive"]),
    rrb.TimeSeriesView(origin="/", name="torque: commanded vs sent (fraction of STEER_MAX)",
                       contents=["+ /torque/**"]),
    rrb.TimeSeriesView(origin="/", name="car",
                       contents=["+ /steeringAngleDeg"]),
    row_shares=[1, 2, 1],
  ),
  collapse_panels=True,
))

# stalk, and the gate that decides whether the kick fires
series("blinker", d["cs_t"], d["blinker"], (255, 200, 60), "leftBlinker", 3.0)
series("latActive", d["cc_t"], d["lat_active"], (90, 200, 120), "latActive")

# what the controller asked for vs what survived the rate limiter
series("torque/commanded", d["cc_t"], d["cmd"], (80, 160, 255), "commanded", 3.0)
series("torque/sent", d["co_t"], d["sent"] / STEER_MAX, (180, 120, 255), "sent (/STEER_MAX)")

series("steeringAngleDeg", d["cs_t"], d["angle"], (200, 200, 200), "steeringAngleDeg")

# contiguous runs where the command sits at exactly the kick torque
on = np.isclose(d["cmd"], KICK_TORQUE, atol=1e-6)
edges = np.diff(on.astype(int))
starts = np.flatnonzero(edges == 1) + 1
ends = np.flatnonzero(edges == -1) + 1
if on[0]:
  starts = np.r_[0, starts]
if on[-1]:
  ends = np.r_[ends, len(on)]

for i, (a, b) in enumerate(zip(starts, ends)):
  start, frames = d["cc_t"][a], b - a
  dur = d["cc_t"][b - 1] - start
  full = frames >= int(KICK_SECONDS * 100) - 1
  label = f"kick {i}: {dur:.2f}s / {frames} frames" + ("" if full else "  ABORTED")
  rr.set_time(TIMELINE, timestamp=start + EPOCH)
  rr.log("events", rr.TextLog(label, level="INFO" if full else "WARN"))
  print(f"  {label}")

print(f"{len(starts)} kicks over {d['cs_t'][0]:.0f}-{d['cs_t'][-1]:.0f}s")
