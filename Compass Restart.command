#!/bin/bash
# Double-click in Finder to RESTART Compass (use this after code changes).
cd "$(dirname "$0")"
./compass-ctl.sh restart
echo
echo "— press any key to close this window —"
read -n 1 -s
