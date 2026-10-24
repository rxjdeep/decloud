#!/usr/bin/env bash
# Build one of the two Android apps inside a throwaway Docker container: nothing is installed on this machine.
#   ./scripts/build_android.sh shell    android/shell:  DeCloud itself, a WebView on your server with lock-screen music controls  -> decloud.apk
#   ./scripts/build_android.sh pusher   android/pusher: DeCloud Backup, pushes new photos and files to the server             -> decloud-pusher.apk
# They are separate apps with separate package names; installing one never touches the other.
#
#   DECLOUD_BACKUP_URL    public address of this DeCloud, e.g. https://box.example.ts.net   (.env)  both apps
#   DECLOUD_BACKUP_TOKEN  the same token the server uses (24+ characters)                    (.env)  backup app only
#
# The URL (and for the backup app the token) is compiled in, so keep the APKs private.
# Signing key: ~/.decloud-android/ (created on first run; keep it, an update must be signed with the same key).
set -euo pipefail
cd "$(dirname "$0")/.."

MODULE="${1:-pusher}"
case "$MODULE" in
  pusher) PKGDIR=io/decloud/pusher; APKNAME=decloud-pusher.apk; NEED_TOKEN=1 ;;
  shell)  PKGDIR=io/decloud/app;    APKNAME=decloud.apk;        NEED_TOKEN=0 ;;
  *) echo "usage: $0 [pusher|shell]" >&2; exit 1 ;;
esac
[ -d "android/$MODULE" ] || { echo "android/$MODULE not found" >&2; exit 1; }

envval() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
URL="${DECLOUD_BACKUP_URL:-$(envval DECLOUD_BACKUP_URL)}"
TOKEN="${DECLOUD_BACKUP_TOKEN:-$(envval DECLOUD_BACKUP_TOKEN)}"
[ -n "$URL" ] || { echo "Set DECLOUD_BACKUP_URL (the public address of this DeCloud) in .env first." >&2; exit 1; }
[ "$NEED_TOKEN" = 0 ] || [ "${#TOKEN}" -ge 24 ] || { echo "The backup app also needs a 24+ character DECLOUD_BACKUP_TOKEN in .env." >&2; exit 1; }
case "$URL" in https://*) ;; *) echo "DECLOUD_BACKUP_URL must start with https://" >&2; exit 1;; esac
URL="${URL%/}"

KEYS="${DECLOUD_ANDROID_KEYS:-$HOME/.decloud-android}"
mkdir -p "$KEYS"; chmod 700 "$KEYS"
[ -f "$KEYS/pass" ] || { umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=\n' > "$KEYS/pass"; }

OUT="$PWD/android/$MODULE/build"
mkdir -p "$OUT"
GEN="$(mktemp -d)"; trap 'rm -rf "$GEN"' EXIT
mkdir -p "$GEN/$PKGDIR"
( umask 077
  PKG="${PKGDIR//\//.}"
  if [ "$NEED_TOKEN" = 1 ]; then
    printf 'package %s;\nfinal class Config { static final String BASE_URL = "%s"; static final String TOKEN = "%s"; }\n' "$PKG" "$URL" "$TOKEN" > "$GEN/$PKGDIR/Config.java"
  else
    printf 'package %s;\nfinal class Config { static final String BASE_URL = "%s"; }\n' "$PKG" "$URL" > "$GEN/$PKGDIR/Config.java"
  fi
)
chmod -R a+rX "$GEN"

docker run --rm \
  -v decloud-android-sdk:/sdk \
  -v "$PWD/android/$MODULE:/src:ro" \
  -v "$GEN:/gen:ro" \
  -v "$KEYS:/keys" \
  -v "$OUT:/out" \
  -e APKNAME="$APKNAME" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  eclipse-temurin:17-jdk bash -euo pipefail -c '
SDKM=/sdk/cmdline-tools/latest/bin/sdkmanager
if [ ! -x "$SDKM" ]; then
  echo ">> fetching the Android command-line tools"
  cd /tmp && curl -fsSLo cl.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
  jar xf cl.zip && mkdir -p /sdk/cmdline-tools && rm -rf /sdk/cmdline-tools/latest && mv cmdline-tools /sdk/cmdline-tools/latest
  chmod +x /sdk/cmdline-tools/latest/bin/* && rm cl.zip
fi
if [ ! -d /sdk/platforms/android-34 ] || [ ! -d /sdk/build-tools/34.0.0 ]; then
  echo ">> installing platform 34 and build-tools 34.0.0 (accepts the standard Android SDK licence inside the container)"
  yes | "$SDKM" --sdk_root=/sdk --licenses >/dev/null || true
  "$SDKM" --sdk_root=/sdk "platforms;android-34" "build-tools;34.0.0" >/dev/null
fi
JAR=/sdk/platforms/android-34/android.jar
BT=/sdk/build-tools/34.0.0
W=$(mktemp -d)

echo ">> compiling"
mkdir -p $W/classes
javac -Xlint:-options --release 8 -classpath $JAR -d $W/classes $(find /src/src /gen -name "*.java")
$BT/d8 --release --min-api 30 --lib $JAR --output $W $(find $W/classes -name "*.class")

echo ">> packaging"
$BT/aapt2 compile --dir /src/res -o $W/res.zip
$BT/aapt2 link -o $W/base.apk -I $JAR --manifest /src/AndroidManifest.xml --min-sdk-version 30 --target-sdk-version 34 --version-code 3 --version-name 0.3 $W/res.zip
(cd $W && $BT/aapt add base.apk classes.dex >/dev/null)
$BT/zipalign -f -p 4 $W/base.apk $W/aligned.apk

if [ ! -f /keys/pusher.jks ]; then
  echo ">> creating the signing key (kept in ~/.decloud-android)"
  keytool -genkeypair -keystore /keys/pusher.jks -storepass:file /keys/pass -keypass:file /keys/pass \
    -alias pusher -keyalg RSA -keysize 3072 -validity 36500 -dname "CN=DeCloud pusher" >/dev/null
fi
$BT/apksigner sign --ks /keys/pusher.jks --ks-pass file:/keys/pass --out /out/$APKNAME $W/aligned.apk
$BT/apksigner verify /out/$APKNAME
chown "$HOST_UID:$HOST_GID" /out/$APKNAME /keys/pusher.jks /keys/pass /keys 2>/dev/null || true
chmod 600 /out/$APKNAME /keys/pusher.jks /keys/pass
'
echo "Built: $OUT/$APKNAME ($(du -h "$OUT/$APKNAME" | cut -f1))"
