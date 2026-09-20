# GPS relay + live display

The car's windshield is metallized and blocks GPS — the device behind it tracks
**zero satellites**, not a weak fix. A second comma with sky view relays its position
to the main one, and this repo drives the setup and the live rerun display.

The openpilot-side code lives on branch `gps-relay` of `ivhacks/openpilot`, with its
own `gps-relay.md` covering the daemon internals. This file is about *using* it from
a laptop.

Substitute your own addresses for `GPS_IP` (spare comma, sky view) and `MAIN_IP`
(the one in the car).

---

## Quick start

```bash
./pair_gps.sh MAIN_IP GPS_IP     # branch + both params on both devices
./check_gps.sh MAIN_IP           # verify the fix is crossing

# point the display at the MAIN device, then:
cd ~/openpilot && uv run python ~/comma_rerun/live_sub.py | python3 ~/comma_rerun/live_viz.py
```

`HOST` at the top of `live_sub.py` must be **MAIN_IP**. The point is seeing what the
car's device believes, not the spare.

After `pair_gps.sh`, restart openpilot on both so the new branch loads:

```bash
for h in GPS_IP MAIN_IP; do ssh comma@$h 'sudo systemctl restart comma'; done
```

That triggers a `build.py` run taking **several minutes**. `manager.py` is absent
from the process list until it finishes. Wait rather than debugging.

---

## The scripts

| script | what it does |
|---|---|
| `pair_gps.sh MAIN_IP GPS_IP` | checks out `gps-relay` on both, sets `GpsPublish` and `GpsSource` |
| `check_gps.sh IP` | params, daemons, bridges, and whether that device has a fix |
| `setup_device.sh IP` | starts the bridge and ublox daemons by hand — only needed on a device *not* running the `gps-relay` branch |

`setup_device.sh` is the pre-relay tool. Once both devices run `gps-relay`, manager
starts everything itself and you should not need it.

### What `pair_gps.sh` sets

| device | `GpsPublish` | `GpsSource` |
|---|---|---|
| GPS | `1` | empty |
| main | `1` | `GPS_IP` |

`GpsPublish` on **main** is deliberate. Receive mode only connects outward and
publishes nothing, so without it your laptop cannot subscribe to the main device at
all. Both params are `PERSISTENT` and survive reboots.

### Expected `check_gps.sh` output

```
GpsPublish   1
GpsSource    GPS_IP
gpsbridge    running
bridge       <pid>
ubloxd       no (expected when GpsSource is set)
gps          FIX 32.751588,-117.196341 acc=1.8m sats=13
```

On the **GPS** device instead: `GpsSource` empty, `ubloxd running`.

Main has no local GPS receiver, so any fix reported there arrived over the relay.
That is the end-to-end proof.

---

## The display

Two processes joined by a pipe, because openpilot's venv is Python 3.12 (has cereal)
and rerun-sdk is in the 3.14 user site. No interpreter has both.

```
live_sub.py   3.12   ZMQ from MAIN_IP  ->  NDJSON on stdout
live_viz.py   3.14   NDJSON on stdin   ->  rerun
```

`cd ~/openpilot` first — `uv run` resolves the venv from the working directory.

### Panels

Left half is a satellite map; right half is three stacked plots.

| panel | series |
|---|---|
| map | car position, trail, destination marker |
| stalk + gate | `leftBlinker`, `rightBlinker`, `latActive` |
| lateral | commanded vs sent, y-axis pinned −1..1 |
| car | `steeringAngleDeg`, y-axis pinned −450..450 |

The map builds itself on the **first GPS fix** and centres there. Walk or drive
outside its span and you leave the frame — raise `SPAN` in `live_viz.py` (tiles either
side of centre; 2 gives ~320 m at `ZOOM = 19`).

Tiles come from Esri World Imagery, which needs no API key. Rerun's built-in
`MapView` with `MapboxSatellite` would need `RERUN_MAPBOX_ACCESS_TOKEN`.

### Constants worth knowing

`live_sub.py`: `HOST`, `MAX_HZ`
`live_viz.py`: `WINDOW_SECONDS` (30s rolling), `DEST`, `ZOOM`, `SPAN`, `STEER_MAX`

### Which fields the lateral panel plots

Depends on the car:

- **torque cars** (Hyundai/Kia): `actuators.torque` and `actuatorsOutput.torqueOutputCan`, the latter divided by that platform's `STEER_MAX` (270 on Hyundai CAN-FD).
- **curvature cars** (VW ID.4 and friends): `actuators.curvature` and `actuatorsOutput.curvature`, in 1/m.

