#!/bin/bash
# Double-click in Finder to check whether Compass is running.
cd "$(dirname "$0")"
./compass-ctl.sh status
echo
echo "— press any key to close this window —"
read -n 1 -s
