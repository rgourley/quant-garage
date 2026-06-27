#!/usr/bin/env bash
# Re-render PNG assets from their HTML sources via headless Chrome.
# Run from the repo root: ./scripts/render-assets.sh
# All three assets are 1200x630.

set -euo pipefail

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ASSETS_DIR="$(cd "$(dirname "$0")/../assets" && pwd)"

if [ ! -x "$CHROME" ]; then
  echo "Chrome not found at $CHROME" >&2
  exit 1
fi

render() {
  local name="$1"
  local html="$ASSETS_DIR/$name.html"
  local png="$ASSETS_DIR/$name.png"

  if [ ! -f "$html" ]; then
    echo "Missing $html" >&2
    return 1
  fi

  echo "Rendering $name.html -> $name.png"
  "$CHROME" \
    --headless=new \
    --disable-gpu \
    --hide-scrollbars \
    --window-size=1200,630 \
    --virtual-time-budget=10000 \
    --screenshot="$png" \
    "file://$html" \
    2>/dev/null

  if [ ! -f "$png" ]; then
    echo "Render failed for $name" >&2
    return 1
  fi
}

render og
render skills
render closing

echo "Done. Re-rendered: og.png, skills.png, closing.png"
