#!/bin/bash
# Double-click in Finder to STOP the Compass server.
cd "$(dirname "$0")"
./compass-ctl.sh stop
echo
echo "— press any key to close this window —"
read -n 1 -s
