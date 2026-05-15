#!/bin/bash
# Double-click in Finder to START the Compass server.
cd "$(dirname "$0")"
./compass-ctl.sh start
echo
echo "— press any key to close this window —"
read -n 1 -s
