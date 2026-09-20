#!/usr/bin/env bash
# Show where a comma's GPS is coming from and whether it is working.
#
#   ./check_gps.sh <ip>
set -uo pipefail

HOST="comma@${1:?usage: check_gps.sh <ip>}"

ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=12 "$HOST" '
echo "GpsPublish   $(cat /data/params/d/GpsPublish 2>/dev/null || echo -)"
echo "GpsSource    $(cat /data/params/d/GpsSource 2>/dev/null || echo -)"
echo "gpsbridge    $(pgrep -f "[g]psbridge.gpsbridge" >/dev/null && echo running || echo no)"
echo "bridge       $(pgrep -x bridge || echo none)"
echo "ubloxd       $(pgrep -f "[u]bloxd.ubloxd" >/dev/null && echo running || echo "no (expected when GpsSource is set)")"
cd /data/openpilot && PYTHONPATH=/data/openpilot timeout 15 /usr/local/venv/bin/python -c "
from openpilot.cereal import messaging
import time
s = messaging.sub_sock(\"gpsLocationExternal\", timeout=1000)
n = 0
for _ in range(30):
    m = messaging.recv_one_or_none(s)
    if m:
        g = m.gpsLocationExternal
        n += 1
        if g.hasFix:
            print(f\"gps          FIX {g.latitude:.6f},{g.longitude:.6f} acc={g.horizontalAccuracy:.1f}m sats={g.satelliteCount}\")
            break
    time.sleep(0.2)
else:
    print(f\"gps          no fix (messages seen: {n})\")
"'
