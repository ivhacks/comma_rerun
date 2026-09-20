#!/usr/bin/env python3
"""Plot an extracted route in Rerun.

Runs under the interpreter that has rerun-sdk (3.14), not openpilot's venv:

    python3 ~/comma_rerun/viz.py ~/steering-test/00000011.npz --start 84 --end 142

Crop to the interesting span with --start/--end. A whole route is mostly parked
and flat, and a plot that opens on dead air looks like broken data.
"""
import argparse

import numpy as np
import rerun as rr
import rerun.blueprint as rrb

TIMELINE = "t"
KICK_TORQUE = 0.5
KICK_SECONDS = 4.0
STEER_MAX = 270  # hyundai CANFD, for normalising torqueOutputCan back to a fraction


def series(path, t, vals, color, name, width=2.0):
  rr.log(path, rr.SeriesLines(colors=color, names=name, widths=width), static=True)
  rr.send_columns(path, [rr.TimeColumn(TIMELINE, duration=t)],
                  rr.Scalars.columns(scalars=vals))


def find_kicks(t, cmd):
  """Contiguous runs where the command sits at exactly the kick torque."""
  on = np.isclose(cmd, KICK_TORQUE, atol=1e-6)
  if not on.any():
    return []
  d = np.diff(on.astype(int))
  starts = np.flatnonzero(d == 1) + 1
  ends = np.flatnonzero(d == -1) + 1
  if on[0]:
    starts = np.r_[0, starts]
  if on[-1]:
    ends = np.r_[ends, len(on)]
  return [(t[a], t[b - 1], b - a) for a, b in zip(starts, ends)]


def blueprint() -> rrb.Blueprint:
  # No rolling window: the whole lap is the interesting unit, and a cursor-relative
  # range opens at t=0 where nothing has happened yet.
  return rrb.Blueprint(
    rrb.Vertical(
      rrb.TimeSeriesView(origin="/", name="stalk + gate",
                         contents=["+ /blinker", "+ /latActive", "+ /steeringPressed"]),
      rrb.TimeSeriesView(origin="/", name="torque: commanded vs sent (fraction of STEER_MAX)",
                         contents=["+ /torque/**"]),
      rrb.TimeSeriesView(origin="/", name="car",
                         contents=["+ /steeringAngleDeg", "+ /vEgo"]),
      row_shares=[1, 2, 1],
    ),
    collapse_panels=True,
  )


def crop(d, lo, hi):
  """Keep only the driving lap; the rest of the route is parked and flat."""
  out = {}
  for prefix, tkey in (("cs", "cs_t"), ("cc", "cc_t"), ("co", "co_t")):
    t = d[tkey]
    m = (t >= lo) & (t <= hi)
    out[tkey] = t[m]
    for k in d.files:
      if k == tkey or not len(d[k]) == len(t):
        continue
      if k.endswith("_t"):
        continue
      if k not in out:
        out[k] = d[k][m]
  return out


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("npz")
  ap.add_argument("--start", type=float, default=84.0)
  ap.add_argument("--end", type=float, default=142.0)
  args = ap.parse_args()
  raw = np.load(args.npz)
  d = crop(raw, args.start, args.end)

  rr.init("blinker-kick", spawn=True)
  rr.send_blueprint(blueprint())

  # stalk and the two conditions that gate / abort the kick
  series("blinker", d["cs_t"], d["blinker"], (255, 200, 60), "leftBlinker", 3.0)
  series("latActive", d["cc_t"], d["lat_active"], (90, 200, 120), "latActive")
  series("steeringPressed", d["cs_t"], d["pressed"], (230, 90, 90), "steeringPressed")

  # what the controller asked for vs what survived the rate limiter
  series("torque/commanded", d["cc_t"], d["cmd"], (80, 160, 255), "commanded", 3.0)
  series("torque/sent", d["co_t"], d["sent"] / STEER_MAX, (180, 120, 255), "sent (/STEER_MAX)")
  ref = np.full_like(d["cc_t"], KICK_TORQUE)
  series("torque/kick_level", d["cc_t"], ref, (120, 120, 130), "0.5 reference", 1.0)

  series("steeringAngleDeg", d["cs_t"], d["angle"], (200, 200, 200), "steeringAngleDeg")
  series("vEgo", d["cs_t"], d["v_ego"], (140, 180, 160), "vEgo m/s")

  kicks = find_kicks(d["cc_t"], d["cmd"])
  for i, (start, end, frames) in enumerate(kicks):
    dur = end - start
    full = frames >= int(KICK_SECONDS * 100) - 1
    label = f"kick {i}: {dur:.2f}s / {frames} frames" + ("" if full else "  ABORTED")
    rr.set_time(TIMELINE, duration=start)
    rr.log("events", rr.TextLog(label, level="INFO" if full else "WARN"))
    print(f"  {label}")

  print(f"{len(kicks)} kicks over {d['cs_t'][0]:.0f}-{d['cs_t'][-1]:.0f}s ({d['cs_t'][-1]-d['cs_t'][0]:.0f}s lap)")


if __name__ == "__main__":
  main()
