#!/bin/sh
# Renders the branded manuals to ../*.pdf with headless Chromium/Chrome.
# Usage: ./build_manuals.sh   (set CHROME=/path/to/chrome if it is not found)
cd "$(dirname "$0")"
CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || echo /opt/pw-browsers/chromium-1194/chrome-linux/chrome)}"
for doc in user_manual:UMS_Serialine_User_Manual technician_manual:UMS_Serialine_Technician_Manual; do
  src="${doc%%:*}.html"; out="../${doc##*:}.pdf"
  [ -f "$src" ] || continue
  "$CHROME" --headless=new --no-sandbox --disable-gpu --no-pdf-header-footer \
    --allow-file-access-from-files --virtual-time-budget=5000 \
    --print-to-pdf="$out" "file://$PWD/$src" 2>/dev/null && echo "built $out"
done
