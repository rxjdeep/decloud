#!/usr/bin/env bash
# Install the reclmr app (the WebView shell, android/shell) on an Android phone through a shell you already have on it.
# It is a separate app from reclmr Backup and never touches it. Same options as install_pusher.sh:
#   PHONE=shell@192.168.0.50 PORT=2222 ./scripts/install_app.sh
#   PHONE=s23 PORT= ./scripts/install_app.sh           KEY=~/.ssh/phone-noble ./scripts/install_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."
APK=android/shell/build/reclmr.apk
envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
PHONE="${PHONE:-$(envval RECLMR_PHONE)}"          # your phone's shell address, kept in .env (not in the repo)
[ -n "$PHONE" ] || { echo "Set RECLMR_PHONE=shell@<phone address> in .env, or run with PHONE=shell@<address>." >&2; exit 1; }
PORT="${PORT-2222}"
KEY="${KEY:-}"
PKG=io.reclmr.app
[ -f "$APK" ] || { echo "Build it first: ./scripts/build_android.sh shell" >&2; exit 1; }

SSH_OPTS=(${PORT:+-p "$PORT"} ${KEY:+-i "$KEY"})
SCP_OPTS=(${PORT:+-P "$PORT"} ${KEY:+-i "$KEY"})

scp "${SCP_OPTS[@]}" "$APK" "$PHONE:/data/local/tmp/reclmr.apk"
ssh "${SSH_OPTS[@]}" "$PHONE" "
  pm install -r /data/local/tmp/reclmr.apk &&
  { pm grant $PKG android.permission.POST_NOTIFICATIONS 2>/dev/null || true; } &&
  { pm grant $PKG android.permission.WRITE_SECURE_SETTINGS 2>/dev/null || true; } &&
  am start -n $PKG/.MainActivity &&
  rm -f /data/local/tmp/reclmr.apk
"
echo "Installed reclmr ($PKG). reclmr Backup was not touched."
