#!/usr/bin/env bash
# Build a Chrome Web Store upload zip containing ONLY runtime files.
# Allowlist (not denylist) so dev-only files — tests/, the SPEC/POSSIBILITIES/
# EDGE-CASES/README docs, package.sh itself — can never leak into the package.
#
# Usage:  ./package.sh        → writes dist/compass-extension-<version>.zip
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(python3 -c "import json; print(json.load(open('manifest.json'))['version'])")
OUT="dist/compass-extension-${VERSION}.zip"

RUNTIME=(
  manifest.json
  background.js
  sidepanel.html
  sidepanel.js
  sidepanel.css
  popup.css
  lib
  icons
)

# Fail loudly if anything in the allowlist is missing.
for f in "${RUNTIME[@]}"; do
  [ -e "$f" ] || { echo "ERROR: missing runtime file/dir: $f" >&2; exit 1; }
done

mkdir -p dist
rm -f "$OUT"
zip -r -X "$OUT" "${RUNTIME[@]}" -x "*/.DS_Store" "*.DS_Store" >/dev/null

echo "Built $OUT"
unzip -l "$OUT" | tail -n +2
