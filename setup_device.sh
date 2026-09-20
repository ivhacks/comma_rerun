#!/usr/bin/env bash
# Prepare a comma device to feed the live rerun display.
#
#   ./setup_device.sh            # uses HOST below
#   ./setup_device.sh 1.2.3.4    # or override
#
# Nothing here survives a reboot, so re-run after one. Idempotent otherwise.
set -uo pipefail

HOST="${1:-172.20.10.11}"
SSH=(ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=12 "comma@$HOST")

echo "== $HOST =="

# The bridge must be launched fully detached: a plain background job dies when the
# ssh channel closes. Kill any existing one first -- a second instance fails its port
# binds, then keeps running while holding sockets and forwarding nothing.
"${SSH[@]}" 'pkill -9 -x bridge 2>/dev/null; exit 0'
sleep 2
"${SSH[@]}" 'cd /data/openpilot && (setsid ./openpilot/cereal/messaging/bridge >/tmp/bridge.log 2>&1 </dev/null &) ; exit 0'

# GPS: manager only runs these onroad, and they can sit alive-but-silent after a
# reboot. Only the ublox path applies when /dev/ttyHS0 is present.
"${SSH[@]}" 'test -e /dev/ttyHS0 || exit 0
pgrep -f ubloxd.pigeond >/dev/null && pgrep -f ubloxd.ubloxd >/dev/null && exit 0
cd /data/openpilot && export PYTHONPATH=/data/openpilot
(setsid /usr/local/venv/bin/python -m openpilot.system.ubloxd.pigeond >/tmp/pigeond.log 2>&1 </dev/null &)
(setsid /usr/local/venv/bin/python -m openpilot.system.ubloxd.ubloxd  >/tmp/ubloxd.log  2>&1 </dev/null &)
exit 0'

sleep 6

"${SSH[@]}" '
bridge=$(pgrep -x bridge || echo NONE)
# grep -c exits 1 on zero matches, so guard the status rather than the output
fails=$(grep -c Failed /tmp/bridge.log 2>/dev/null); fails=${fails:-0}
echo "bridge      $bridge  (bind failures: $fails)"
echo "listening   $(ss -tln | grep -c LISTEN) ports"
echo "onroad      $([ "$(cat /data/params/d/IsOffroad 2>/dev/null)" = "0" ] && echo yes || echo NO - carState will be silent)"
echo "receiver    $(test -e /dev/ttyHS0 && echo "ublox (/dev/ttyHS0)" || echo quectel)"
echo "pigeond     $(pgrep -f ubloxd.pigeond >/dev/null && echo yes || echo no)"
echo "ubloxd      $(pgrep -f ubloxd.ubloxd >/dev/null && echo yes || echo no)"
[ "$fails" != "0" ] && { echo "BRIDGE FAILED TO BIND -- re-run this script"; exit 1; }
exit 0'
