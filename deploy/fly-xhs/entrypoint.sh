#!/bin/sh
set -eu
mkdir -p /data/xhs
chown browser:browser /data/xhs
chmod 700 /data/xhs
# Hard wall-clock deadline, never renewed by requests. restart.policy must be no.
timeout --signal=TERM --kill-after=15s 300s gosu browser xvfb-run -a \
  python -m uvicorn xhs_fly_service:app --host 0.0.0.0 --port 8080 --no-access-log || true
exit 0
