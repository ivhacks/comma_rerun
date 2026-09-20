#!/usr/bin/env bash
# Pair two commas so the main one gets its GPS from the other.
#
#   ./pair_gps.sh <main-ip> <gps-ip>
#
# The GPS device needs sky view; the main one is the car's. Both params are
# PERSISTENT, so this is a one-time setup that survives reboots.
set -uo pipefail

MAIN="${1:?usage: pair_gps.sh <main-ip> <gps-ip>}"
GPS="${2:?usage: pair_gps.sh <main-ip> <gps-ip>}"
BRANCH=gps-relay

ssh_do() { ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=12 "comma@$1" "$2"; }

param() {  # host key value  (empty value clears)
  ssh_do "$1" "cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c \"
from openpilot.common.params import Params
Params().put('$2', '$3', block=True)\""
}

for host in "$GPS" "$MAIN"; do
  echo "== $host: checking out $BRANCH =="
  ssh_do "$host" "cd /data/openpilot && git fetch origin $BRANCH 2>&1 | tail -1 && git checkout -f -B $BRANCH FETCH_HEAD 2>&1 | tail -1"
done

echo "== $GPS: publisher =="
param "$GPS" GpsSource ""
ssh_do "$GPS" "cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c \"
from openpilot.common.params import Params
Params().put_bool('GpsPublish', True, block=True)\""

# GpsPublish on the receiver too: it is what lets a laptop subscribe to the main
# device, and it coexists with the receive bridge (one connects out, one binds).
echo "== $MAIN: receiver, source $GPS =="
ssh_do "$MAIN" "cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -c \"
from openpilot.common.params import Params
Params().put_bool('GpsPublish', True, block=True)\""
param "$MAIN" GpsSource "$GPS"

echo
echo "== state =="
for host in "$GPS" "$MAIN"; do
  echo "$host  GpsPublish=$(ssh_do "$host" 'cat /data/params/d/GpsPublish 2>/dev/null || echo -')  GpsSource=$(ssh_do "$host" 'cat /data/params/d/GpsSource 2>/dev/null || echo -')"
done

echo
echo "manager picks the params up on its next process check; no reboot needed."
echo "verify: ./check_gps.sh $MAIN"
