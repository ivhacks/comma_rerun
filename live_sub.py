#!/usr/bin/env python3
"""Subscribe to a comma over ZMQ and emit the template's signals as NDJSON.

Runs in openpilot's environment (3.12, needs cereal + pyzmq):

    cd ~/openpilot && uv run python ~/comma_rerun/live_sub.py | python3 ~/comma_rerun/live_viz.py

Requires ./openpilot/cereal/messaging/bridge running on the device.
"""
import json
import subprocess
import sys
import threading
import time

import zmq

from openpilot.cereal import log

HOST = "172.20.10.11"
MAX_HZ = 20.0

# Mirrors get_port() in cereal/messaging/bridge_zmq.cc: the bridge derives each
# service's TCP port from an FNV-1a hash of its name, so both ends must agree.
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
START_PORT = 8023
MAX_PORT = 65535

SERVICES = ("carState", "carControl", "carOutput", "gpsLocationExternal")

DEST_POLL_SECONDS = 2.0


def poll_destination():
  """ParkingDestination is a param on the device, not a cereal message, so the only
  way to see it change is to read the file. Emits only on change."""
  last = None
  while True:
    try:
      out = subprocess.run(
        ["ssh", "-n", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=5", f"comma@{HOST}",
         "cat /data/params/d/ParkingDestination 2>/dev/null"],
        capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
      out = ""
    if out != last:
      last = out
      try:
        d = json.loads(out)
        print(json.dumps({"t": time.time(),
                          "v": {"dest_lat": float(d["latitude"]),
                                "dest_lon": float(d["longitude"])}}), flush=True)
      except Exception:
        pass
    time.sleep(DEST_POLL_SECONDS)


def port_for(name):
  h = FNV_OFFSET
  for b in name.encode():
    h = ((h ^ b) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
  return START_PORT + (h % (MAX_PORT - START_PORT))


def fields(evt, which):
  if which == "carState":
    c = evt.carState
    return {"blinker": float(c.leftBlinker), "rblinker": float(c.rightBlinker),
            "angle": c.steeringAngleDeg}
  if which == "carControl":
    c = evt.carControl
    return {"cmd": c.actuators.curvature, "lat_active": float(c.latActive)}
  if which == "carOutput":
    # post rate-limit value the carcontroller actually put on CAN
    return {"sent": evt.carOutput.actuatorsOutput.curvature}
  g = evt.gpsLocationExternal
  if not g.hasFix:
    return {}
  return {"lat": g.latitude, "lon": g.longitude}


ctx = zmq.Context()
poller = zmq.Poller()
by_sock = {}

for name in SERVICES:
  sock = ctx.socket(zmq.SUB)
  sock.setsockopt(zmq.SUBSCRIBE, b"")
  sock.setsockopt(zmq.CONFLATE, 1)  # only ever hand us the newest sample
  sock.connect(f"tcp://{HOST}:{port_for(name)}")
  poller.register(sock, zmq.POLLIN)
  by_sock[sock] = name

print(json.dumps({"_meta": "subscribed", "host": HOST}), flush=True)
threading.Thread(target=poll_destination, daemon=True).start()

min_dt = 1.0 / MAX_HZ
last = dict.fromkeys(SERVICES, 0.0)

try:
  while True:
    for sock, _ in poller.poll(1000):
      name = by_sock[sock]
      try:
        raw = sock.recv(zmq.NOBLOCK)
      except zmq.Again:
        continue

      now = time.monotonic()
      if now - last[name] < min_dt:
        continue
      last[name] = now

      try:
        with log.Event.from_bytes(raw) as evt:
          vals = fields(evt, evt.which())
      except Exception:
        continue

      if vals:
        print(json.dumps({"t": time.time(), "v": vals}), flush=True)
except (KeyboardInterrupt, BrokenPipeError):
  sys.exit(0)
