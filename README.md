# comma_rerun

Template for plotting openpilot route data in Rerun.

## Why two scripts

openpilot's venv is Python 3.12. rerun-sdk is installed in the 3.14 user site. They
cannot share an interpreter, so extraction and plotting are separate processes with a
`.npz` between them. This is also the right split regardless: parsing rlogs needs
cereal, drawing does not.

```
rlog.zst --[ extract.py, openpilot 3.12 ]--> route.npz --[ viz.py, 3.14 ]--> Rerun
```

## Use

Offline, from a route's rlogs:

```bash
cd ~/openpilot && uv run python ~/comma_rerun/extract.py
python3 ~/comma_rerun/viz.py
```

Live, from a device (`live` branch):

```bash
./setup_device.sh 172.20.10.11
cd ~/openpilot && uv run python ~/comma_rerun/live_sub.py | python3 ~/comma_rerun/live_viz.py
```

`setup_device.sh` starts the cereal ZMQ bridge and, on ublox devices, `pigeond` +
`ubloxd`. None of that survives a reboot, so re-run it after one.

## Adding signals

`extract.py` keeps one time array per message type (`cs_t`, `cc_t`, `co_t`) because
carState, carControl and carOutput publish at different rates and cannot share a time
base. Add a field to the matching block and a matching array to `np.savez_compressed`.

Verify field names against real data before trusting them — the schema keeps retired
fields forever, and some obvious ones do not exist. `carState` has no `brake` or `gas`
float on this platform, only `brakePressed` / `gasPressed`; the continuous values live
on `carControl`.

`viz.py` has two things to edit:

- `series(...)` calls — one per line on a plot, each with an explicit color and name.
- `blueprint()` — the layout. Each `TimeSeriesView` is one panel; `contents` picks
  entities with `+`/`-` and wildcards. Group by unit and scale, never mix degrees with
  probabilities on one axis.

## Rules learned the hard way

**Crop to the interesting span.** A 235 s route with 8 s of action plots as a flat line.
`--start`/`--end` exist for this.

**Do not use a cursor-relative time range for offline data.** It opens the view at t=0,
which is usually a parked car, and every series reads as zero. Rolling windows are for
live streams.

**Check the data before opening the window.** Print min/max/nonzero per array. A flat
plot is more often a bad view than bad data.

**Timestamps must not be plotted.** `logMonoTime` and friends are ~1e11 nanoseconds and
flatten every real signal sharing their axis.

**Commanded is not sent.** `carControl.actuators.torque` is what the controller asked
for; `carOutput.actuatorsOutput.torqueOutputCan` is what survived the carcontroller's
rate limiter, in car units. Divide by that platform's `STEER_MAX` to compare them
(270 on Hyundai CAN-FD).

**Use `rr.send_columns`, not per-sample `rr.log`.** One columnar batch per series
instead of 200k calls.

**Static vs temporal.** Series color and name describe the series, not a moment:
`static=True`. Only the samples carry time.
