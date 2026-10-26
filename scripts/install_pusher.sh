#!/usr/bin/env bash
# Put the phone backup app on an Android phone through a shell you already have on it (an adb-style sshd, `adb shell`, ...).
# Nothing runs by itself: review it, then run it. The phone's address comes from RECLMR_PHONE in .env (or PHONE=...); the port defaults to 2222.
#   PHONE=shell@192.168.0.50 PORT=2222 ./scripts/install_pusher.sh
#   PHONE=s23 PORT= ./scripts/install_pusher.sh                  # an ssh_config alias that already sets the port (empty PORT = pass none)
#   KEY=~/.ssh/phone-noble ./scripts/install_pusher.sh           # a specific identity file
set -euo pipefail
cd "$(dirname "$0")/.."
APK=android/pusher/build/reclmr-pusher.apk
envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
PHONE="${PHONE:-$(envval RECLMR_PHONE)}"          # your phone's shell address, kept in .env (not in the repo)
[ -n "$PHONE" ] || { echo "Set RECLMR_PHONE=shell@<phone address> in .env, or run with PHONE=shell@<address>." >&2; exit 1; }
PORT="${PORT-2222}"
KEY="${KEY:-}"
PKG=io.reclmr.pusher
[ -f "$APK" ] || { echo "Build it first: ./scripts/build_pusher.sh" >&2; exit 1; }

SSH_OPTS=(${PORT:+-p "$PORT"} ${KEY:+-i "$KEY"})
SCP_OPTS=(${PORT:+-P "$PORT"} ${KEY:+-i "$KEY"})

# Exempt the app from Doze/standby so Android does not hold its jobs back for hours (it still does nothing unless a job fires). NO_IDLE_WHITELIST=1 skips it.
IDLE="cmd deviceidle whitelist +$PKG"
[ "${NO_IDLE_WHITELIST:-}" = 1 ] && IDLE="true"

scp "${SCP_OPTS[@]}" "$APK" "$PHONE:/data/local/tmp/reclmr-pusher.apk"
ssh "${SSH_OPTS[@]}" "$PHONE" "
  pm install -r /data/local/tmp/reclmr-pusher.apk &&
  appops set $PKG MANAGE_EXTERNAL_STORAGE allow &&
  { pm grant $PKG android.permission.POST_NOTIFICATIONS 2>/dev/null || true; } &&
  $IDLE &&
  am start -n $PKG/.MainActivity &&
  rm -f /data/local/tmp/reclmr-pusher.apk
"
[ "$IDLE" != true ] && echo "Doze/standby exemption added; undo with: cmd deviceidle whitelist -$PKG (run on the phone)"
echo "Installed. The app arms its jobs when opened (just done); after that Android runs them on its own, reboots included."