Check with:

```bash
ssh comma@MAIN_IP 'cd /data/openpilot && PYTHONPATH=/data/openpilot timeout 15 /usr/local/venv/bin/python -c "
from openpilot.cereal import messaging
import time
sm = messaging.SubMaster([\"carParams\"])
for _ in range(50):
    sm.update(100)
    if sm.updated[\"carParams\"]:
        print(sm[\"carParams\"].carFingerprint, sm[\"carParams\"].steerControlType); break
    time.sleep(0.1)"'
```

If the lateral panel is flat zero, you are almost certainly plotting the wrong one of
those two pairs. `live_sub.py` currently emits **curvature**.

---

## Offline plots

Same repo, for recorded routes rather than live:

```bash
cd ~/openpilot && uv run python ~/comma_rerun/extract.py    # rlogs -> .npz
python3 ~/comma_rerun/viz.py                                 # .npz -> rerun
```

Route and paths are constants at the top of each file. `viz.py` crops to
`START`/`END` seconds — a whole route is mostly parked and flat, and a plot that
opens on dead air looks like broken data.

---

## Troubleshooting

### Everything is flat / nothing on screen

Check in this order:

1. **Is there a GPS fix at all?** `./check_gps.sh GPS_IP`. Zero satellites means no
   sky view or an unseated antenna — no software fix exists.
2. **Is main onroad?** `carState`/`carControl` only publish onroad, so the graphs are
   legitimately flat with the car parked. GPS still works offroad.
3. **Is data reaching the laptop?**
   ```bash
   cd ~/openpilot && timeout 20 uv run python ~/comma_rerun/live_sub.py | head -5
   ```
   Only a `_meta` line means the main device is not publishing over ZMQ — see below.

### Only `_meta`, no data

The main device is running the bridge in receive mode only. Set `GpsPublish=1` on it
and restart, or start a publish bridge by hand:

```bash
ssh comma@MAIN_IP 'cd /data/openpilot && (setsid ./openpilot/cereal/messaging/bridge >/tmp/pubbridge.log 2>&1 </dev/null &) ; exit 0'
```

### Ports listening, still no data

Two causes, both common:

**Two bridges racing.** The loser fails its binds, prints `Address already in use`,
then keeps running holding sockets and forwarding nothing.

```bash
ssh comma@GPS_IP 'grep -c Failed /tmp/bridge.log'   # must be 0
```

**Stale msgq segment.** The bridge attaches its msgq subscriber on first client
connect; if no publisher existed yet it lands on a dead shared-memory segment
permanently. Happens when the bridge starts before `ubloxd`.

Either way:

```bash
ssh comma@GPS_IP 'pkill -9 -x bridge'   # gpsbridge restarts it within ~2s
```

### Rerun viewer crashes with `transport error`

The spawned viewer dies under load and takes the stream with it. Start it standalone
and let the script connect:

```bash
rerun --port 9876 &
```

`live_viz.py` already tries `connect_grpc` first and falls back to spawning.

### `pkill`/`pgrep` behaving oddly

`pgrep -f foo` matches its own command line, and `pkill -f foo` kills the shell
running it — visible as exit code 143 or 144. Bracket the first character:
`pgrep -f "[f]oo"`.

### Anything started over SSH dies immediately

A plain `&` dies when the channel closes. Always:

```bash
ssh comma@IP 'cd /data/openpilot && (setsid ./cmd >/tmp/log 2>&1 </dev/null &) ; exit 0'
```

### Parking params keep disappearing

`ParkingDestination` and `ParkingNavEnabled` are
`CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION` — wiped on every reboot *and*
every ignition cycle. `GpsPublish`/`GpsSource` are `PERSISTENT` and survive.

```bash
ssh comma@MAIN_IP 'cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c "
from openpilot.common.params import Params
p = Params()
p.put(\"ParkingDestination\", {\"latitude\": LAT, \"longitude\": LON}, block=True)
p.put_bool(\"ParkingNavEnabled\", True, block=True)"'
```

Pass a **dict**, not a JSON string — the conversion table has `(dict, JSON)` but not
`(str, JSON)`. Same reason `put_bool` is required for BOOL params.

### Never `pkill manager.py`

`launch_chffrplus.sh` does not loop; it falls into `while true; do sleep 1; done` and
openpilot stays dead. Use `sudo systemctl restart comma`.
