#!/usr/bin/env python3
"""Pull signals out of a route's rlogs into a .npz.

Runs in openpilot's environment (3.12, needs cereal):

    cd ~/openpilot && uv run python ~/comma_rerun/extract.py

Add fields by extending the three per-message blocks below. Keep one list per
message type: their rates differ, so they cannot share a time base.
"""
import pathlib

import numpy as np

from openpilot.tools.lib.logreader import LogReader

ROUTE = "00000011"
SEGMENT_DIR = pathlib.Path("/home/iv/steering-test")
OUT = SEGMENT_DIR / f"{ROUTE}.npz"

segs = sorted(SEGMENT_DIR.glob(f"{ROUTE}--*/rlog.zst"),
              key=lambda p: int(p.parent.name.rsplit("--", 1)[1]))
if not segs:
  raise SystemExit(f"no segments for {ROUTE} under {SEGMENT_DIR}")

t0 = None
wall_offset = None  # wallTimeNanos - logMonoTime, from the clocks message
cs_t, blinker, pressed, angle, v = [], [], [], [], []
cc_t, cmd, lat_active = [], [], []
co_t, sent = [], []

for s in segs:
  try:
    for m in LogReader(str(s)):
      w = m.which()
      if w == "clocks" and wall_offset is None:
        wall_offset = m.clocks.wallTimeNanos - m.logMonoTime
        continue
      if w not in ("carState", "carControl", "carOutput"):
        continue
      t = m.logMonoTime * 1e-9
      if t0 is None:
        t0 = t
      t -= t0

      if w == "carState":
        c = m.carState
        cs_t.append(t)
        blinker.append(float(c.leftBlinker))
        pressed.append(float(c.steeringPressed))
        angle.append(c.steeringAngleDeg)
        v.append(c.vEgo)
      elif w == "carControl":
        c = m.carControl
        cc_t.append(t)
        cmd.append(c.actuators.torque)
        lat_active.append(float(c.latActive))
      else:
        # post rate-limit value the carcontroller actually put on CAN
        co_t.append(t)
        sent.append(m.carOutput.actuatorsOutput.torqueOutputCan)
  except Exception as e:
    print(f"  skipped {s.parent.name}: {type(e).__name__}")

# epoch seconds of t=0, so the viewer can show wall-clock time
t_start_epoch = (t0 * 1e9 + wall_offset) / 1e9 if wall_offset is not None else 0.0

np.savez_compressed(
  OUT,
  t_start_epoch=np.array(t_start_epoch),
  cs_t=np.array(cs_t), blinker=np.array(blinker), pressed=np.array(pressed),
  angle=np.array(angle), v_ego=np.array(v),
  cc_t=np.array(cc_t), cmd=np.array(cmd), lat_active=np.array(lat_active),
  co_t=np.array(co_t), sent=np.array(sent),
)
print(f"wrote {OUT}  ({cs_t[-1]:.0f}s, {len(cs_t)} carState, {len(cc_t)} carControl)")
